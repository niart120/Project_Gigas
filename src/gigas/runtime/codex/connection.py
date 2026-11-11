"""Codex account, catalog and conversation behavior over one RPC transport."""

import asyncio
import json
from contextlib import suppress
from importlib import resources
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

from gigas.runtime.codex.models import LoginEvent, SessionStatus, TextEvent
from gigas.runtime.codex.transport import (
    RpcNotification,
    ServerRequest,
    TransportClosed,
    TransportError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.runtime.codex.models import ConnectionEvent, ConnectionOptions, ConnectionPhase
    from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcId, TransportEvent


class CodexConnectionError(RuntimeError):
    """A Codex operation failed with a display-safe application-owned reason."""


class RpcPeer(Protocol):
    """The single asynchronous I/O boundary owned by a Codex connection."""

    async def start(self) -> None:
        """Start the underlying transport."""
        ...

    async def request(self, method: str, params: "JsonObject | None") -> "JsonValue":
        """Send one RPC and return its result without retrying."""
        ...

    async def notify(self, method: str, params: "JsonObject | None" = None) -> None:
        """Send a notification."""
        ...

    async def respond(self, request_id: "RpcId", result: "JsonValue") -> None:
        """Answer a server request using its original ID."""
        ...

    async def reject(self, request_id: "RpcId", code: int, message: str) -> None:
        """Reject an unsupported server request."""
        ...

    async def next_event(self) -> "TransportEvent":
        """Receive the next request, notification or closure."""
        ...

    async def close(self) -> None:
        """Wait for owned transport tasks and the child process to finish."""
        ...


def read_play_instructions() -> str:
    """Read the packaged UTF-8 play instructions independently of the working directory.

    Raises:
        OSError: The packaged resource cannot be read.
        UnicodeError: The resource is not UTF-8.
    """
    return resources.files("gigas.prompts").joinpath("play.md").read_text(encoding="utf-8")


def _object(value: "JsonValue") -> "JsonObject":
    if isinstance(value, dict):
        return value
    message = "Codexの応答形式が不正"
    raise CodexConnectionError(message)


def _text(value: "JsonValue") -> str:
    if isinstance(value, str) and value:
        return value
    message = "Codexの応答形式が不正"
    raise CodexConnectionError(message)


def _login_url(value: "JsonValue") -> str:
    url = _text(value)
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        message = "ログインURLが不正"
        raise CodexConnectionError(message)
    return url


class CodexConnection:
    """Own account validation and one conversation over a single RPC peer.

    All methods and the synchronous event callback run on the same asyncio loop.
    The callback must return promptly. Authentication remains owned by the runtime;
    this class never reads or copies credential files.

    Args:
        peer: Transport implementing the actual RPC boundary.
        options: Working directory and requested authentication/model settings.
        emit: Callback receiving typed application events, never raw RPC frames.
    """

    def __init__(
        self, peer: RpcPeer, options: "ConnectionOptions", emit: "Callable[[ConnectionEvent], None]"
    ) -> None:
        """Store connection dependencies without performing I/O."""
        self._peer = peer
        self._options = options
        self._emit = emit
        self._pump: asyncio.Task[None] | None = None
        self._opened = False
        self._closed = False
        self._authenticated = False
        self._model = ""
        self._effort = ""
        self._phase: ConnectionPhase = "connecting"
        self._thread_id: str | None = None
        self._turn_id: str | None = None
        self._login_id: str | None = None
        self._mcp_config: JsonObject = {}
        self._user_items = 0

    async def open(self) -> None:
        """Initialize, read authentication and validate model settings against the catalog.

        Missing ChatGPT authentication emits login_required without logging in.
        Startup failures release the transport; this connection cannot be reopened.

        Raises:
            CodexConnectionError: Authentication mode, catalog, startup or protocol
                validation failed.
        """
        if self._closed or self._opened:
            message = "接続を開始できない状態"
            raise CodexConnectionError(message)
        self._status("connecting")
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
            await self._read_account()
            config = _object(
                await self._peer.request(
                    "config/read",
                    {
                        "includeLayers": False,
                        "cwd": str(self._options.cwd),
                    },
                )
            )
            servers = _object(_object(config.get("config")).get("mcp_servers", {}))
            for name in servers:
                key = name if name.replace("_", "").isalnum() else json.dumps(name)
                self._mcp_config[f"mcp_servers.{key}.enabled"] = False
            await self._read_models()
            self._opened = True
            self._status("ready" if self._authenticated else "login_required")
        except asyncio.CancelledError:
            await self.close()
            raise
        except (TransportError, CodexConnectionError) as error:
            reason = str(error) if isinstance(error, CodexConnectionError) else "Codex接続に失敗"
            await self.close()
            self._status("error", reason)
            raise CodexConnectionError(reason) from None

    async def login(self) -> None:
        """Begin an explicit ChatGPT login and emit its browser URL.

        The account becomes authenticated only after a matching completion
        notification and a fresh account/read response.

        Raises:
            CodexConnectionError: Login is unavailable, already pending or failed.
        """
        self._require_open()
        if self._authenticated or self._login_id is not None:
            message = "ログインを開始できない状態"
            raise CodexConnectionError(message)
        self._status("authenticating")
        try:
            result = _object(await self._peer.request("account/login/start", {"type": "chatgpt"}))
            self._login_id = _text(result.get("loginId"))
            self._emit(LoginEvent(_login_url(result.get("authUrl"))))
        except (TransportError, CodexConnectionError):
            self._status("login_required", "ログイン開始に失敗")
            message = "ログイン開始に失敗"
            raise CodexConnectionError(message) from None

    async def send(self, prompt: str) -> None:
        """Start one ordinary turn after delivering the packaged developer instructions.

        Args:
            prompt: Nonempty user text. Calls are not retried after unknown acceptance.

        Raises:
            CodexConnectionError: The connection is not ready, instructions fail,
                runtime policy differs, or the request fails.
        """
        self._require_open()
        if not self._authenticated or self._phase not in {"ready", "stopped"} or not prompt.strip():
            message = "ターンを開始できない状態"
            raise CodexConnectionError(message)
        self._status("starting")
        try:
            if self._thread_id is None:
                await self._start_thread()
            result = _object(
                await self._peer.request(
                    "turn/start",
                    {
                        "threadId": self._thread_id,
                        "input": [{"type": "text", "text": prompt}],
                        "model": self._model,
                        "effort": self._effort,
                    },
                )
            )
            self._turn_id = _text(_object(result.get("turn")).get("id"))
            self._user_items += 1
            self._emit(TextEvent(f"user-{self._user_items}", prompt, "user", True))
            self._status("running")
        except (TransportError, CodexConnectionError, OSError, UnicodeError):
            self._status("error", "ターン開始に失敗")
            message = "ターン開始に失敗"
            raise CodexConnectionError(message) from None

    async def close(self) -> None:
        """Release the event consumer and wait for the owned peer to close."""
        if self._closed:
            return
        self._closed = True
        await self._peer.close()
        if self._pump is not None and self._pump is not asyncio.current_task():
            self._pump.cancel()
            with suppress(asyncio.CancelledError):
                await self._pump
        self._status("closed")

    def _require_open(self) -> None:
        if not self._opened or self._closed:
            message = "Codexに接続されていない"
            raise CodexConnectionError(message)

    def _validate_options(self) -> None:
        if self._options.auth_method != "chatgpt":
            message = "APIキー認証は未対応"
            raise CodexConnectionError(message)
        if not self._options.cwd.is_absolute() or not self._options.cwd.is_dir():
            message = "作業フォルダーが無効"
            raise CodexConnectionError(message)

    async def _start_thread(self) -> None:
        instructions = read_play_instructions()
        if not instructions.strip():
            message = "共通指示が空"
            raise CodexConnectionError(message)
        result = _object(
            await self._peer.request(
                "thread/start",
                {
                    "cwd": str(self._options.cwd),
                    "model": self._model,
                    "developerInstructions": instructions,
                    "ephemeral": True,
                    "sandbox": "read-only",
                    "approvalPolicy": "on-request",
                    "approvalsReviewer": "user",
                    "config": self._mcp_config,
                },
            )
        )
        if (
            result.get("approvalPolicy") != "on-request"
            or result.get("approvalsReviewer") != "user"
            or result.get("model") != self._model
            or _object(result.get("sandbox")).get("type") != "readOnly"
        ):
            message = "Codexの実行設定が要求と不一致"
            raise CodexConnectionError(message)
        self._thread_id = _text(_object(result.get("thread")).get("id"))

    def _status(self, phase: "ConnectionPhase", detail: str = "") -> None:
        self._phase = phase
        self._emit(SessionStatus(phase, self._model, self._effort, self._authenticated, detail))

    async def _read_account(self) -> None:
        result = _object(await self._peer.request("account/read", {"refreshToken": False}))
        account = result.get("account")
        self._authenticated = isinstance(account, dict) and account.get("type") == "chatgpt"

    async def _read_models(self) -> None:
        models: list[JsonObject] = []
        cursor: str | None = None
        visited: set[str] = set()
        while True:
            response = _object(await self._peer.request("model/list", {"cursor": cursor}))
            data = response.get("data")
            if not isinstance(data, list):
                message = "モデル一覧が不正"
                raise CodexConnectionError(message)
            models.extend(_object(model) for model in data)
            next_cursor = response.get("nextCursor")
            if next_cursor is None:
                break
            cursor = _text(next_cursor)
            if cursor in visited:
                message = "モデル一覧のページが重複"
                raise CodexConnectionError(message)
            visited.add(cursor)
        selected = next(
            (
                model
                for model in models
                if (
                    model.get("model") == self._options.model
                    if self._options.model
                    else model.get("isDefault") is True
                )
            ),
            None,
        )
        if selected is None:
            message = "指定モデルを利用できない"
            raise CodexConnectionError(message)
        self._model = _text(selected.get("model"))
        self._effort = self._options.effort or _text(selected.get("defaultReasoningEffort"))
        efforts = selected.get("supportedReasoningEfforts")
        if not isinstance(efforts, list) or not any(
            isinstance(option, dict) and option.get("reasoningEffort") == self._effort
            for option in efforts
        ):
            message = "指定エフォートを利用できない"
            raise CodexConnectionError(message)

    async def _consume_events(self) -> None:
        try:
            while not self._closed:
                event = await self._peer.next_event()
                if isinstance(event, TransportClosed):
                    if not self._closed:
                        self._status("error", "Codexとの接続が終了")
                    return
                if isinstance(event, ServerRequest):
                    await self._peer.reject(event.id, -32601, "Unsupported request")
                elif isinstance(event, RpcNotification):
                    await self._notification(event)
        except (TransportError, CodexConnectionError):
            self._status("error", "Codexの通知処理に失敗")

    async def _notification(self, event: RpcNotification) -> None:
        params = event.params or {}
        if event.method == "account/login/completed" and params.get("loginId") == self._login_id:
            self._login_id = None
            if params.get("success") is True:
                await self._read_account()
                await self._read_models()
            self._status("ready" if self._authenticated else "login_required")
