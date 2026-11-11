"""Single-reader stdio transport for the Codex app-server protocol."""

import asyncio
import json
import os
import subprocess
import sys
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

    Attributes:
        reason: Diagnostic category without payloads or stderr contents.
    """

    reason: str


type TransportEvent = RpcNotification | ServerRequest | TransportClosed


def _json_value(value: object) -> JsonValue:
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


class StdioTransport:
    """Own one app-server process and route its newline-delimited JSON frames.

    All methods must run on one asyncio event loop. Incoming frames are limited
    to 1 MiB. This transport does not interpret or approve server requests.

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
        self._writer_lock = asyncio.Lock()
        self._events: asyncio.Queue[TransportEvent] = asyncio.Queue()
        self._pending: dict[str, tuple[str, asyncio.Future[JsonValue]]] = {}
        self._counter = 0

    async def start(self) -> None:
        """Start the child process and its stdout and stderr readers."""
        if self._process is not None:
            return
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
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
            TransportError: The process is not connected.
            RpcError: The peer returned an error response.
        """
        self._counter += 1
        request_id = f"gigas-{self._counter}"
        future: asyncio.Future[JsonValue] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (method, future)
        try:
            await self._write({"id": request_id, "method": method, "params": params})
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: JsonObject | None = None) -> None:
        """Send a notification without expecting a response."""
        await self._write({"method": method, "params": params})

    async def respond(self, request_id: RpcId, result: JsonValue) -> None:
        """Send a result; the connection owner must first validate request liveness."""
        await self._write({"id": _request_id(request_id), "result": result})

    async def reject(self, request_id: RpcId, code: int, message: str) -> None:
        """Send a protocol error for a server request using its original ID."""
        await self._write(
            {"id": _request_id(request_id), "error": {"code": code, "message": message}}
        )

    async def next_event(self) -> TransportEvent:
        """Wait for the next server request, notification, or closure event."""
        return await self._events.get()

    async def close(self) -> None:
        """Stop the owned child and release its reader tasks."""
        process = self._process
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        if process.returncode is None:
            process.terminate()
        await process.wait()
        tasks = [task for task in (self._reader, self._stderr) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _write(self, frame: JsonObject) -> None:
        process = self._process
        if process is None or process.stdin is None:
            message = "Transport is not connected"
            raise TransportError(message)
        data = (json.dumps(frame, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        async with self._writer_lock:
            process.stdin.write(data)
            await process.stdin.drain()

    async def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        while raw := await process.stdout.readline():
            frame = _object(_json_value(json.loads(raw)))
            self._route(frame)

    def _route(self, frame: JsonObject) -> None:
        if "method" in frame:
            method = frame["method"]
            if not isinstance(method, str):
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
            future.set_result(frame.get("result"))

    async def _discard_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        while await process.stderr.read(65536):
            pass
