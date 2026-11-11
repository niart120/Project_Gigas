"""Single-reader stdio transport for the Codex app-server protocol."""

import asyncio
import json
import math
import os
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]
type RpcId = str | int

MAX_FRAME_BYTES = 1024 * 1024


class TransportError(RuntimeError):
    """A connection operation failed without exposing protocol payloads."""


class RpcError(TransportError):
    """The peer rejected a request with a numeric protocol error.

    Attributes:
        code: Numeric error code returned by the peer.
        method: Locally supplied method associated with the rejected request.
    """

    def __init__(self, code: int, method: str) -> None:
        """Store the error code and local method without the peer's message."""
        self.code = code
        self.method = method
        super().__init__(f"RPC {method} failed with code {code}")


@dataclass(frozen=True, slots=True)
class RpcNotification:
    """A notification received from the peer.

    Attributes:
        method: Protocol method name.
        params: Object parameters or None when omitted or null.
    """

    method: str
    params: JsonObject | None


@dataclass(frozen=True, slots=True)
class ServerRequest:
    """A peer request awaiting a reply from the connection owner.

    Attributes:
        id: Original string or integer request ID; booleans are rejected.
        method: Protocol method name.
        params: Object parameters or None when omitted or null.
    """

    id: RpcId
    method: str
    params: JsonObject | None


@dataclass(frozen=True, slots=True)
class TransportClosed:
    """A terminal connection event.

    This event closes message routing. Only a successful awaited ``close()``
    confirms that the owned process has exited.

    Attributes:
        reason: Diagnostic category without payloads or stderr contents.
    """

    reason: str


type TransportEvent = RpcNotification | ServerRequest | TransportClosed


def _json_value(value: object) -> JsonValue:
    if isinstance(value, float) and not math.isfinite(value):
        message = "Non-finite JSON number"
        raise TransportError(message)
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _json_value(item) for key, item in value.items()}
    message = "Invalid JSON value"
    raise TransportError(message)


def _object(value: JsonValue) -> JsonObject:
    if isinstance(value, dict):
        return value
    message = "Expected a JSON object"
    raise TransportError(message)


def _request_id(value: JsonValue) -> RpcId:
    if isinstance(value, str | int) and not isinstance(value, bool):
        return value
    message = "Invalid RPC request ID"
    raise TransportError(message)


def _decode_frame(raw: bytes) -> JsonObject:
    if not raw.endswith(b"\n") or len(raw) > MAX_FRAME_BYTES:
        message = "Invalid RPC frame"
        raise TransportError(message)
    return _object(_json_value(json.loads(raw.decode("utf-8"))))


class StdioTransport:
    """Own one app-server process and route its newline-delimited JSON frames.

    All methods must run on one asyncio event loop. Sent and received frames,
    including the trailing newline, are limited to 1 MiB. This transport does
    not interpret or approve server requests.

    Args:
        command: Executable and arguments, passed directly without a shell.
        cwd: Working directory for the child process.
        env: Environment overrides merged with the current process environment.
        request_timeout: Maximum seconds for an individual request.
        close_timeout: Maximum seconds to wait before escalating termination.
    """

    def __init__(
        self,
        command: "Sequence[str]",
        cwd: "Path",
        env: "Mapping[str, str] | None" = None,
        request_timeout: float = 30,
        close_timeout: float = 2,
    ) -> None:
        """Store launch settings without starting a process."""
        self._command = tuple(command)
        self._cwd = cwd
        self._env = dict(env) if env is not None else {}
        self._request_timeout = request_timeout
        self._close_timeout = close_timeout
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._starting: asyncio.Task[None] | None = None
        self._closing: asyncio.Task[None] | None = None
        self._writer_lock = asyncio.Lock()
        self._events: asyncio.Queue[TransportEvent] = asyncio.Queue()
        self._pending: dict[str, tuple[str, asyncio.Future[JsonValue]]] = {}
        self._counter = 0
        self._closed: TransportClosed | None = None

    async def start(self) -> None:
        """Start one child and its readers, sharing concurrent startup calls.

        Cancelling startup waits for the created process to be cleaned up. A
        transport that failed or was closed cannot be restarted.

        Raises:
            TransportError: Startup failed or the transport is already closed.
        """
        self._check_open()
        if self._starting is None:
            self._starting = asyncio.create_task(self._start_process(), name="gigas-codex-start")
        try:
            await asyncio.shield(self._starting)
            self._check_open()
        except (asyncio.CancelledError, TransportError):
            await self.close()
            raise

    async def _start_process(self) -> None:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                cwd=self._cwd,
                env=os.environ | self._env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_FRAME_BYTES,
                creationflags=flags,
            )
        except (OSError, ValueError, TypeError):
            self._finish("Process start failed")
            message = "Process start failed"
            raise TransportError(message) from None
        self._reader = asyncio.create_task(self._read_stdout(), name="gigas-codex-stdout")
        self._stderr = asyncio.create_task(self._discard_stderr(), name="gigas-codex-stderr")

    async def request(self, method: str, params: JsonObject | None) -> JsonValue:
        """Send a request and return the result corresponding to its ID.

        Args:
            method: Protocol method name.
            params: Object parameters, or None to send JSON null.

        Returns:
            The peer's JSON result.

        Raises:
            TransportError: The connection failed, the frame is invalid, or the
                request exceeded its deadline. A timed-out request may have
                reached the peer and is never resent automatically.
            RpcError: The peer returned an error response.
        """
        self._counter += 1
        request_id = f"gigas-{self._counter}"
        future: asyncio.Future[JsonValue] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (method, future)
        try:
            async with asyncio.timeout(self._request_timeout):
                await self._write({"id": request_id, "method": method, "params": params})
                return await future
        except TimeoutError:
            message = "RPC request timed out"
            raise TransportError(message) from None
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def notify(self, method: str, params: JsonObject | None = None) -> None:
        """Send a notification without expecting a response.

        Args:
            method: Protocol method name.
            params: Object parameters or None to send JSON null.

        Raises:
            TransportError: The connection failed or the frame is invalid.
        """
        await self._write({"method": method, "params": params})

    async def respond(self, request_id: RpcId, result: JsonValue) -> None:
        """Send a result after the owner has validated request liveness.

        Args:
            request_id: Original server request ID; booleans are invalid.
            result: JSON result appropriate to the server request's method.

        Raises:
            TransportError: The connection failed or the frame is invalid.

        The connection owner handles duplicate replies and expired requests.
        """
        await self._write({"id": _request_id(request_id), "result": result})

    async def reject(self, request_id: RpcId, code: int, message: str) -> None:
        """Send a protocol error without interpreting its cancellation semantics.

        Args:
            request_id: Original server request ID; booleans are invalid.
            code: Protocol error code selected by the connection owner.
            message: Diagnostic to transmit to the peer.

        Raises:
            TransportError: The connection failed or the frame is invalid.
        """
        await self._write(
            {"id": _request_id(request_id), "error": {"code": code, "message": message}}
        )

    async def next_event(self) -> TransportEvent:
        """Return the next event, repeating the terminal event after closure.

        Repeating the terminal event releases concurrent and future consumers.
        Previously received events remain ordered before the terminal event.
        """
        event = await self._events.get()
        if isinstance(event, TransportClosed):
            self._events.put_nowait(event)
        return event

    async def close(self) -> None:
        """Wait for termination of the owned child and release reader tasks.

        Concurrent calls share one cleanup. Cancellation of a caller does not
        cancel cleanup; await close again before stopping the event loop. This
        method confirms the owned child's exit, not arbitrary descendants or
        the completion of files written by tools.

        Raises:
            TransportError: The operating system could not terminate the child.
        """
        if self._closing is None:
            self._finish("Transport closed")
            self._closing = asyncio.create_task(self._close_process(), name="gigas-codex-close")
        await asyncio.shield(self._closing)

    async def _close_process(self) -> None:
        if self._starting is not None:
            with suppress(TransportError):
                await asyncio.shield(self._starting)
        process = self._process
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        tasks = [task for task in (self._reader, self._stderr) if task is not None]
        waiter = asyncio.create_task(process.wait(), name="gigas-codex-wait")
        try:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
            try:
                await asyncio.wait_for(asyncio.shield(waiter), timeout=self._close_timeout)
            except TimeoutError:
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.kill()
                await waiter
            if process.stdin is not None:
                with suppress(ConnectionError, OSError):
                    await process.stdin.wait_closed()
        except OSError:
            message = "Process shutdown failed"
            raise TransportError(message) from None
        finally:
            for task in (*tasks, waiter):
                task.cancel()
            await asyncio.gather(*tasks, waiter, return_exceptions=True)

    async def _write(self, frame: JsonObject) -> None:
        process = self._process
        if process is None or process.stdin is None:
            message = "Transport is not connected"
            raise TransportError(message)
        try:
            data = (
                json.dumps(_json_value(frame), ensure_ascii=False, allow_nan=False) + "\n"
            ).encode("utf-8")
            if len(data) > MAX_FRAME_BYTES:
                message = "Outgoing RPC frame exceeds 1 MiB"
                raise TransportError(message)
        except (ValueError, RecursionError):
            message = "Invalid outgoing RPC frame"
            raise TransportError(message) from None
        async with self._writer_lock:
            self._check_open()
            try:
                process.stdin.write(data)
                await process.stdin.drain()
            except (ConnectionError, OSError):
                self._finish("Write failed")
                message = "Write failed"
                raise TransportError(message) from None

    def _check_open(self) -> None:
        if self._closed is not None:
            raise TransportError(self._closed.reason)

    async def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while raw := await process.stdout.readline():
                if self._closed is None:
                    self._route(_decode_frame(raw))
        except (TransportError, ValueError, RecursionError):
            self._finish("Invalid RPC frame")
            await self._discard_stream(process.stdout)
        except (ConnectionError, OSError):
            self._finish("Read failed")
        else:
            self._finish("Peer closed stdout")

    def _route(self, frame: JsonObject) -> None:
        if "method" in frame:
            method = frame["method"]
            if not isinstance(method, str) or not method or "result" in frame or "error" in frame:
                message = "Invalid RPC method"
                raise TransportError(message)
            params = frame.get("params")
            parameters = _object(params) if params is not None else None
            if "id" in frame:
                self._events.put_nowait(ServerRequest(_request_id(frame["id"]), method, parameters))
            else:
                self._events.put_nowait(RpcNotification(method, parameters))
            return
        request_id = _request_id(frame.get("id"))
        if ("result" in frame) == ("error" in frame):
            message = "Invalid RPC response"
            raise TransportError(message)
        pending = self._pending.get(request_id) if isinstance(request_id, str) else None
        if pending is None:
            return
        method, future = pending
        if future.done():
            return
        if "error" in frame:
            error = _object(frame["error"])
            code = error.get("code")
            if not isinstance(code, int) or isinstance(code, bool):
                message = "Invalid RPC error code"
                raise TransportError(message)
            future.set_exception(RpcError(code, method))
        else:
            future.set_result(frame["result"])

    def _finish(self, reason: str) -> None:
        if self._closed is not None:
            return
        self._closed = TransportClosed(reason)
        for _, future in self._pending.values():
            if not future.done():
                future.set_exception(TransportError(reason))
        self._events.put_nowait(self._closed)

    async def _discard_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        await self._discard_stream(process.stderr)

    @staticmethod
    async def _discard_stream(stream: asyncio.StreamReader) -> None:
        try:
            while await stream.read(65536):
                pass
        except (ConnectionError, OSError):
            pass
