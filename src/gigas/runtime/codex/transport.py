"""Single-reader stdio transport for the Codex app-server protocol."""

import asyncio
import json
import math
import os
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

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


class _ProcessProtocol(asyncio.SubprocessProtocol):
    """Separate owned-process exit from pipe lifetimes using public asyncio APIs."""

    def __init__(self) -> None:
        loop = asyncio.get_running_loop()
        self.stdout = asyncio.StreamReader(limit=MAX_FRAME_BYTES)
        self.exited: asyncio.Future[None] = loop.create_future()
        self.closed: asyncio.Future[None] = loop.create_future()
        self._paused = False
        self._stdin_closed = False
        self._draining: asyncio.Future[None] | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        if isinstance(transport, asyncio.SubprocessTransport):
            stdout = transport.get_pipe_transport(1)
            if isinstance(stdout, asyncio.ReadTransport):
                self.stdout.set_transport(stdout)

    def pipe_data_received(self, fd: int, data: bytes) -> None:
        if fd == 1:
            self.stdout.feed_data(data)
        # stderr is intentionally drained here without retaining its contents.

    def pipe_connection_lost(self, fd: int, exc: Exception | None) -> None:
        if fd == 1:
            if exc is None:
                self.stdout.feed_eof()
            else:
                self.stdout.set_exception(exc)
        elif fd == 0:
            self._stdin_closed = True
            self.resume_writing()

    def process_exited(self) -> None:
        if not self.exited.done():
            self.exited.set_result(None)

    @override
    def connection_lost(self, exc: Exception | None) -> None:
        self._stdin_closed = True
        self.resume_writing()
        if not self.closed.done():
            self.closed.set_result(None)

    def pause_writing(self) -> None:
        self._paused = True

    def resume_writing(self) -> None:
        self._paused = False
        if self._draining is not None and not self._draining.done():
            self._draining.set_result(None)

    async def drain(self) -> None:
        if self._paused and not self._stdin_closed:
            self._draining = asyncio.get_running_loop().create_future()
            try:
                await self._draining
            finally:
                self._draining = None
        if self._stdin_closed:
            message = "Process stdin closed"
            raise TransportError(message)


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
        self._process: asyncio.SubprocessTransport | None = None
        self._protocol: _ProcessProtocol | None = None
        self._reader: asyncio.Task[None] | None = None
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
            self._process, self._protocol = await asyncio.get_running_loop().subprocess_exec(
                _ProcessProtocol,
                *self._command,
                cwd=self._cwd,
                env=os.environ | self._env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=flags,
            )
        except (OSError, ValueError, TypeError):
            self._finish("Process start failed")
            message = "Process start failed"
            raise TransportError(message) from None
        self._reader = asyncio.create_task(self._read_stdout(), name="gigas-codex-stdout")

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
        method confirms the owned child's exit and closes this connection's
        pipes even if a descendant inherited them. It does not terminate
        arbitrary descendants or confirm files written by tools.

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
        protocol = self._protocol
        if process is None or protocol is None:
            return
        stdin = process.get_pipe_transport(0)
        if stdin is not None:
            stdin.close()
        try:
            if process.get_returncode() is None:
                with suppress(ProcessLookupError):
                    process.terminate()
            try:
                await asyncio.wait_for(asyncio.shield(protocol.exited), timeout=self._close_timeout)
            except TimeoutError:
                if process.get_returncode() is None:
                    with suppress(ProcessLookupError):
                        process.kill()
                await asyncio.shield(protocol.exited)
        except OSError:
            message = "Process shutdown failed"
            raise TransportError(message) from None
        finally:
            process.close()
            await asyncio.shield(protocol.closed)
            if self._reader is not None:
                self._reader.cancel()
                await asyncio.gather(self._reader, return_exceptions=True)

    async def _write(self, frame: JsonObject) -> None:
        process = self._process
        protocol = self._protocol
        stdin = process.get_pipe_transport(0) if process is not None else None
        if not isinstance(stdin, asyncio.WriteTransport) or protocol is None:
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
                stdin.write(data)
                await protocol.drain()
            except (ConnectionError, OSError, TransportError):
                self._finish("Write failed")
                message = "Write failed"
                raise TransportError(message) from None

    def _check_open(self) -> None:
        if self._closed is not None:
            raise TransportError(self._closed.reason)

    async def _read_stdout(self) -> None:
        protocol = self._protocol
        if protocol is None:
            return
        try:
            while raw := await protocol.stdout.readline():
                if self._closed is None:
                    self._route(_decode_frame(raw))
        except (TransportError, ValueError, RecursionError):
            self._finish("Invalid RPC frame")
            await self._discard_stream(protocol.stdout)
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

    @staticmethod
    async def _discard_stream(stream: asyncio.StreamReader) -> None:
        try:
            while await stream.read(65536):
                pass
        except (ConnectionError, OSError):
            pass
