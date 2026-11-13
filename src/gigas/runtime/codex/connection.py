"""Codex account, catalog and conversation behavior over one RPC transport."""

import asyncio
import json
import uuid
from contextlib import suppress
from importlib import resources
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

from gigas.runtime.codex.models import (
    AccountInfo,
    ActivityEvent,
    DialogueEvent,
    GoalEvent,
    GoalState,
    HumanRequest,
    LoginEvent,
    ModelChoice,
    ModelSelection,
    PlanEvent,
    PlanStep,
    Question,
    QuestionOption,
    RateLimitWindow,
    ReasoningEvent,
    RequestResolved,
    SandboxEvent,
    SessionStatus,
    TextEvent,
    ThreadConfiguration,
    TurnStarted,
    UsageBucket,
    UsageCredits,
    UsageEvent,
)
from gigas.runtime.codex.transport import (
    RpcError,
    RpcNotification,
    ServerRequest,
    TransportClosed,
    TransportError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Mapping

    from gigas.runtime.codex.models import ConnectionEvent, ConnectionOptions, ConnectionPhase
    from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcId, TransportEvent


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
    """A submitted message has no confirmed runtime acceptance; never retry it automatically."""


class _ThreadPreparationError(Exception):
    """A host preparation failure that does not invalidate runtime authentication."""


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


def _optional_text(value: "JsonValue") -> str | None:
    return value if isinstance(value, str) else None


def _usage_window(value: "JsonValue") -> RateLimitWindow | None:
    if value is None:
        return None
    window = _object(value)
    used = window.get("usedPercent")
    minutes, resets = window.get("windowDurationMins"), window.get("resetsAt")
    if type(used) is not int:
        raise CodexConnectionError("利用枠の応答形式が不正")
    return RateLimitWindow(
        used,
        minutes if type(minutes) is int else None,
        resets if type(resets) is int else None,
    )


def _usage_bucket(value: "JsonValue", key: str | None = None) -> UsageBucket:
    bucket = _object(value)
    credit_snapshot = None
    if bucket.get("credits") is not None:
        data = _object(bucket["credits"])
        has_credits, unlimited = data.get("hasCredits"), data.get("unlimited")
        if not isinstance(has_credits, bool) or not isinstance(unlimited, bool):
            raise CodexConnectionError("追加利用枠の応答形式が不正")
        credit_snapshot = UsageCredits(has_credits, unlimited, _optional_text(data.get("balance")))
    return UsageBucket(
        _optional_text(bucket.get("limitId")) or key,
        _optional_text(bucket.get("limitName")),
        _usage_window(bucket.get("primary")),
        _usage_window(bucket.get("secondary")),
        credit_snapshot,
    )


def _questions(value: "JsonValue") -> tuple[Question, ...]:
    if not isinstance(value, list) or not value:
        message = "質問の形式が不正"
        raise CodexConnectionError(message)
    questions: list[Question] = []
    for item in value:
        data = _object(item)
        if data.get("isSecret") is True:
            message = "入力を秘匿する質問形式には未対応"
            raise CodexConnectionError(message)
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
            )
        )
    if len({question.id for question in questions}) != len(questions):
        message = "質問IDが重複"
        raise CodexConnectionError(message)
    return tuple(questions)


def _file_changes(value: "JsonValue") -> str:
    if not isinstance(value, list) or not value:
        message = "ファイル変更の確認対象が不足"
        raise CodexConnectionError(message)
    details: list[str] = []
    for raw in value:
        change = _object(raw)
        path = _text(change.get("path"))
        diff = change.get("diff")
        kind = _object(change.get("kind"))
        action = kind.get("type")
        target = kind.get("move_path")
        if (
            action not in ("add", "delete", "update")
            or not isinstance(diff, str)
            or (target is not None and not isinstance(target, str))
        ):
            message = "ファイル変更の形式に未対応"
            raise CodexConnectionError(message)
        details.append(f"{action}: {path}")
        if isinstance(target, str):
            details.append(f"移動先: {target}")
        details.append(diff)
    return "\n".join(details)


class CodexConnection:
    """Own account validation and one conversation over a single RPC peer.

    All methods and the synchronous event callback run on the same asyncio loop.
    The callback must return promptly. Authentication remains owned by the runtime;
    this class never reads or copies credential files.

    Goal completion closes input and reaps public terminal commands while the
    current turn finishes its final response. That turn's completion and a final
    terminal check precede the ready status. Explicit stop or clear interrupts
    this wait instead.

    Args:
        peer: Transport implementing the actual RPC boundary.
        options: Working directory and requested authentication/model settings.
        emit: Callback receiving typed application events, never raw RPC frames.
        prepare_turn: Optional async host preparation for an ordinary send,
            new Goal, or explicit Goal resume, receiving the prompt/objective and
            confirmed thread ID, or None initially. Its configuration starts or
            resumes the thread; autonomous turns and objective updates reuse it.
            None uses options.cwd and leaves an existing thread subscribed.
        on_thread_started: Optional async host recording of the confirmed thread ID.
            Completion is required before accepting the thread or starting a turn.
            Callback errors preserve authentication and permit an explicit retry.
    """

    def __init__(
        self,
        peer: RpcPeer,
        options: "ConnectionOptions",
        emit: "Callable[[ConnectionEvent], None]",
        *,
        prepare_turn: "Callable[[str, str | None], Awaitable[ThreadConfiguration]] | None" = None,
        on_thread_started: "Callable[[str], Awaitable[None]] | None" = None,
    ) -> None:
        """Store connection dependencies without performing I/O."""
        self._peer = peer
        self._options = options
        self._emit = emit
        self._prepare_turn = prepare_turn
        self._on_thread_started = on_thread_started
        self._pump: asyncio.Task[None] | None = None
        self._opened = False
        self._closed = False
        self._authenticated = False
        self._model = ""
        self._effort = ""
        self._choices: tuple[ModelChoice, ...] = ()
        self._phase: ConnectionPhase = "connecting"
        self._thread_id: str | None = None
        self._turn_id: str | None = None
        self._login_id: str | None = None
        self._mcp_config: JsonObject = {}
        self._user_items = 0
        self._stopping = False
        self._finishing_goal_turn: str | None = None
        self._terminal_cleanup: asyncio.Task[None] | None = None
        self._requests: dict[str, HumanRequest] = {}
        self._request_tokens: dict[RpcId, str] = {}
        self._request_items: dict[str, str] = {}
        self._seen_requests: set[RpcId] = set()
        self._file_details: dict[str, str] = {}
        self._summaries: dict[tuple[str, str], dict[int, str]] = {}
        self._operations: set[asyncio.Task[object]] = set()
        self._cleanup: asyncio.Task[None] | None = None
        self._opening = False
        self._sending = False
        self._starting_turn = False
        self._completed_turns: set[str] = set()
        self._interrupted_turns: set[str] = set()
        self._login_starting = False
        self._login_completions: dict[str, JsonObject] = {}
        self._failure_serial = 0
        self._goal: GoalState | None = None
        self._goal_lock = asyncio.Lock()
        self._goal_starting = False
        self._goal_revision = 0
        self._dialogue: dict[tuple[str, str], JsonObject] = {}
        self._usage = UsageEvent()
        self._usage_read: asyncio.Task[None] | None = None
        self._usage_refresh_pending = False
        self._sandbox = SandboxEvent()
        self._sandbox_read: asyncio.Task[None] | None = None
        self._sandbox_setup: asyncio.Future[bool] | None = None

    async def open(self) -> None:
        """Initialize authentication and obtain model choices without starting a thread.

        Missing ChatGPT authentication emits login_required without logging in.
        Invalid defaults or a rejected catalog request preserve the connection for
        login or explicit model selection; sending requires a valid selection.
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
            selection_detail = await self._read_models()
            self._opened = True
            self._status("ready" if self._authenticated else "login_required", selection_detail)
            self._refresh_usage()
            self._sandbox_read = asyncio.create_task(
                self._read_sandbox(), name="gigas-codex-sandbox"
            )
            self._operations.add(self._sandbox_read)
            self._sandbox_read.add_done_callback(self._operations.discard)
        except (TransportError, CodexConnectionError) as error:
            reason = str(error) if isinstance(error, CodexConnectionError) else "Codex接続に失敗"
            await self.close()
            failure_id = self._failure("error", reason)
            raise CodexConnectionError(reason, failure_id=failure_id) from None
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

    async def setup_windows_sandbox(self) -> None:
        """Explicitly prepare the elevated Windows sandbox and confirm readiness.

        Setup does not require login and never grants the runtime cwd as a workspace.
        A lost start response keeps waiting for completion without resending. Closing
        this connection cancels only local waiting; no OS setup cancellation is claimed.

        Raises:
            CodexConnectionError: Setup is unavailable, rejected, failed, or its
                completion does not establish readiness in this connection. A fresh
                connection may be required after a successful initial setup.
        """
        await self._operate(self._setup_windows_sandbox())

    async def _setup_windows_sandbox(self) -> None:
        self._require_open()
        if (
            self._sandbox_setup is not None
            or self._sending
            or self._turn_id is not None
            or self._phase not in {"ready", "stopped", "login_required"}
            or (self._goal is not None and self._goal.status == "active")
        ):
            raise CodexConnectionError("実行環境の準備を開始できない状態")
        completion = asyncio.get_running_loop().create_future()
        self._sandbox_setup = completion
        self._sandbox = SandboxEvent(self._sandbox.readiness, True)
        self._emit(self._sandbox)
        try:
            await self._run_sandbox_setup(completion)
        except CodexConnectionError as error:
            self._sandbox = SandboxEvent(self._sandbox.readiness, False, str(error))
            if not self._closed:
                self._emit(self._sandbox)
            raise
        else:
            if self._closed:
                raise asyncio.CancelledError
            self._sandbox = SandboxEvent("ready")
            self._emit(self._sandbox)
        finally:
            self._sandbox_setup = None

    async def _run_sandbox_setup(self, completion: asyncio.Future[bool]) -> None:
        if self._sandbox_read is not None:
            await self._sandbox_read
        self._require_open()
        try:
            response = await self._peer.request("windowsSandbox/setupStart", {"mode": "elevated"})
        except RpcError:
            raise CodexConnectionError("実行環境の準備開始が拒否された") from None
        except TransportError:
            response = None
        if self._closed:
            raise asyncio.CancelledError
        if isinstance(response, dict) and response.get("started") is False:
            raise CodexConnectionError("実行環境の準備が開始されなかった")
        if not isinstance(response, dict) or response.get("started") is not True:
            self._sandbox = SandboxEvent(
                self._sandbox.readiness, True, "準備開始の受付結果を確認できない"
            )
            self._emit(self._sandbox)
        if not await completion:
            raise CodexConnectionError("実行環境の準備に失敗")
        await self._read_sandbox()
        if self._sandbox.readiness is None:
            raise CodexConnectionError("準備後の実行環境を確認できない")
        if self._sandbox.readiness != "ready":
            raise CodexConnectionError("準備後の再接続が必要")

    async def _read_sandbox(self) -> None:
        try:
            result = _object(await self._peer.request("windowsSandbox/readiness", {}))
        except (TransportError, CodexConnectionError):
            result = {}
        readiness = result.get("status")
        if isinstance(readiness, str) and readiness in {"ready", "notConfigured", "updateRequired"}:
            self._sandbox = SandboxEvent(readiness, self._sandbox_setup is not None)
        else:
            self._sandbox = SandboxEvent(
                None, self._sandbox_setup is not None, "実行環境の準備状態を取得できない"
            )
        if not self._closed:
            self._emit(self._sandbox)

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
            message = "ログイン開始に失敗"
            failure_id = self._failure("login_required", message)
            raise CodexConnectionError(message, failure_id=failure_id) from None
        finally:
            self._login_starting = False
            self._login_completions.clear()

    async def send(self, prompt: str) -> str | None:
        """Prepare the thread's current configuration and start one ordinary turn.

        Args:
            prompt: Nonempty user text. Calls are not retried after unknown acceptance.

        Returns:
            Turn ID confirmed by the public success response, even if the turn has
            already ended. None means stopping prevented submission during preparation.

        Raises:
            MessageDeliveryError: A submitted turn has no confirmed acceptance.
            CodexConnectionError: Preparation failed, the connection is not ready,
                or the runtime explicitly rejected the request.
        """
        return await self._operate(self._send(prompt))

    async def _send(self, prompt: str) -> str | None:
        self._require_open()
        if (
            not self._authenticated
            or self._usage.ordinary_usage_allowed is False
            or not self._model
            or not self._effort
            or self._phase not in {"ready", "stopped"}
            or not prompt.strip()
            or self._sending
            or (self._goal is not None and self._goal.status == "active")
        ):
            message = "ターンを開始できない状態"
            raise CodexConnectionError(message)
        self._status("starting")
        self._stopping = False
        self._terminal_cleanup = None
        self._sending = True
        submitted = False
        accepted_turn: str | None = None
        try:
            if self._thread_id is None or self._prepare_turn is not None:
                await self._configure_thread(prompt)
            if self._stopping:
                self._status("stopped")
                return None
            self._starting_turn = True
            self._user_items += 1
            self._emit(TextEvent(f"user-{self._user_items}", prompt, "user", True))
            submitted = True
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
            active = await self._accept_start_response(turn)
            accepted_turn = _text(turn.get("id"))
            if not active:
                return accepted_turn
            if self._stopping:
                await self._interrupt()
            else:
                self._update_waiting()
        except _ThreadPreparationError as error:
            failure_id = self._failure("stopped" if self._stopping else "ready", str(error))
            raise CodexConnectionError(str(error), failure_id=failure_id) from None
        except RpcError as error:
            if not submitted:
                await self.close()
            message = f"ターン開始が拒否された (RPC {error.code})"
            phase = "error" if self._closed else "stopped" if self._stopping else "ready"
            failure_id = self._failure(phase, message)
            raise CodexConnectionError(message, failure_id=failure_id) from None
        except asyncio.CancelledError:
            if accepted_turn is None:
                raise
            await self.close()
            return accepted_turn
        except (TransportError, CodexConnectionError, OSError, UnicodeError) as error:
            await self.close()
            if accepted_turn is not None:
                return accepted_turn
            if isinstance(error, CodexConnectionError) and error.failure_id is not None:
                raise
            message = "ターン開始に失敗"
            failure_id = self._failure("error", message)
            if submitted:
                raise MessageDeliveryError(message, failure_id=failure_id) from None
            raise CodexConnectionError(message, failure_id=failure_id) from None
        else:
            return accepted_turn
        finally:
            self._sending = False
            self._starting_turn = False

    async def set_goal(self, objective: str) -> bool:
        """Start or update the public Goal once, preserving existing usage and pause.

        Args:
            objective: Nonempty user objective; never duplicated as a steer.

        Returns:
            True after public acceptance; False if stopped before submission.

        Raises:
            CodexConnectionError: Invalid state, preparation, or explicit rejection.
            MessageDeliveryError: Submitted change has unknown acceptance.
        """
        return await self._operate(self._set_goal(objective, resume=False))

    async def resume_goal(self) -> bool:
        """Resume a retained Goal after reloading the same thread's MCP configuration.

        Returns:
            True after acceptance; False if stopped before submission.

        Raises:
            CodexConnectionError: No resumable Goal, an active turn, preparation
                failure, or explicit RPC rejection.
            MessageDeliveryError: Resume acceptance is unknown.
        """
        return await self._operate(self._set_goal(None, resume=True))

    async def _set_goal(self, objective: str | None, *, resume: bool) -> bool:
        self._require_open()
        if (
            not self._authenticated
            or self._usage.ordinary_usage_allowed is False
            or not self._model
            or not self._effort
            or self._sending
            or self._phase not in {"ready", "stopped", "running", "waiting"}
            or (objective is not None and not objective.strip())
        ):
            raise CodexConnectionError("目標を変更できない状態")
        fresh = self._goal is None or self._goal.status == "complete"
        if resume and (fresh or self._goal is None or self._goal.status == "active"):
            raise CodexConnectionError("再開できる目標がない")
        if (fresh or resume) and (
            self._turn_id is not None or self._phase not in {"ready", "stopped"}
        ):
            raise CodexConnectionError("ターンの終了を待つ必要がある")
        submitted = False
        self._sending = True
        if fresh or resume:
            self._stopping = False
            self._terminal_cleanup = None
            self._goal_starting = True
            self._status("starting")
        try:
            async with self._goal_lock:
                if fresh or resume:
                    await self._configure_thread(
                        objective or self._goal.objective if self._goal else objective or ""
                    )
                    if self._stopping:
                        return False
                    if resume:
                        await self._read_retained_goal()
                    elif self._goal is not None:
                        await self._goal_request("thread/goal/clear", {})
                    await self._peer.request(
                        "thread/settings/update",
                        {"threadId": self._thread_id, "model": self._model, "effort": self._effort},
                    )
                    if self._stopping:
                        return False
                params: JsonObject = {}
                if objective is not None:
                    params["objective"] = objective
                if fresh or resume:
                    params["status"] = "active"
                if objective is not None:
                    self._user_items += 1
                    self._emit(TextEvent(f"user-{self._user_items}", objective, "user", True))
                submitted = True
                await self._goal_request("thread/goal/set", params)
                if self._turn_id is None and not self._stopping:
                    self._status("ready")
                return True
        except _ThreadPreparationError as error:
            self._status("stopped" if self._stopping else "ready")
            raise CodexConnectionError(str(error)) from None
        except RpcError as error:
            self._status(
                "stopped"
                if self._stopping and self._turn_id is None
                else self._phase
                if self._turn_id is not None
                else "ready"
            )
            message = f"目標の変更が拒否された (RPC {error.code})"
            raise CodexConnectionError(message) from None
        except TransportError:
            await self.close()
            if submitted:
                raise MessageDeliveryError("目標の変更結果を確認できない") from None
            raise CodexConnectionError("目標の準備に失敗") from None
        except CodexConnectionError as error:
            if self._turn_id is None:
                self._status("stopped" if self._stopping else "ready")
            if submitted:
                raise MessageDeliveryError("目標の変更結果を確認できない") from error
            raise
        finally:
            self._sending = False
            self._goal_starting = False
            if self._stopping and self._turn_id is None and not self._closed:
                self._status("stopped")

    async def clear_goal(self) -> None:
        """Remove the Goal and stop its turn and publicly listed terminal commands.

        Raises:
            CodexConnectionError: Removal, interruption, or terminal cleanup was
                not confirmed; new execution remains unavailable.
        """
        await self._operate(self._stop(clear=True))

    def _accept_goal(self, value: "JsonValue") -> None:
        if value is None:
            goal = None
        else:
            data = _object(value)
            status = data.get("status")
            if (
                not isinstance(status, str)
                or status
                not in {"active", "paused", "blocked", "usageLimited", "budgetLimited", "complete"}
                or data.get("threadId") != self._thread_id
            ):
                raise CodexConnectionError("目標の応答形式が不正")
            tokens, seconds = data.get("tokensUsed"), data.get("timeUsedSeconds")
            if type(tokens) is not int or type(seconds) is not int:
                raise CodexConnectionError("目標の利用量が不正")
            goal = GoalState(_text(data.get("objective")), status, tokens, seconds)
        if goal != self._goal:
            became_inactive = (
                self._goal is not None
                and self._goal.status == "active"
                and (goal is None or goal.status != "active")
            )
            self._goal = goal
            self._emit(GoalEvent(goal))
            if became_inactive:
                self._finishing_goal_turn = (
                    self._turn_id
                    if goal is not None and goal.status == "complete" and not self._stopping
                    else None
                )
                self._stopping = True
                self._expire_requests()
                self._start_terminal_cleanup()
                self._status("stopping" if self._turn_id is not None else "stopped")

    async def _read_retained_goal(self) -> None:
        result = _object(await self._peer.request("thread/goal/get", {"threadId": self._thread_id}))
        self._accept_goal(result.get("goal"))
        if self._goal is None or self._goal.status == "complete":
            raise CodexConnectionError("保持した目標を確認できない")

    async def _goal_request(self, method: str, params: "JsonObject") -> None:
        revision = self._goal_revision
        result = _object(
            await self._peer.request(
                method, {"threadId": self._thread_id, "origin": "user", **params}
            )
        )
        if method == "thread/goal/clear" and not isinstance(result.get("cleared"), bool):
            raise CodexConnectionError("目標の破棄結果が不正")
        if method == "thread/goal/set" and not isinstance(result.get("goal"), dict):
            raise MessageDeliveryError("目標の変更結果を確認できない")
        if revision == self._goal_revision:
            self._accept_goal(None if method == "thread/goal/clear" else result.get("goal"))

    async def _pause_goal(self, *, clear: bool) -> None:
        async with self._goal_lock:
            if self._thread_id is None or self._goal is None:
                return
            if clear:
                await self._goal_request("thread/goal/clear", {})
            elif self._goal.status == "active":
                await self._goal_request("thread/goal/set", {"status": "paused"})

    async def steer(self, prompt: str, expected_turn_id: str) -> str:
        """Submit an instruction to exactly the turn selected by the caller.

        Args:
            prompt: Nonempty text, submitted once without preparing another turn.
            expected_turn_id: Active turn identity captured when the user submits.

        Returns:
            Turn ID confirmed by the public success reply. Acceptance does not
            resolve questions, interrupt input, or guarantee instruction compliance.

        Raises:
            MessageDeliveryError: Runtime acceptance could not be confirmed.
            CodexConnectionError: The target is stale, stopping, invalid, or rejected.
        """
        return await self._operate(self._steer(prompt, expected_turn_id))

    async def _steer(self, prompt: str, expected_turn_id: str) -> str:
        self._require_open()
        if (
            not prompt.strip()
            or not expected_turn_id
            or self._turn_id != expected_turn_id
            or self._stopping
        ):
            message = "このターンは追加指示を受け付けない"
            raise CodexConnectionError(message)
        self._user_items += 1
        self._emit(TextEvent(f"user-{self._user_items}", prompt, "user", True))
        try:
            result = _object(
                await self._peer.request(
                    "turn/steer",
                    {
                        "threadId": self._thread_id,
                        "expectedTurnId": expected_turn_id,
                        "input": [{"type": "text", "text": prompt}],
                    },
                )
            )
        except RpcError as error:
            message = f"追加指示が拒否された (RPC {error.code})"
            raise CodexConnectionError(message) from None
        except (TransportError, CodexConnectionError):
            pass
        else:
            if result.get("turnId") == expected_turn_id:
                return expected_turn_id
        await self.close()
        message = "追加指示の受付結果を確認できない"
        failure_id = self._failure("error", message)
        raise MessageDeliveryError(message, failure_id=failure_id) from None

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
            AnswerDeliveryError: Transport completion is unknown; do not retry.
            CodexConnectionError: The request is stale or the answer is invalid;
                no response was submitted to the transport.

        Returning confirms transport write completion only, not peer acceptance
        or execution of an approved operation. Cancellation leaves delivery unknown.
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
        if decision == "cancel":
            self._stopping = True
            self._expire_requests()
            self._status("stopping")
        try:
            await self._peer.respond(request.rpc_id, payload)
        except TransportError:
            await self.close()
            message = "回答の送信結果を確認できない"
            failure_id = self._failure("error", message)
            raise AnswerDeliveryError(message, failure_id=failure_id) from None
        self._update_waiting()

    async def stop(self) -> None:
        """Disable answers, pause the Goal, and stop its turn and terminal commands.

        Goal pause and turn/terminal cleanup are attempted independently. Returning
        confirms applicable RPCs and an empty public terminal list, not turn
        completion or the end of processes outside that public list.
        Questions receive no synthetic empty answer or JSON-RPC error during stopping.

        Raises:
            CodexConnectionError: Goal pause, interruption, or terminal cleanup was
                not confirmed; new execution remains unavailable.
        """
        await self._operate(self._stop())

    async def _stop(self, *, clear: bool = False) -> None:
        self._require_open()
        self._finishing_goal_turn = None
        self._stopping = True
        self._expire_requests()
        self._status("stopping")
        results = await asyncio.gather(
            self._pause_goal(clear=clear),
            self._start_terminal_cleanup(interrupt=True),
            return_exceptions=True,
        )
        if self._turn_id is None and not self._sending:
            self._status("stopped")
        error = next((result for result in results if isinstance(result, BaseException)), None)
        if self._phase == "error" and isinstance(error, CodexConnectionError):
            await self.close()
            self._status("error", str(error), failure_id=error.failure_id)
            raise error
        if error is not None:
            raise CodexConnectionError("目標の停止または破棄を確認できない") from error

    def _start_terminal_cleanup(self, *, interrupt: bool = False) -> asyncio.Task[None]:
        task = self._terminal_cleanup
        if task is None or (
            task.done()
            and not task.cancelled()
            and task.exception() is None
            and self._turn_id is not None
        ):
            operation = (
                self._interrupt_and_cleanup(None)
                if interrupt
                else self._terminate_thread_commands()
            )
        elif interrupt:
            operation = self._interrupt_and_cleanup(task)
        else:
            return task
        task = asyncio.create_task(operation, name="gigas-codex-stop")
        self._terminal_cleanup = task
        self._operations.add(task)
        task.add_done_callback(self._terminal_cleanup_finished)
        return task

    async def _interrupt_and_cleanup(self, cleanup: asyncio.Task[None] | None) -> None:
        results = await asyncio.gather(
            self._interrupt(close_on_failure=False),
            cleanup if cleanup is not None else self._terminate_thread_commands(),
            return_exceptions=True,
        )
        error = next((result for result in results if isinstance(result, BaseException)), None)
        if error is not None:
            raise error

    def _terminal_cleanup_finished(self, task: asyncio.Task[None]) -> None:
        self._operations.discard(task)
        if (
            not self._closed
            and self._stopping
            and self._turn_id is None
            and self._finishing_goal_turn is None
            and not self._sending
        ):
            self._status("stopped")

    async def _terminate_thread_commands(self) -> None:
        try:
            await self._terminate_listed_commands()
        except (TransportError, CodexConnectionError) as error:
            failure_id = self._failure("error", "端末処理の終了を確認できない")
            raise CodexConnectionError(
                "端末処理の終了を確認できない", failure_id=failure_id
            ) from error

    async def _terminate_listed_commands(self) -> None:
        if self._thread_id is None:
            return
        for process_id in await self._list_thread_commands():
            result = _object(
                await self._peer.request(
                    "thread/backgroundTerminals/terminate",
                    {"threadId": self._thread_id, "processId": process_id},
                )
            )
            if result.get("terminated") is not True:
                raise CodexConnectionError("端末処理の終了を確認できない")
        if await self._list_thread_commands():
            raise CodexConnectionError("スレッドに端末処理が残っている")

    async def _list_thread_commands(self) -> list[str]:
        process_ids: list[str] = []
        cursor: str | None = None
        while True:
            params: JsonObject = {"threadId": self._thread_id}
            if cursor is not None:
                params["cursor"] = cursor
            result = _object(await self._peer.request("thread/backgroundTerminals/list", params))
            data = result.get("data")
            if not isinstance(data, list):
                raise CodexConnectionError("端末一覧の応答形式が不正")
            process_ids.extend(_text(_object(item).get("processId")) for item in data)
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                return process_ids
            cursor = _text(next_cursor)

    async def close(self) -> None:
        """Wait for owned operations, event consumer and peer to close.

        Concurrent callers share cleanup. Cancelling a caller does not cancel
        cleanup; another close call can wait for the same completion.
        """
        if self._cleanup is None:
            self._closed = True
            self._expire_requests()
            self._flush_dialogue()
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

    async def _operate[T](self, operation: "Coroutine[object, object, T]") -> T:
        task = asyncio.create_task(operation, name="gigas-codex-operation")
        self._operations.add(task)
        try:
            return await task
        except asyncio.CancelledError:
            if not self._closed:
                await self.close()
            raise
        finally:
            self._operations.discard(task)

    async def _interrupt(self, *, close_on_failure: bool = True) -> None:
        turn_id = self._turn_id
        if (
            turn_id is None
            or turn_id == self._finishing_goal_turn
            or turn_id in self._interrupted_turns
        ):
            return
        self._interrupted_turns.add(turn_id)
        try:
            await self._peer.request(
                "turn/interrupt", {"threadId": self._thread_id, "turnId": turn_id}
            )
        except TransportError:
            if close_on_failure:
                await self.close()
            message = "中断結果を確認できない"
            failure_id = self._failure("error", message)
            raise CodexConnectionError(message, failure_id=failure_id) from None

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

    async def _configure_thread(self, prompt: str) -> None:
        context = ThreadConfiguration(self._options.cwd)
        if self._prepare_turn is not None:
            try:
                context = await self._prepare_turn(prompt, self._thread_id)
                valid_directory = context.cwd.is_absolute() and context.cwd.is_dir()
            except Exception as error:
                message = "チャットの実行準備に失敗"
                raise _ThreadPreparationError(message) from error
            if not valid_directory:
                message = "チャットの記録先が無効"
                raise _ThreadPreparationError(message)
        if self._stopping:
            return
        if context.memory_dir is not None:
            if self._sandbox_read is not None:
                await self._sandbox_read
            if self._stopping:
                return
            if self._sandbox.readiness != "ready":
                raise _ThreadPreparationError(
                    "実行環境の準備が必要です。Codexメニューから準備してください。"
                )
        instructions = read_play_instructions()
        if not instructions.strip():
            message = "共通指示が空"
            raise CodexConnectionError(message)
        if self._prepare_turn is not None:
            instructions += (
                f"\n\n観測の記録先: {context.cwd}\n"
                "observeが返す観測IDの画像はobservations/<観測ID>.png、"
                "取得時刻・元寸法・出力寸法はevents.jsonlに記録されます。"
                "返された画像と保存画像は同じPNGです。保存は非同期なので、"
                "応答直後にはファイルがなく、保存失敗や終了時の障害で残らない場合があります。"
                "必要な過去画像は保存されている場合に参照してください。"
            )
        config = dict(self._mcp_config)
        if context.cwd != self._options.cwd:
            result = _object(
                await self._peer.request(
                    "config/read", {"includeLayers": False, "cwd": str(context.cwd)}
                )
            )
            servers = _object(_object(result.get("config")).get("mcp_servers", {}))
            for name in servers:
                key = name if name.replace("_", "").isalnum() else json.dumps(name)
                config[f"mcp_servers.{key}.enabled"] = False
        config.update(context.mcp_config)
        if self._stopping:
            return
        memory = context.memory_dir
        if memory is not None:
            if not memory.is_absolute() or not memory.is_dir() or memory != context.cwd / "memory":
                raise CodexConnectionError("記憶の書込先が不正")
            config.update(
                {
                    "sandbox_workspace_write.writable_roots": [],
                    "sandbox_workspace_write.network_access": False,
                    "sandbox_workspace_write.exclude_tmpdir_env_var": True,
                    "sandbox_workspace_write.exclude_slash_tmp": True,
                }
            )
            instructions += (
                "\n案内は ../guide.md、現状メモは current.md、知識は notes/。"
                "書込はこのmemory内だけに限定し、親の原記録は編集しない。"
            )
        params: JsonObject = {
            "cwd": str(memory or context.cwd),
            "model": self._model,
            "developerInstructions": instructions,
            "sandbox": "workspace-write" if memory is not None else "read-only",
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
            "config": config,
        }
        if memory is not None:
            params["runtimeWorkspaceRoots"] = [str(memory)]
        if self._thread_id is None:
            params["ephemeral"] = False
            method = "thread/start"
        else:
            result = _object(
                await self._peer.request("thread/unsubscribe", {"threadId": self._thread_id})
            )
            if result.get("status") not in ("unsubscribed", "notSubscribed", "notLoaded"):
                message = "Codexのチャット購読解除を確認できない"
                raise CodexConnectionError(message)
            if self._stopping:
                return
            params["threadId"] = self._thread_id
            params["excludeTurns"] = True
            method = "thread/resume"
        result = _object(await self._peer.request(method, params))
        sandbox = _object(result.get("sandbox"))
        expected_sandbox: JsonObject = (
            {"type": "readOnly"}
            if memory is None
            else {
                "type": "workspaceWrite",
                "writableRoots": [],
                "networkAccess": False,
                "excludeTmpdirEnvVar": True,
                "excludeSlashTmp": True,
            }
        )
        if (
            result.get("approvalPolicy") != "on-request"
            or result.get("approvalsReviewer") != "user"
            or result.get("model") != self._model
            or any(sandbox.get(key) != value for key, value in expected_sandbox.items())
            or (memory is not None and result.get("cwd") != str(memory))
        ):
            message = "Codexの実行設定が要求と不一致"
            raise CodexConnectionError(message)
        thread_id = _text(_object(result.get("thread")).get("id"))
        if self._thread_id is not None and thread_id != self._thread_id:
            message = "CodexのチャットIDが要求と不一致"
            raise CodexConnectionError(message)
        if self._thread_id is None and self._on_thread_started is not None:
            try:
                await self._on_thread_started(thread_id)
            except Exception as error:
                message = "チャットの対応記録を保存できない"
                raise _ThreadPreparationError(message) from error
        self._thread_id = thread_id

    def _failure(self, phase: "ConnectionPhase", detail: str) -> int:
        self._failure_serial += 1
        self._status(phase, detail, failure_id=self._failure_serial)
        return self._failure_serial

    def _status(
        self, phase: "ConnectionPhase", detail: str = "", *, failure_id: int | None = None
    ) -> None:
        if self._stopping and phase in {"ready", "stopped"}:
            task = self._terminal_cleanup
            if task is not None and (task.cancelled() or (task.done() and task.exception())):
                phase, detail = "error", "端末処理の終了を確認できない"
            elif (task is not None and not task.done()) or self._goal_lock.locked():
                phase = "stopping"
        self._phase = phase
        self._emit(
            SessionStatus(phase, self._model, self._effort, self._authenticated, detail, failure_id)
        )

    async def _read_account(self) -> None:
        result = _object(await self._peer.request("account/read", {"refreshToken": False}))
        account = result.get("account")
        self._authenticated = isinstance(account, dict) and account.get("type") == "chatgpt"
        self._emit(
            AccountInfo(
                _optional_text(account.get("email")),
                _optional_text(account.get("planType")),
                _optional_text(account.get("workspaceName")),
                True,
            )
            if self._authenticated and isinstance(account, dict)
            else AccountInfo()
        )

    def _refresh_usage(self) -> None:
        if not self._authenticated or self._closed:
            return
        self._usage_refresh_pending = True
        if self._usage_read is None or self._usage_read.done():
            self._usage_read = asyncio.create_task(self._read_usage(), name="gigas-codex-usage")
            self._operations.add(self._usage_read)
            self._usage_read.add_done_callback(self._operations.discard)

    async def _read_usage(self) -> None:
        while self._usage_refresh_pending and not self._closed:
            self._usage_refresh_pending = False
            try:
                result = _object(await self._peer.request("account/rateLimits/read", {}))
                keyed = result.get("rateLimitsByLimitId")
                buckets = (
                    tuple(_usage_bucket(value, key) for key, value in keyed.items())
                    if isinstance(keyed, dict) and keyed
                    else (_usage_bucket(result.get("rateLimits")),)
                )
                allowed = result.get("ordinaryUsageAllowed")
                permission = allowed if isinstance(allowed, bool) else None
                if permission is None and self._usage.ordinary_usage_allowed is False:
                    permission = False
                self._usage = UsageEvent(buckets, permission, True)
            except (TransportError, CodexConnectionError):
                self._usage = UsageEvent(
                    self._usage.buckets, self._usage.ordinary_usage_allowed, False
                )
            if self._closed:
                return
            self._emit(self._usage)
            if self._usage.ordinary_usage_allowed is False and not self._stopping:
                self._failure("stopping", "利用制限により続行できない")
                try:
                    await self._stop()
                except CodexConnectionError:
                    if not self._closed:
                        self._failure("error", "利用制限による停止を確認できない")

    async def _read_models(self) -> str:
        self._model, self._effort, self._choices = "", "", ()
        models: list[JsonObject] = []
        cursor: str | None = None
        visited: set[str] = set()
        while True:
            try:
                response = _object(await self._peer.request("model/list", {"cursor": cursor}))
            except RpcError:
                self._emit(ModelSelection((), "", ""))
                return "モデル一覧を取得できない"
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
        choices = []
        for model in models:
            levels = model.get("supportedReasoningEfforts")
            if isinstance(levels, list):
                choices.append(
                    ModelChoice(
                        _text(model.get("model")),
                        tuple(_text(_object(level).get("reasoningEffort")) for level in levels),
                        _text(model.get("defaultReasoningEffort")),
                    )
                )
        self._choices = tuple(choices)
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
        detail = ""
        if selected is None:
            detail = "指定モデルを利用できない"
        else:
            self._model = _text(selected.get("model"))
            effort = self._options.effort or _text(selected.get("defaultReasoningEffort"))
            efforts = selected.get("supportedReasoningEfforts")
            if not isinstance(efforts, list) or not any(
                isinstance(option, dict) and option.get("reasoningEffort") == effort
                for option in efforts
            ):
                detail = "指定推論レベルを利用できない"
            else:
                self._effort = effort

        self._emit(ModelSelection(self._choices, self._model, self._effort))
        return detail

    async def select_model(self, model: str, effort: str) -> None:
        """Select validated values for future turns without changing an active turn.

        A retained Goal also receives the public thread settings update before
        the selection is acknowledged. Ordinary turns use their next start parameters.

        Args:
            model: Model from this connection's catalog.
            effort: Reasoning level supported by that model.

        Raises:
            CodexConnectionError: Selection is unavailable or not in the catalog.
        """
        self._require_open()
        if (
            self._sending
            or not self._authenticated
            or not any(
                choice.model == model and effort in choice.efforts for choice in self._choices
            )
        ):
            raise CodexConnectionError("モデル・推論レベルを選択できない状態")
        if self._thread_id is not None and self._goal is not None:
            try:
                await self._peer.request(
                    "thread/settings/update",
                    {"threadId": self._thread_id, "model": model, "effort": effort},
                )
            except TransportError as error:
                raise CodexConnectionError("次ターン設定の反映を確認できない") from error
        self._model, self._effort = model, effort
        self._emit(ModelSelection(self._choices, model, effort))
        self._status(self._phase)

    async def _consume_events(self) -> None:
        try:
            while not self._closed:
                event = await self._peer.next_event()
                if isinstance(event, TransportClosed):
                    if not self._closed:
                        await self.close()
                        self._failure("error", "Codexとの接続が終了")
                    return
                if isinstance(event, ServerRequest):
                    await self._server_request(event)
                elif isinstance(event, RpcNotification):
                    await self._notification(event)
        except (TransportError, CodexConnectionError) as error:
            await self.close()
            if not isinstance(error, CodexConnectionError) or error.failure_id is None:
                self._failure("error", "Codexの通知処理に失敗")

    async def _notification(self, event: RpcNotification) -> None:
        params = event.params or {}
        if event.method == "windowsSandbox/setupCompleted":
            success = params.get("success")
            if (
                not self._closed
                and self._sandbox_setup is not None
                and not self._sandbox_setup.done()
                and params.get("mode") == "elevated"
                and isinstance(success, bool)
            ):
                self._sandbox_setup.set_result(success)
            return
        if event.method == "account/login/completed":
            login_id = params.get("loginId")
            if isinstance(login_id, str):
                if login_id == self._login_id:
                    await self._complete_login(params)
                elif self._login_starting:
                    self._login_completions[login_id] = params
            return
        if event.method == "account/rateLimits/updated":
            # Rolling updates may omit account metadata; obtain the full snapshot
            # without blocking the consumer or inferring permission from one bucket.
            self._refresh_usage()
            return
        if params.get("threadId") != self._thread_id:
            return
        if event.method == "error":
            if (
                params.get("turnId") == self._turn_id
                and self._turn_id is not None
                and params.get("willRetry") is False
                and not self._stopping
            ):
                self._failure("stopping", "ターンが失敗")
                await self._stop()
            return
        if event.method in {"thread/goal/updated", "thread/goal/cleared"}:
            self._goal_revision += 1
            self._accept_goal(params.get("goal") if event.method.endswith("updated") else None)
            if self._stopping:
                if self._goal is not None and self._goal.status == "complete":
                    await self._start_terminal_cleanup()
                else:
                    await self._stop()
            return
        incoming_turn = params.get("turnId")
        if isinstance(incoming_turn, str):
            self._adopt_turn(incoming_turn)
            if incoming_turn == self._turn_id or incoming_turn in self._completed_turns:
                # Display-only updates, including late history, never change turn lifetime.
                with suppress(CodexConnectionError):
                    self._public_notification(event.method, params, incoming_turn)
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
                stop_goal = (
                    self._terminal_cleanup is None
                    and self._goal is not None
                    and self._goal.status == "active"
                    and turn.get("status") in {"failed", "interrupted"}
                )
                await self._complete_turn(turn)
                if stop_goal:
                    await self._stop()
                elif self._stopping:
                    await self._start_terminal_cleanup()
            elif self._stopping:
                await self._interrupt()
                await self._start_terminal_cleanup()
            else:
                self._update_waiting()
        elif self._turn_id is not None and params.get("turnId") == self._turn_id:
            await self._item_notification(event.method, params)

    async def _complete_login(self, params: "JsonObject") -> None:
        self._login_id = None
        if params.get("success") is not True:
            self._failure("login_required", "ログインに失敗")
            return
        await self._read_account()
        selection_detail = await self._read_models()
        self._status("ready" if self._authenticated else "login_required", selection_detail)
        self._refresh_usage()

    def _adopt_turn(self, turn_id: str) -> None:
        accepting = (
            self._starting_turn
            or self._goal_starting
            or self._stopping
            or (self._goal is not None and self._goal.status == "active")
        )
        if (
            accepting
            and self._thread_id is not None
            and self._turn_id is None
            and turn_id not in self._completed_turns
        ):
            self._turn_id = turn_id
            self._emit(TurnStarted(turn_id))

    async def _accept_start_response(self, turn: "JsonObject") -> bool:
        turn_id = _text(turn.get("id"))
        self._adopt_turn(turn_id)
        if turn_id in self._completed_turns:
            return False
        if self._turn_id != turn_id:
            message = "ターンIDが不一致"
            raise CodexConnectionError(message)
        if turn.get("status") in {"completed", "interrupted", "failed"}:
            await self._complete_turn(turn)
            return False
        return True

    async def _complete_turn(self, turn: "JsonObject") -> None:
        turn_id = _text(turn.get("id"))
        if turn_id != self._turn_id:
            return
        stopped = (
            self._stopping
            or turn.get("status") == "interrupted"
            or (self._goal is not None and self._goal.status == "active")
        )
        if self._stopping or turn.get("status") in {"failed", "interrupted"}:
            self._stopping = True
            self._start_terminal_cleanup()
        self._flush_dialogue(turn_id)
        self._completed_turns.add(turn_id)
        self._expire_requests()
        self._turn_id = None
        self._file_details.clear()
        if self._stopping:
            self._status("stopping")
            await self._start_terminal_cleanup()
        if (
            self._goal is not None
            and self._goal.status == "active"
            and turn.get("status") in {"failed", "interrupted"}
        ):
            self._stopping = True
        finished_goal = self._finishing_goal_turn == turn_id and turn.get("status") == "completed"
        self._finishing_goal_turn = None
        if turn.get("status") == "failed":
            self._failure("stopped" if stopped else "ready", "ターンが失敗")
        elif finished_goal:
            self._status("ready")
        elif self._stopping or turn.get("status") == "interrupted":
            self._status("stopped")
        else:
            self._status("ready")

    async def _item_notification(self, method: str, params: "JsonObject") -> None:
        if method == "item/agentMessage/delta":
            delta = params.get("delta")
            if isinstance(delta, str):
                item_id = _text(params.get("itemId"))
                self._emit(TextEvent(item_id, delta))
                if self._turn_id is not None:
                    key = (self._turn_id, item_id)
                    previous = self._dialogue.get(key, {}).get("text", "")
                    self._dialogue[key] = {
                        "item_id": item_id,
                        "kind": "message",
                        "text": str(previous) + delta,
                    }
        elif method == "item/fileChange/patchUpdated":
            await self._update_file_changes(_text(params.get("itemId")), params.get("changes"))
        elif method in {"item/started", "item/completed"}:
            item = _object(params.get("item"))
            if item.get("type") == "agentMessage" and method == "item/completed":
                text = item.get("text")
                if isinstance(text, str):
                    item_id = _text(item.get("id"))
                    self._emit(TextEvent(item_id, text, replace=True))
                    if self._turn_id is not None:
                        self._finish_dialogue(
                            self._turn_id,
                            item_id,
                            {"item_id": item_id, "kind": "message", "text": text},
                        )
            elif item.get("type") == "fileChange":
                await self._update_file_changes(_text(item.get("id")), item.get("changes"))

    def _public_notification(self, method: str, params: "JsonObject", turn_id: str) -> None:
        if method == "turn/plan/updated":
            plan = params.get("plan")
            if not isinstance(plan, list):
                return
            steps: list[PlanStep] = []
            for raw in plan:
                step = _object(raw)
                status = step.get("status")
                if not isinstance(status, str) or status not in (
                    "pending",
                    "inProgress",
                    "completed",
                ):
                    return
                steps.append(PlanStep(_text(step.get("step")), status))
            explanation = params.get("explanation")
            self._emit(
                PlanEvent(
                    turn_id, tuple(steps), explanation if isinstance(explanation, str) else ""
                )
            )
        elif method == "item/reasoning/summaryTextDelta":
            index, delta = params.get("summaryIndex"), params.get("delta")
            if type(index) is not int or index < 0 or not isinstance(delta, str):
                return
            item_id = _text(params.get("itemId"))
            summary = self._summaries.setdefault((turn_id, item_id), {})
            summary[index] = summary.get(index, "") + delta
            self._emit(ReasoningEvent(turn_id, item_id, tuple(summary[i] for i in sorted(summary))))
            self._dialogue[turn_id, item_id] = {
                "item_id": item_id,
                "kind": "reasoning",
                "summary": [summary[i] for i in sorted(summary)],
            }
        elif method in {"item/started", "item/completed"}:
            item = _object(params.get("item"))
            item_id = _text(item.get("id"))
            if item.get("type") == "reasoning" and method == "item/completed":
                raw_summary = item.get("summary", [])
                if not isinstance(raw_summary, list):
                    return
                summary = {}
                for index, paragraph in enumerate(raw_summary):
                    if not isinstance(paragraph, str):
                        return
                    summary[index] = paragraph
                self._summaries[turn_id, item_id] = summary
                self._emit(ReasoningEvent(turn_id, item_id, tuple(summary.values())))
                self._finish_dialogue(
                    turn_id,
                    item_id,
                    {"item_id": item_id, "kind": "reasoning", "summary": list(summary.values())},
                )
            elif (activity := self._activity(turn_id, item_id, item)) is not None:
                self._emit(activity)
                snapshot: JsonObject = {
                    "item_id": item_id,
                    "kind": "activity",
                    "type": activity.kind,
                    "title": activity.title,
                    "status": activity.status,
                    "details": activity.details,
                }
                if method == "item/completed":
                    self._finish_dialogue(turn_id, item_id, snapshot)
                else:
                    self._dialogue[turn_id, item_id] = snapshot

    def _finish_dialogue(self, turn_id: str, item_id: str, payload: "JsonObject") -> None:
        self._dialogue.pop((turn_id, item_id), None)
        self._emit(DialogueEvent(str(payload["kind"]), {**payload, "partial": False}, turn_id))

    def _flush_dialogue(self, turn_id: str | None = None) -> None:
        for key, payload in tuple(self._dialogue.items()):
            if turn_id is None or key[0] == turn_id:
                self._dialogue.pop(key)
                self._emit(
                    DialogueEvent(str(payload["kind"]), {**payload, "partial": True}, key[0])
                )

    @staticmethod
    def _mcp_text(text: str) -> str:
        # The pinned runtime can wrap the whole MCP result in a text block.
        # Unwrap that single wire envelope, retaining only its text content.
        try:
            result = json.loads(text)
        except (ValueError, RecursionError):
            return text
        if (
            isinstance(result, dict)
            and isinstance(result.get("content"), list)
            and type(result.get("isError")) is bool
        ):
            return "\n".join(
                block["text"]
                for block in result["content"]
                if isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            )
        return text

    @staticmethod
    def _activity(turn_id: str, item_id: str, item: "JsonObject") -> ActivityEvent | None:
        kind, status = item.get("type"), item.get("status")
        if (
            not isinstance(kind, str)
            or not isinstance(status, str)
            or kind not in ("commandExecution", "fileChange", "mcpToolCall")
            or status not in ("inProgress", "completed", "failed", "declined")
        ):
            return None
        details: list[str] = []
        if kind == "commandExecution":
            title = _text(item.get("command"))
            for key in ("cwd", "aggregatedOutput"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    details.append(value)
            exit_code = item.get("exitCode")
            if type(exit_code) is int:
                details.append(f"終了コード: {exit_code}")
        elif kind == "fileChange":
            changes = item.get("changes")
            if not isinstance(changes, list):
                return None
            details.append(_file_changes(changes))
            title = "\n".join(_text(_object(change).get("path")) for change in changes)
        else:
            title = f"{_text(item.get('server'))}/{_text(item.get('tool'))}"
            result = item.get("result")
            if isinstance(result, dict) and isinstance(content := result.get("content"), list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text")
                        if isinstance(text, str):
                            details.append(CodexConnection._mcp_text(text))
            error = item.get("error")
            if isinstance(error, dict) and isinstance(message := error.get("message"), str):
                details.append(message)
        return ActivityEvent(turn_id, item_id, kind, title, status, "\n".join(details))

    async def _update_file_changes(self, item_id: str, changes: "JsonValue") -> None:
        previous = self._file_details.pop(item_id, None)
        try:
            details = _file_changes(changes)
        except CodexConnectionError:
            details = None
        if details is not None:
            self._file_details[item_id] = details
        if previous == details:
            return
        for token, request in tuple(self._requests.items()):
            if request.kind == "file" and self._request_items.get(token) == item_id:
                self._resolve(token)
                await self._peer.reject(request.rpc_id, -32602, "Approval details changed")
        self._update_waiting()

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
        except CodexConnectionError as error:
            await self._peer.reject(event.id, -32602, "Invalid request parameters")
            self._failure(self._phase, str(error))
            return
        self._requests[request.token] = request
        self._request_tokens[event.id] = request.token
        self._request_items[request.token] = _text(params.get("itemId"))
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
        self._validate_approval(kind, item_id, params)
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
                ("environmentId", "環境"),
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

    def _validate_approval(self, kind: str, item_id: str, params: "JsonObject") -> None:
        if kind == "command":
            if (
                params.get("kind", "command") != "command"
                or params.get("environmentId") not in (None, "local")
                or any(
                    params.get(key) is not None
                    for key in ("networkApprovalContext", "additionalPermissions")
                )
            ):
                message = "この承認形式は未対応"
                raise CodexConnectionError(message)
            if not all(
                isinstance(value := params.get(key), str) and value.strip()
                for key in ("command", "cwd")
            ):
                message = "コマンドの確認対象が不足"
                raise CodexConnectionError(message)
        elif params.get("grantRoot") is not None:
            message = "継続的な書込許可は未対応"
            raise CodexConnectionError(message)
        elif item_id not in self._file_details:
            message = "ファイル変更の確認対象が不足"
            raise CodexConnectionError(message)

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
            self._request_items.pop(token, None)
            self._emit(RequestResolved(token))

    def _expire_requests(self) -> None:
        for token in tuple(self._requests):
            self._resolve(token)

    def _update_waiting(self) -> None:
        if self._turn_id is not None and not self._stopping:
            self._status(
                "waiting" if any(req.is_blocking for req in self._requests.values()) else "running"
            )
