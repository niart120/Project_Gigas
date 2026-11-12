"""Expose completed observations over an authenticated loopback MCP endpoint."""

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

from gigas.game_io.observation import ObservationError

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from gigas.services.observation import ObservationService


class McpServerConfig(TypedDict):
    """MCP endpoint settings for one connection, including a private header.

    These values belong in the runtime's thread config, never in prompts,
    application logs, or project records.
    """

    url: str
    http_headers: dict[str, str]


class ObservationMcpServer:
    """Own one loopback listener while borrowing the application's observation service.

    Start, close, and tool calls run on the existing application service loop.
    The owner must also drain the observation service before closing its writer
    or event loop. Closing HTTP does not cancel or claim completion of saving.

    Args:
        observations: Application-owned latest-image service; its writer is not owned here.
    """

    def __init__(self, observations: "ObservationService") -> None:
        """Prepare a connection-specific name and token without opening a listener."""
        self._observations = observations
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
                self._serve(self._server, listener), name="gigas-observation-http"
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
            observation = await self._observations.observe()
        except ObservationError as error:
            return CallToolResult(
                is_error=True,
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {"code": error.code, "message": str(error)}, ensure_ascii=False
                        ),
                    )
                ],
            )
        metadata = {
            "observation_id": observation.observation_id,
            "captured_at": observation.captured_at.isoformat().replace("+00:00", "Z"),
            "source_frame": asdict(observation.source_frame),
            "image": {
                "mime_type": observation.mime_type,
                "width": observation.width,
                "height": observation.height,
            },
        }
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
