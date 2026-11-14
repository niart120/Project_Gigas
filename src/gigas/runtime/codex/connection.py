"""Codex authentication, connection lifecycle and RPC event delivery."""

import asyncio
from importlib import resources
from typing import TYPE_CHECKING

from gigas.runtime.codex._account_session import AccountSession
from gigas.runtime.codex._model_catalog import ModelCatalog
from gigas.runtime.codex._windows_sandbox import WindowsSandbox
from gigas.runtime.codex.models import (
    AccountInfo,
    LoginEvent,
    ModelSelection,
    SandboxEvent,
    SessionStatus,
    UsageEvent,
)
from gigas.runtime.codex.transport import (
    RpcNotification,
    ServerRequest,
    TransportClosed,
    TransportError,
)
from gigas.runtime.codex.transport import (
    RpcPeer as RpcPeer,  # noqa: PLC0414 -- Public transport boundary.
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from gigas.runtime.codex.models import ConnectionEvent, ConnectionOptions, ConnectionPhase
    from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcId


type RuntimeEvent = ConnectionEvent | RpcNotification | ServerRequest


class CodexConnectionError(RuntimeError):
    """A Codex operation failed with a display-safe application-owned reason.

    Args:
        message: Application-owned reason safe to display.
        failure_id: Connection-local identity of an already emitted failure status,
            or None when only the rejected operation reports this failure.
    """

    def __init__(self, message: str, *, failure_id: int | None = None) -> None:
        """Retain the optional identity without changing the exception message."""
        super().__init__(message)
        self.failure_id = failure_id


class AnswerDeliveryError(CodexConnectionError):
    """An answer write failed without confirming whether the peer received it."""


class MessageDeliveryError(CodexConnectionError):
    """A submitted message has no confirmed acceptance; never retry it automatically."""


def read_play_instructions() -> str:
    """Read the packaged UTF-8 play instructions independently of the working directory.

    Raises:
        OSError: The packaged resource cannot be read.
        UnicodeError: The resource is not UTF-8.
    """
    return resources.files("gigas.prompts").joinpath("play.md").read_text(encoding="utf-8")


class CodexConnection:
    """Own one connection without conversation or device execution state.

    All methods and callbacks run on the same asyncio loop. Conversation notifications
    and server requests pass through unchanged; their consumer owns thread filtering,
    question handling and execution decisions. Authentication notifications remain here.

    Args:
        peer: Single RPC transport, including its process lifetime.
        options: Runtime directory and requested authentication/model settings.
        emit: Fast synchronous receiver for connection facts and conversation RPC events.
            It must not await conversation operations or connection shutdown.
    """

    def __init__(
        self,
        peer: RpcPeer,
        options: "ConnectionOptions",
        emit: "Callable[[RuntimeEvent], None]",
    ) -> None:
        """Retain dependencies without starting I/O."""
        self._peer = peer
        self._options = options
        self._emit = emit
        self._pump: asyncio.Task[None] | None = None
        self._opened = False
        self._opening = False
        self._closed = False
        self._account = AccountSession(peer, self._runtime_fact)
        self._catalog = ModelCatalog(peer, self._runtime_fact)
        self._sandbox = WindowsSandbox(peer, self._runtime_fact)
        self._snapshot = SessionStatus("connecting")
        self._operations: set[asyncio.Task[object]] = set()
        self._cleanup: asyncio.Task[None] | None = None
        self._failure_serial = 0

    @property
    def status(self) -> SessionStatus:
        """Latest connection status, independent of conversation activity."""
        return self._snapshot

    @property
    def selection(self) -> ModelSelection:
        """Catalog and confirmed settings for future turns."""
        return self._catalog.snapshot

    @property
    def ready(self) -> bool:
        """Whether authentication and model selection permit execution preparation.

        Sandbox readiness is checked separately by require_ready. Usage information
        is display data; the server decides whether a submitted request is allowed.
        """
        return (
            self._opened
            and not self._closed
            and self._account.authenticated
            and bool(self.selection.model and self.selection.effort)
        )

    async def open(self) -> None:
        """Initialize authentication and model choices without starting a thread.

        Missing authentication and invalid model defaults preserve the connection
        for explicit login or selection. Startup failures close its transport.

        Raises:
            CodexConnectionError: Authentication mode, startup or protocol validation failed.
        """
        await self._operate(self._open())

    async def _open(self) -> None:
        if self._closed or self._opened or self._opening:
            raise CodexConnectionError("接続を開始できない状態")
        self._opening = True
        self._publish_status("connecting")
        try:
            self._validate_options()
            await self._peer.start()
            await self._peer.request(
                "initialize",
                {
                    "clientInfo": {"name": "gigas", "title": "GIGAS", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            await self._peer.notify("initialized")
            self._pump = asyncio.create_task(self._consume_events(), name="gigas-codex-events")
            await self._account.read_account()
            selection_detail = await self._read_models()
            if self._closed:
                raise asyncio.CancelledError
            self._opened = True
            self._publish_status(
                "ready" if self._account.authenticated else "login_required", selection_detail
            )
            self._refresh_usage()
            self._sandbox.start_read()
        except (TransportError, CodexConnectionError, ValueError) as error:
            reason = (
                str(error)
                if isinstance(error, CodexConnectionError | ValueError)
                else "Codex接続に失敗"
            )
            await self.close()
            failure_id = self._failure("error", reason)
            raise CodexConnectionError(reason, failure_id=failure_id) from None
        finally:
            self._opening = False

    async def login(self) -> None:
        """Start explicit ChatGPT login without accessing credential files.

        Raises:
            CodexConnectionError: Login is unavailable, already pending or failed.
        """
        await self._operate(self._login())

    async def _login(self) -> None:
        self._require_open()
        if not self._account.can_login:
            raise CodexConnectionError("ログインを開始できない状態")
        self._publish_status("authenticating")
        try:
            await self._complete_login(await self._account.login())
        except (TransportError, CodexConnectionError, ValueError):
            failure_id = self._failure("login_required", "ログイン開始に失敗")
            raise CodexConnectionError("ログイン開始に失敗", failure_id=failure_id) from None

    async def setup_windows_sandbox(self) -> None:
        """Prepare the elevated sandbox and confirm its public readiness.

        The conversation owner decides whether this operation is appropriate while
        executing. Closing ends local waiting, without claiming OS setup cancellation.

        Raises:
            CodexConnectionError: Setup is unavailable, rejected or failed.
        """
        await self._operate(self._setup_windows_sandbox())

    async def _setup_windows_sandbox(self) -> None:
        self._require_open()
        try:
            await self._sandbox.setup()
        except ValueError as error:
            raise CodexConnectionError(str(error)) from error

    async def require_ready(self) -> None:
        """Check authentication, model selection and confirmed sandbox readiness.

        Cancellation of the caller does not close the connection or cancel the
        shared readiness read. The conversation owns cancellation of preparation.

        Raises:
            CodexConnectionError: Authentication, model selection or sandbox is unavailable.
        """
        if not self.ready:
            raise CodexConnectionError("認証とモデルの選択が必要です")
        try:
            await self._sandbox.require_ready()
        except ValueError as error:
            raise CodexConnectionError(str(error)) from error
        self._require_open()

    def propose_model(self, model: str, effort: str) -> ModelSelection:
        """Validate settings without changing the confirmed selection.

        Raises:
            CodexConnectionError: Authentication or the proposed selection is invalid.
        """
        self._require_open()
        if not self._account.authenticated:
            raise CodexConnectionError("モデル・推論レベルを選択できない状態")
        try:
            return self._catalog.propose(model, effort)
        except ValueError as error:
            raise CodexConnectionError(str(error)) from error

    def commit_model(self, selection: ModelSelection) -> None:
        """Publish validated settings after the conversation applies required RPCs."""
        self._require_open()
        self._catalog.commit(selection)
        self._publish_status(self.status.phase)

    async def request(self, method: str, params: "JsonObject | None") -> "JsonValue":
        """Send one RPC without retries or conversation state changes.

        Raises:
            CodexConnectionError: The connection is not open.
            TransportError: The request was rejected or its result is unknown.
        """
        self._require_open()
        return await self._peer.request(method, params)

    async def respond(self, request_id: "RpcId", result: "JsonValue") -> None:
        """Write a response under the original server request ID.

        Raises:
            CodexConnectionError: The connection is not open.
            TransportError: Response delivery could not be confirmed.
        """
        self._require_open()
        await self._peer.respond(request_id, result)

    async def reject(self, request_id: "RpcId", code: int, message: str) -> None:
        """Reject a server request selected by the conversation's request handler."""
        self._require_open()
        await self._peer.reject(request_id, code, message)

    async def close(self) -> None:
        """Close authentication work, event reception and the owned RPC transport.

        Concurrent callers share cleanup. Cancellation of a waiting caller does not
        cancel it. Conversation sends, answers, input and archives belong to the
        service's enclosing shutdown; this method never calls conversation stop.
        """
        await asyncio.shield(self._begin_close())

    def _begin_close(self) -> asyncio.Task[None]:
        if self._cleanup is None:
            self._closed = True
            self._account.cancel_pending()
            self._sandbox.cancel_pending()
            self._cleanup = asyncio.create_task(
                self._shutdown(asyncio.current_task()), name="gigas-codex-close"
            )
        return self._cleanup

    async def _shutdown(self, origin: asyncio.Task[object] | None) -> None:
        pending = [task for task in self._operations if task is not origin]
        if self._pump is not None and self._pump is not origin:
            pending.append(self._pump)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        try:
            await self._account.close()
            await self._sandbox.close()
        finally:
            await self._peer.close()
        self._publish_status("closed")

    async def _operate[T](self, operation: "Coroutine[object, object, T]") -> T:
        task = asyncio.create_task(operation, name="gigas-codex-connection-operation")
        self._operations.add(task)
        try:
            return await task
        except asyncio.CancelledError:
            if not self._closed:
                await self.close()
            raise
        finally:
            self._operations.discard(task)

    def _require_open(self) -> None:
        if not self._opened or self._closed:
            raise CodexConnectionError("Codexに接続されていない")

    def _validate_options(self) -> None:
        if self._options.auth_method != "chatgpt":
            raise CodexConnectionError("APIキー認証は未対応")
        if not self._options.cwd.is_absolute() or not self._options.cwd.is_dir():
            raise CodexConnectionError("作業フォルダーが無効")

    def _runtime_fact(
        self, event: AccountInfo | LoginEvent | UsageEvent | ModelSelection | SandboxEvent
    ) -> None:
        if not self._closed:
            self._emit(event)

    def _failure(self, phase: "ConnectionPhase", detail: str) -> int:
        self._failure_serial += 1
        self._publish_status(phase, detail, self._failure_serial)
        return self._failure_serial

    def _publish_status(
        self, phase: "ConnectionPhase", detail: str = "", failure_id: int | None = None
    ) -> None:
        selection = self.selection
        self._snapshot = SessionStatus(
            phase,
            selection.model,
            selection.effort,
            self._account.authenticated,
            detail,
            failure_id,
        )
        self._emit(self._snapshot)

    def _refresh_usage(self) -> None:
        if self._opened and not self._closed:
            self._account.refresh_usage()

    async def _read_models(self) -> str:
        try:
            return await self._catalog.load(self._options.model, self._options.effort)
        except ValueError as error:
            raise CodexConnectionError(str(error)) from error

    async def _consume_events(self) -> None:
        try:
            while not self._closed:
                event = await self._peer.next_event()
                if isinstance(event, TransportClosed):
                    self._begin_close()
                    self._failure("error", "Codexとの接続が終了")
                    return
                if isinstance(event, RpcNotification):
                    params = event.params or {}
                    if event.method == "windowsSandbox/setupCompleted":
                        self._sandbox.accept_completion(params)
                        continue
                    if event.method == "account/login/completed":
                        task = asyncio.create_task(self._login_completed(params))
                        self._operations.add(task)
                        task.add_done_callback(self._operations.discard)
                        continue
                    if event.method == "account/rateLimits/updated":
                        self._refresh_usage()
                        continue
                self._emit(event)
        except (TransportError, CodexConnectionError, ValueError):
            self._begin_close()
            self._failure("error", "Codexの通知処理に失敗")

    async def _login_completed(self, params: "JsonObject") -> None:
        try:
            outcome = await self._account.accept_login_completion(params)
            await self._complete_login(outcome)
        except (TransportError, CodexConnectionError, ValueError):
            if not self._closed:
                self._failure("login_required", "ログイン完了を確認できない")

    async def _complete_login(self, outcome: bool | None) -> None:
        if self._closed or outcome is None:
            return
        if not outcome:
            self._failure("login_required", "ログインに失敗")
            return
        detail = await self._read_models()
        if not self._closed:
            self._publish_status(
                "ready" if self._account.authenticated else "login_required", detail
            )
            self._refresh_usage()
