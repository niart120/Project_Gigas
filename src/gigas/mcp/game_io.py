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

from gigas.game_io.input.config import PROTOCOLS
from gigas.game_io.input.models import InvalidSequenceError, parse_sequence
from gigas.game_io.observation import ObservationError
from gigas.services.input import OperationResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.types import ASGIApp, Receive, Scope, Send

    from gigas.game_io.input.models import InputStep
    from gigas.game_io.observation import Observation


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
    """

    def __init__(
        self,
        observe: "Callable[[], Awaitable[Observation]]",
        act: ActCall,
    ) -> None:
        """Prepare a connection-specific name and token without opening a listener."""
        self._observe_call = observe
        self._act_call = act
        self._name = f"gigas_{uuid4().hex}"
        self._authorization = f"Bearer {secrets.token_urlsafe(32)}"
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
        """Return the latest game image and its observation identity."""
        try:
            observation = await self._observe_call()
        except ObservationError as error:
            return _error_result({"code": error.code, "message": str(error)})
        return _observation_result(observation)

    @staticmethod
    def _input_description() -> str:
        buttons = sorted({button for protocol in PROTOCOLS for button in protocol.buttons})
        return (
            "Execute a finite nonempty sequence and return the latest cached game image with "
            "operation_id and observation_id. The image may predate the input. "
            "Each step is {kind: 'press' or 'wait', duration_ms: positive integer}. "
            "press holds a complete state then releases it; buttons is an array of names. "
            "Do not repeat buttons or combine opposite directions. "
            "left_stick and right_stick are {x: -100..100, y: -100..100}; "
            "right/down are positive. Omitted inputs are released or centered. "
            "touch: {x: 0..319, y: 0..239}, independent of observation image coordinates. "
            f"Known buttons across supported devices: {', '.join(buttons)}. "
            "Available inputs depend on the connected device; an unsupported input rejects "
            "the entire sequence before sending any input. "
            "press requires input; wait accepts only kind and duration_ms. "
            "The whole sequence is validated before sending. "
            f"The request deadline is {TOOL_TIMEOUT_SECONDS} seconds. "
            "An error or timeout may follow partial execution; never automatically resend "
            "the sequence. Observe again before deciding what to do. "
        )

    async def _act_and_observe(self, sequence: list[object]) -> CallToolResult:
        """Validate input, delegate admission, and format the composed outcome."""
        observation: Observation | ObservationError | None = None
        try:
            steps = parse_sequence(sequence)
        except InvalidSequenceError as error:
            result = OperationResult("INVALID_SEQUENCE", message=str(error))
        else:
            try:
                async with asyncio.timeout(TOOL_TIMEOUT_SECONDS):
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
    metadata: dict[str, object] = {
        "observation_id": observation.observation_id,
        "captured_at": observation.captured_at.isoformat().replace("+00:00", "Z"),
        "source_frame": asdict(observation.source_frame),
        "image": {
            "mime_type": observation.mime_type,
            "width": observation.width,
            "height": observation.height,
        },
    }
    if operation_id is not None:
        metadata["operation_id"] = operation_id
    return CallToolResult(
        content=[
            ImageContent(
                type="image",
                data=base64.b64encode(observation.png_bytes).decode("ascii"),
                mime_type=observation.mime_type,
            ),
            TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)),
        ]
    )


def _error_result(metadata: dict[str, object]) -> CallToolResult:
    return CallToolResult(
        is_error=True,
        content=[TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False))],
    )
