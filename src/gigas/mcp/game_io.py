"""Expose game input and observations over an authenticated loopback MCP endpoint."""

import asyncio
import base64
import json
import secrets
import socket
from dataclasses import asdict
from typing import TYPE_CHECKING, TypedDict
from uuid import uuid4

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import StrictInt

from gigas.diagnostics import redactor
from gigas.game_io.input.config import PROTOCOLS
from gigas.game_io.input.models import InvalidSequenceError, parse_sequence
from gigas.game_io.observation import ObservationError
from gigas.services.input import OperationResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.types import ASGIApp, Receive, Scope, Send

    from gigas.game_io.input.models import InputStep
    from gigas.game_io.observation import Observation
    from gigas.services._game_io_tools import GameOperations, ToolOutcome


TOOL_TIMEOUT_SECONDS = 300

type ActCall = Callable[
    [tuple[InputStep, ...]],
    Awaitable[tuple[OperationResult, Observation | ObservationError | None]],
]


class McpServerConfig(TypedDict):
    """MCP endpoint settings for one connection, including a private header.

    These values belong in the runtime's thread config, never in prompts,
    application logs, or project records.
    """

    url: str
    http_headers: dict[str, str]


class GameIoMcpServer:
    """Own one loopback listener while borrowing the application's game I/O services.

    Start, close, and tool calls run on the existing application service loop.
    The owner stops input and drains observation saving before closing its writer
    or event loop. Closing HTTP does not own those services or claim saving has finished.

    Args:
        observe: Fetch the latest image from this conversation's resources.
        act: Admit input and compose its outcome with observation or observation failure.
        operations: Resolve the current conversation owner for dispatch and periodic sampling.
    """

    def __init__(
        self,
        observe: "Callable[[], Awaitable[Observation]]",
        act: ActCall,
        *,
        operations: "Callable[[], GameOperations | None] | None" = None,
    ) -> None:
        """Prepare a connection-specific name and token without opening a listener."""
        self._observe_call = observe
        self._act_call = act
        self._operations = operations
        self._name = f"gigas_{uuid4().hex}"
        self._authorization = f"Bearer {secrets.token_urlsafe(32)}"
        redactor.register(self._authorization.removeprefix("Bearer "))
        self._url = ""
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def server_name(self) -> str:
        """Return the unique server key used under the runtime's mcp_servers config."""
        return self._name

    @property
    def url(self) -> str:
        """Return the loopback endpoint after successful start.

        Raises:
            RuntimeError: The listener is unavailable or closing.
        """
        if self._server is None or not self._server.started or self._server.should_exit:
            message = "MCPサーバーは利用できません"
            raise RuntimeError(message)
        return self._url

    @property
    def config(self) -> McpServerConfig:
        """Return transport settings without persisting them.

        The application composes runtime-specific config from these values and
        server_name. The private Authorization header must not be logged or
        shown to the model.

        Raises:
            RuntimeError: The listener is unavailable or closing.
        """
        return {
            "url": self.url,
            "http_headers": {"Authorization": self._authorization},
        }

    async def start(self) -> None:
        """Bind an OS-selected loopback port and wait for SDK startup and HTTP readiness.

        A failed or closed instance is not restarted; create a new instance for
        a new connection so that the old name and token are not reused.

        Raises:
            OSError: The loopback listener could not be opened.
            RuntimeError: Already started, or the SDK/HTTP server failed to start.
            TimeoutError: HTTP readiness was not reached within ten seconds.
        """
        if self._server is not None:
            message = "MCPサーバーは開始済みです"
            raise RuntimeError(message)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", 0))
            listener.setblocking(False)
            host = f"127.0.0.1:{listener.getsockname()[1]}"
            self._url = f"http://{host}/mcp"
            mcp: MCPServer[object] = MCPServer(self._name, log_level="WARNING")
            mcp.tool(
                name="observe",
                structured_output=False,
                annotations=ToolAnnotations(destructive_hint=False, open_world_hint=False),
            )(self._observe)
            mcp.tool(
                name="act_and_observe",
                description=self._input_description(),
                structured_output=False,
                annotations=ToolAnnotations(
                    read_only_hint=False,
                    destructive_hint=True,
                    idempotent_hint=False,
                    open_world_hint=True,
                ),
            )(self._act_and_observe)
            if self._operations is not None:
                mcp.tool(
                    name="dispatch",
                    description="Start input and return dispatch_id for poll or cancel. "
                    "Stops at turn end. A concurrent sequence returns BUSY.\n"
                    + self._steps_description(),
                    structured_output=False,
                )(self._dispatch)
                mcp.tool(
                    name="poll",
                    structured_output=False,
                    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
                )(self._poll)
                mcp.tool(name="cancel", structured_output=False)(self._cancel)
            app = mcp.streamable_http_app(
                json_response=True,
                stateless_http=True,
                transport_security=TransportSecuritySettings(
                    allowed_hosts=[host], allowed_origins=[f"http://{host}"]
                ),
            )
            self._server = uvicorn.Server(
                uvicorn.Config(
                    self._authenticated(app),
                    log_config=None,
                    access_log=False,
                    lifespan="on",
                    ws="none",
                )
            )
            self._task = asyncio.create_task(
                self._serve(self._server, listener), name="gigas-game-io-http"
            )
            await self._wait_started(self._server, self._task)
        except BaseException:
            if self._server is not None:
                self._server.should_exit = True
            if self._task is not None:
                # Keep the SDK lifespan owned through cleanup, even when the
                # caller cancels startup. close() can resume an interrupted wait.
                await asyncio.shield(asyncio.gather(self._task, return_exceptions=True))
                self._task = None
            listener.close()
            raise

    async def _wait_started(self, server: uvicorn.Server, task: asyncio.Task[None]) -> None:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    message = "MCPサーバーが起動前に終了しました"
                    raise RuntimeError(message)
                await asyncio.sleep(0.01)

    async def _serve(self, server: uvicorn.Server, listener: socket.socket) -> None:
        try:
            try:
                await server.serve(sockets=[listener])
            except SystemExit as error:
                # Uvicorn exits on lifespan startup failure; an embedded server
                # must report that failure without exiting the application loop.
                message = "MCPサーバーの起動に失敗しました"
                raise RuntimeError(message) from error
        finally:
            server.should_exit = True
            listener.close()

    def _authenticated(self, app: "ASGIApp") -> "ASGIApp":
        async def handle(scope: "Scope", receive: "Receive", send: "Send") -> None:
            if scope["type"] == "http":
                headers = dict(scope["headers"])
                authorization = headers.get(b"authorization", b"")
                status = None
                if self._server is None or self._server.should_exit:
                    status = 503
                elif not secrets.compare_digest(authorization, self._authorization.encode("ascii")):
                    status = 401
                if status is not None:
                    await send({"type": "http.response.start", "status": status, "headers": []})
                    await send({"type": "http.response.body", "body": b""})
                    return
            await app(scope, receive, send)

        return handle

    async def _observe(self) -> CallToolResult:
        """Return the latest cached game image and its observation_id."""
        try:
            observation = await self._observe_call()
        except ObservationError as error:
            return _error_result({"code": error.code, "message": str(error)})
        return _observation_result(observation)

    @staticmethod
    def _input_description() -> str:
        return (
            "Run the sequence and return the latest cached image, which may predate the input. "
            "Set capture_interval_ms for periodic images (32 maximum including the final image). "
            f"Request deadline: {TOOL_TIMEOUT_SECONDS} seconds.\n"
            + GameIoMcpServer._steps_description()
        )

    @staticmethod
    def _steps_description() -> str:
        buttons = sorted({button for protocol in PROTOCOLS for button in protocol.buttons})
        return (
            "sequence: nonempty array of steps, each with a positive integer duration_ms.\n"
            "- kind='press': hold the specified inputs. Omitted inputs are neutral. "
            "Consecutive presses switch directly: A → A holds A; A → wait → A presses twice.\n"
            "- kind='wait': release all inputs for duration_ms. Takes only kind and duration_ms. "
            "Sequence end also releases all inputs.\n"
            "Each press needs at least one input. buttons is an array of unique names per step; "
            "opposite directions cannot be combined. "
            "left_stick/right_stick: {x: -100..100, y: -100..100} (right/down positive). "
            "touch: {x: 0..319, y: 0..239} in device coordinates.\n"
            f"Button names across devices: {', '.join(buttons)}. "
            "Unsupported inputs reject the whole sequence before execution."
        )

    async def _dispatch(self, sequence: list[object]) -> CallToolResult:
        try:
            steps = parse_sequence(sequence)
        except InvalidSequenceError as error:
            return _error_result({"code": "INVALID_SEQUENCE", "message": str(error)})
        ops = self._operations() if self._operations else None
        return _tool_result(ops.dispatch(steps)) if ops else _unavailable()

    async def _poll(self, dispatch_id: str) -> CallToolResult:
        """Get current progress and screen, or the retained final result."""
        ops = self._operations() if self._operations else None
        if ops is None:
            return _unavailable()
        return await asyncio.to_thread(_tool_result, await ops.query(dispatch_id, cancel=False))

    async def _cancel(self, dispatch_id: str) -> CallToolResult:
        """Stop this dispatched sequence and return its release outcome and latest screen."""
        ops = self._operations() if self._operations else None
        if ops is None:
            return _unavailable()
        return await asyncio.to_thread(_tool_result, await ops.query(dispatch_id, cancel=True))

    async def _act_and_observe(
        self, sequence: list[object], capture_interval_ms: StrictInt | None = None
    ) -> CallToolResult:
        """Validate input, delegate admission, and format the composed outcome."""
        observation: Observation | ObservationError | None = None
        try:
            steps = parse_sequence(sequence)
        except InvalidSequenceError as error:
            result = OperationResult("INVALID_SEQUENCE", message=str(error))
        else:
            try:
                async with asyncio.timeout(TOOL_TIMEOUT_SECONDS):
                    if capture_interval_ms is not None:
                        duration = sum(step.duration_ms for step in steps)
                        minimum = (duration + 31) // 32
                        if capture_interval_ms < minimum:
                            return _error_result(
                                {
                                    "code": "INVALID_INTERVAL",
                                    "min_interval_ms": minimum,
                                    "message": f"撮影間隔には {minimum} ms 以上を指定してください",
                                }
                            )
                        ops = self._operations() if self._operations else None
                        if ops is None:
                            return _unavailable()
                        outcome = await ops.execute(steps, capture_interval_ms)
                        return await asyncio.to_thread(_tool_result, outcome)
                    result, observation = await self._act_call(steps)
            except TimeoutError:
                # InputService owns cancellation and any pending release.
                # No completed outcome is available to this HTTP request.
                return _error_result(
                    {
                        "code": "TOOL_TIMEOUT",
                        "phase": "unknown",
                        "input_sent": "unknown",
                        "neutral": "unconfirmed",
                        "message": (
                            "要求の待機期限に達しました。入力結果と解除完了は未確定です。"
                            "一部が実行された可能性があるため、同じ操作列を再送せず、"
                            "観測して状態を確認してください"
                        ),
                    }
                )
        if observation is not None and not isinstance(observation, ObservationError):
            return _observation_result(observation, result.operation_id)
        metadata: dict[str, object] = {
            "code": result.code,
            "phase": result.phase,
            "completed_steps": result.completed_steps,
            "input_sent": result.input_sent,
            "neutral": result.neutral,
            "message": result.message,
        }
        if result.interrupted:
            metadata["interrupted"] = True
        if result.operation_id is not None:
            metadata["operation_id"] = result.operation_id
        if isinstance(observation, ObservationError):
            metadata.update(code=observation.code, phase="observe", message=str(observation))
        return _error_result(metadata)

    async def close(self) -> None:
        """Reject new requests immediately and wait for HTTP and SDK shutdown.

        Repeated close is harmless. Cancelling a caller leaves the server task
        owned; await close again to finish. The owner drains observation saving
        separately, after HTTP can no longer submit new observations.
        """
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            await asyncio.shield(self._task)


def _observation_result(
    observation: "Observation", operation_id: str | None = None
) -> CallToolResult:
    metadata = _image_metadata(observation)
    if operation_id is not None:
        metadata["operation_id"] = operation_id
    return CallToolResult(
        content=[
            _image_content(observation),
            TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)),
        ]
    )


def _image_metadata(observation: "Observation") -> dict[str, object]:
    return {
        "observation_id": observation.observation_id,
        "captured_at": observation.captured_at.isoformat().replace("+00:00", "Z"),
        "source_frame": asdict(observation.source_frame),
        "image": {
            "mime_type": observation.mime_type,
            "width": observation.width,
            "height": observation.height,
        },
    }


def _image_content(observation: "Observation") -> ImageContent:
    return ImageContent(
        type="image",
        data=base64.b64encode(observation.png_bytes).decode("ascii"),
        mime_type=observation.mime_type,
    )


def _unavailable() -> CallToolResult:
    return _error_result({"code": "INPUT_DISABLED", "message": "操作列の受付が利用できません"})


def _tool_result(outcome: "ToolOutcome") -> CallToolResult:
    metadata = dict(outcome.metadata)
    reply = CallToolResult(content=[], is_error=outcome.error)
    samples = []
    for slot in outcome.samples:
        details = slot.metadata()
        if slot.image:
            reply.content.append(_image_content(slot.image))
            details.update(_image_metadata(slot.image))
        samples.append(details)
    if samples:
        metadata["samples"] = samples
    if outcome.image:
        reply.content.append(_image_content(outcome.image))
        metadata.update(_image_metadata(outcome.image))
    reply.content.append(TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)))
    return reply


def _error_result(metadata: dict[str, object]) -> CallToolResult:
    return CallToolResult(
        is_error=True,
        content=[TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False))],
    )
