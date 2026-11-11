"""Codex account, catalog and conversation behavior over one RPC transport."""

import asyncio
import json
import uuid
from contextlib import suppress
from importlib import resources
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

from gigas.runtime.codex.models import (
    HumanRequest,
    LoginEvent,
    Question,
    QuestionOption,
    RequestResolved,
    SessionStatus,
    TextEvent,
)
from gigas.runtime.codex.transport import (
    RpcNotification,
    ServerRequest,
    TransportClosed,
    TransportError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Mapping

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


def _questions(value: "JsonValue") -> tuple[Question, ...]:
    if not isinstance(value, list) or not value:
        message = "質問の形式が不正"
        raise CodexConnectionError(message)
    questions: list[Question] = []
    for item in value:
        data = _object(item)
        choices = data.get("options")
        options: list[QuestionOption] = []
        if choices is not None:
            if not isinstance(choices, list):
                message = "質問の選択肢が不正"
                raise CodexConnectionError(message)
            for option in choices:
                choice = _object(option)
                label = _text(choice.get("label"))
                description = choice.get("description")
                if not isinstance(description, str):
                    message = "質問の選択肢が不正"
                    raise CodexConnectionError(message)
                options.append(QuestionOption(label, description))
        questions.append(
            Question(
                _text(data.get("id")),
                _text(data.get("header")),
                _text(data.get("question")),
                tuple(options),
                data.get("isOther") is True,
                data.get("isSecret") is True,
            )
        )
    if len({question.id for question in questions}) != len(questions):
        message = "質問IDが重複"
        raise CodexConnectionError(message)
    return tuple(questions)


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
        self._stopping = False
        self._requests: dict[str, HumanRequest] = {}
        self._request_tokens: dict[RpcId, str] = {}
        self._seen_requests: set[RpcId] = set()
        self._file_details: dict[str, str] = {}
        self._operations: set[asyncio.Task[None]] = set()
        self._cleanup: asyncio.Task[None] | None = None
        self._opening = False
        self._sending = False
        self._starting_turn = False
        self._completed_turns: set[str] = set()
        self._interrupted_turns: set[str] = set()
        self._login_starting = False
        self._login_completions: dict[str, JsonObject] = {}

    async def open(self) -> None:
        """Initialize, read authentication and validate model settings against the catalog.

        Missing ChatGPT authentication emits login_required without logging in.
        Startup failures release the transport; this connection cannot be reopened.

        Raises:
            CodexConnectionError: Authentication mode, catalog, startup or protocol
                validation failed.
        """
        await self._operate(self._open())

    async def _open(self) -> None:
        if self._closed or self._opened or self._opening:
            message = "接続を開始できない状態"
            raise CodexConnectionError(message)
        self._opening = True
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
        except (TransportError, CodexConnectionError) as error:
            reason = str(error) if isinstance(error, CodexConnectionError) else "Codex接続に失敗"
            await self.close()
            self._status("error", reason)
            raise CodexConnectionError(reason) from None
        finally:
            self._opening = False

    async def login(self) -> None:
        """Begin an explicit ChatGPT login and emit its browser URL.

        The account becomes authenticated only after a matching completion
        notification and a fresh account/read response.

        Raises:
            CodexConnectionError: Login is unavailable, already pending or failed.
        """
        await self._operate(self._login())

    async def _login(self) -> None:
        self._require_open()
        if self._authenticated or self._login_id is not None or self._login_starting:
            message = "ログインを開始できない状態"
            raise CodexConnectionError(message)
        self._login_starting = True
        self._status("authenticating")
        try:
            result = _object(await self._peer.request("account/login/start", {"type": "chatgpt"}))
            self._login_id = _text(result.get("loginId"))
            completion = self._login_completions.get(self._login_id)
            if completion is not None:
                await self._complete_login(completion)
            else:
                self._emit(LoginEvent(_login_url(result.get("authUrl"))))
        except (TransportError, CodexConnectionError):
            self._status("login_required", "ログイン開始に失敗")
            message = "ログイン開始に失敗"
            raise CodexConnectionError(message) from None
        finally:
            self._login_starting = False
            self._login_completions.clear()

    async def send(self, prompt: str) -> None:
        """Start one ordinary turn after delivering the packaged developer instructions.

        Args:
            prompt: Nonempty user text. Calls are not retried after unknown acceptance.

        Raises:
            CodexConnectionError: The connection is not ready, instructions fail,
                runtime policy differs, or the request fails.
        """
        await self._operate(self._send(prompt))

    async def _send(self, prompt: str) -> None:
        self._require_open()
        if (
            not self._authenticated
            or self._phase not in {"ready", "stopped"}
            or not prompt.strip()
            or self._sending
        ):
            message = "ターンを開始できない状態"
            raise CodexConnectionError(message)
        self._status("starting")
        self._stopping = False
        self._sending = True
        try:
            if self._thread_id is None:
                await self._start_thread()
            if self._stopping:
                self._status("stopped")
                return
            self._starting_turn = True
            self._user_items += 1
            self._emit(TextEvent(f"user-{self._user_items}", prompt, "user", True))
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
            turn = _object(result.get("turn"))
            if not self._accept_start_response(turn):
                return
            if self._stopping:
                await self._interrupt()
            else:
                self._update_waiting()
        except (TransportError, CodexConnectionError, OSError, UnicodeError):
            await self.close()
            self._status("error", "ターン開始に失敗")
            message = "ターン開始に失敗"
            raise CodexConnectionError(message) from None
        finally:
            self._sending = False
            self._starting_turn = False

    async def answer(
        self,
        token: str,
        *,
        decision: str | None = None,
        answers: "Mapping[str, str] | None" = None,
    ) -> None:
        """Submit one explicit answer to a still-live request.

        Args:
            token: Connection-unique HumanRequest token received by the event callback.
            decision: One advertised one-time approval decision, never inferred.
            answers: One nonempty answer per question ID for a question request.

        Raises:
            CodexConnectionError: The request is stale, the answer is invalid or
                transmission fails. Failed transmissions are not retried.
        """
        await self._operate(self._answer(token, decision=decision, answers=answers))

    async def _answer(
        self, token: str, *, decision: str | None, answers: "Mapping[str, str] | None"
    ) -> None:
        self._require_open()
        request = self._requests.get(token)
        if request is None or self._stopping:
            message = "この要求は回答を受け付けない"
            raise CodexConnectionError(message)
        payload = self._answer_payload(request, decision, answers)
        self._resolve(token)
        try:
            await self._peer.respond(request.rpc_id, payload)
        except TransportError:
            await self.close()
            self._status("error", "回答の送信結果を確認できない")
            message = "回答の送信結果を確認できない"
            raise CodexConnectionError(message) from None
        if decision == "cancel" and request.turn_id == self._turn_id:
            self._stopping = True
            self._expire_requests()
            self._status("stopping")
        else:
            self._update_waiting()

    async def stop(self) -> None:
        """Disable answers immediately and request interruption of the active turn.

        Returning confirms only the interrupt RPC, not turn completion. Questions
        receive no synthetic empty answer or JSON-RPC error during stopping.

        Raises:
            CodexConnectionError: The interrupt request failed.
        """
        await self._operate(self._stop())

    async def _stop(self) -> None:
        self._require_open()
        self._stopping = True
        self._expire_requests()
        self._status("stopping")
        if self._thread_id is not None and self._turn_id is not None:
            await self._interrupt()
        elif not self._sending:
            self._status("stopped")

    async def close(self) -> None:
        """Wait for owned operations, event consumer and peer to close.

        Concurrent callers share cleanup. Cancelling a caller does not cancel
        cleanup; another close call can wait for the same completion.
        """
        if self._cleanup is None:
            self._closed = True
            self._expire_requests()
            self._cleanup = asyncio.create_task(
                self._shutdown(asyncio.current_task()), name="gigas-codex-close"
            )
        await asyncio.shield(self._cleanup)
        current = asyncio.current_task()
        pending = [task for task in self._operations if task is not current and not task.done()]
        if self._pump is not None and self._pump is not current and not self._pump.done():
            pending.append(self._pump)
        if pending:
            await asyncio.shield(asyncio.gather(*pending, return_exceptions=True))

    async def _shutdown(self, origin: asyncio.Task[object] | None) -> None:
        pending = [task for task in self._operations if task is not origin]
        if self._pump is not None and self._pump is not origin:
            pending.append(self._pump)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self._login_id is not None:
            with suppress(TransportError, TimeoutError):
                async with asyncio.timeout(2):
                    await self._peer.request("account/login/cancel", {"loginId": self._login_id})
            self._login_id = None
        await self._peer.close()
        self._status("closed")

    async def _operate(self, operation: "Coroutine[object, object, None]") -> None:
        task = asyncio.create_task(operation, name="gigas-codex-operation")
        self._operations.add(task)
        try:
            await task
        except asyncio.CancelledError:
            if not self._closed:
                await self.close()
            raise
        finally:
            self._operations.discard(task)

    async def _interrupt(self) -> None:
        turn_id = self._turn_id
        if turn_id is None or turn_id in self._interrupted_turns:
            return
        self._interrupted_turns.add(turn_id)
        try:
            await self._peer.request(
                "turn/interrupt", {"threadId": self._thread_id, "turnId": turn_id}
            )
        except TransportError:
            await self.close()
            self._status("error", "中断結果を確認できない")
            message = "中断結果を確認できない"
            raise CodexConnectionError(message) from None

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
                        await self.close()
                        self._status("error", "Codexとの接続が終了")
                    return
                if isinstance(event, ServerRequest):
                    await self._server_request(event)
                elif isinstance(event, RpcNotification):
                    await self._notification(event)
        except (TransportError, CodexConnectionError):
            await self.close()
            self._status("error", "Codexの通知処理に失敗")

    async def _notification(self, event: RpcNotification) -> None:
        params = event.params or {}
        if event.method == "account/login/completed":
            login_id = params.get("loginId")
            if isinstance(login_id, str):
                if login_id == self._login_id:
                    await self._complete_login(params)
                elif self._login_starting:
                    self._login_completions[login_id] = params
            return
        if params.get("threadId") != self._thread_id:
            return
        incoming_turn = params.get("turnId")
        if isinstance(incoming_turn, str):
            self._adopt_turn(incoming_turn)
        if event.method == "serverRequest/resolved":
            request_id = params.get("requestId")
            if isinstance(request_id, str | int) and not isinstance(request_id, bool):
                token = self._request_tokens.get(request_id)
                if token is not None:
                    self._resolve(token)
                    self._update_waiting()
        elif event.method in {"turn/started", "turn/completed"}:
            turn = _object(params.get("turn"))
            self._adopt_turn(_text(turn.get("id")))
            if turn.get("id") != self._turn_id:
                return
            if event.method == "turn/completed":
                self._complete_turn(turn)
            elif self._stopping:
                await self._interrupt()
            else:
                self._update_waiting()
        elif self._turn_id is not None and params.get("turnId") == self._turn_id:
            self._item_notification(event.method, params)

    async def _complete_login(self, params: "JsonObject") -> None:
        self._login_id = None
        if params.get("success") is True:
            await self._read_account()
            await self._read_models()
        self._status("ready" if self._authenticated else "login_required")

    def _adopt_turn(self, turn_id: str) -> None:
        if self._starting_turn and self._turn_id is None and turn_id not in self._completed_turns:
            self._turn_id = turn_id

    def _accept_start_response(self, turn: "JsonObject") -> bool:
        turn_id = _text(turn.get("id"))
        self._adopt_turn(turn_id)
        if turn_id in self._completed_turns:
            return False
        if self._turn_id != turn_id:
            message = "ターンIDが不一致"
            raise CodexConnectionError(message)
        if turn.get("status") in {"completed", "interrupted", "failed"}:
            self._complete_turn(turn)
            return False
        return True

    def _complete_turn(self, turn: "JsonObject") -> None:
        turn_id = _text(turn.get("id"))
        if turn_id != self._turn_id:
            return
        self._completed_turns.add(turn_id)
        self._expire_requests()
        self._turn_id = None
        self._file_details.clear()
        if turn.get("status") == "failed":
            self._status("error", "ターンが失敗")
        elif self._stopping or turn.get("status") == "interrupted":
            self._status("stopped")
        else:
            self._status("ready")

    def _item_notification(self, method: str, params: "JsonObject") -> None:
        if method == "item/agentMessage/delta":
            delta = params.get("delta")
            if isinstance(delta, str):
                self._emit(TextEvent(_text(params.get("itemId")), delta))
        elif method in {"item/started", "item/completed"}:
            item = _object(params.get("item"))
            if item.get("type") == "agentMessage" and method == "item/completed":
                text = item.get("text")
                if isinstance(text, str):
                    self._emit(TextEvent(_text(item.get("id")), text, replace=True))
            elif item.get("type") == "fileChange":
                changes = item.get("changes")
                if isinstance(changes, list):
                    self._file_details[_text(item.get("id"))] = "\n".join(
                        value
                        for change in changes
                        if isinstance(change, dict)
                        for key in ("path", "diff")
                        if isinstance(value := change.get(key), str)
                    )

    async def _server_request(self, event: ServerRequest) -> None:
        methods = {
            "item/tool/requestUserInput": "question",
            "item/commandExecution/requestApproval": "command",
            "item/fileChange/requestApproval": "file",
        }
        if event.method not in methods:
            await self._peer.reject(event.id, -32601, "Unsupported request")
            return
        if event.id in self._seen_requests:
            return
        self._seen_requests.add(event.id)
        params = event.params or {}
        incoming_turn = params.get("turnId")
        if params.get("threadId") == self._thread_id and isinstance(incoming_turn, str):
            self._adopt_turn(incoming_turn)
            if self._stopping:
                await self._interrupt()
        if (
            self._stopping
            or params.get("threadId") != self._thread_id
            or params.get("turnId") != self._turn_id
            or self._turn_id is None
        ):
            return
        try:
            request = self._decode_request(event, params)
        except CodexConnectionError:
            await self._peer.reject(event.id, -32602, "Invalid request parameters")
            return
        self._requests[request.token] = request
        self._request_tokens[event.id] = request.token
        self._emit(request)
        self._update_waiting()

    def _decode_request(self, event: ServerRequest, params: "JsonObject") -> HumanRequest:
        token = uuid.uuid4().hex
        thread_id = _text(params.get("threadId"))
        turn_id = _text(params.get("turnId"))
        item_id = _text(params.get("itemId"))
        if event.method == "item/tool/requestUserInput":
            blocking = params.get("isBlocking")
            if not isinstance(blocking, bool):
                message = "質問の待機形式が不正"
                raise CodexConnectionError(message)
            return HumanRequest(
                token,
                event.id,
                thread_id,
                turn_id,
                "question",
                questions=_questions(params.get("questions")),
                is_blocking=blocking,
            )
        started = params.get("startedAtMs")
        if not isinstance(started, int) or isinstance(started, bool):
            message = "承認要求の時刻が不正"
            raise CodexConnectionError(message)
        kind = "command" if event.method == "item/commandExecution/requestApproval" else "file"
        offered = params.get("availableDecisions")
        if kind == "file" or offered is None:
            decisions = ("accept", "decline", "cancel")
        elif isinstance(offered, list):
            decisions = tuple(
                dict.fromkeys(
                    item
                    for item in offered
                    if isinstance(item, str) and item in {"accept", "decline", "cancel"}
                )
            )
        else:
            decisions = ()
        if not decisions:
            message = "承認要求の判断形式に未対応"
            raise CodexConnectionError(message)
        details = "\n".join(
            f"{label}: {value}"
            for key, label in (
                ("command", "コマンド"),
                ("cwd", "作業フォルダー"),
                ("reason", "理由"),
            )
            if isinstance(value := params.get(key), str) and value
        )
        if kind == "file" and item_id in self._file_details:
            details = "\n".join(part for part in (details, self._file_details[item_id]) if part)
        return HumanRequest(
            token,
            event.id,
            thread_id,
            turn_id,
            kind,
            details=details,
            decisions=decisions,
        )

    @staticmethod
    def _answer_payload(
        request: HumanRequest, decision: str | None, answers: "Mapping[str, str] | None"
    ) -> "JsonObject":
        if request.kind != "question":
            if decision not in request.decisions or answers is not None:
                message = "承認判断を選択する必要がある"
                raise CodexConnectionError(message)
            return {"decision": decision}
        if (
            decision is not None
            or answers is None
            or set(answers) != {q.id for q in request.questions}
        ):
            message = "すべての質問に回答する必要がある"
            raise CodexConnectionError(message)
        payload: JsonObject = {}
        for question in request.questions:
            text = answers[question.id]
            if not text.strip() or (
                question.options
                and not question.is_other
                and text not in {option.label for option in question.options}
            ):
                message = "質問の回答が無効"
                raise CodexConnectionError(message)
            payload[question.id] = {"answers": [text]}
        return {"answers": payload}

    def _resolve(self, token: str) -> None:
        request = self._requests.pop(token, None)
        if request is not None:
            self._request_tokens.pop(request.rpc_id, None)
            self._emit(RequestResolved(token))

    def _expire_requests(self) -> None:
        for token in tuple(self._requests):
            self._resolve(token)

    def _update_waiting(self) -> None:
        if self._turn_id is not None and not self._stopping:
            self._status(
                "waiting" if any(req.is_blocking for req in self._requests.values()) else "running"
            )
