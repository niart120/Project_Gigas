import asyncio
from collections.abc import Coroutine
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from gigas.runtime.codex import launch
from gigas.runtime.codex.connection import (
    AnswerDeliveryError,
    CodexConnection,
    CodexConnectionError,
    MessageDeliveryError,
)
from gigas.runtime.codex.models import (
    ActivityEvent,
    ConnectionOptions,
    DialogueEvent,
    HumanRequest,
    LoginEvent,
    ModelSelection,
    PlanEvent,
    PlanStep,
    ReasoningEvent,
    RequestResolved,
    SessionStatus,
    TextEvent,
    ThreadConfiguration,
    TurnStarted,
)
from gigas.runtime.codex.transport import (
    JsonObject,
    JsonValue,
    RpcError,
    RpcId,
    RpcNotification,
    ServerRequest,
    TransportClosed,
    TransportError,
    TransportEvent,
)

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch

    from gigas.runtime.codex.models import ConnectionEvent


def _model(name: str, *, default: bool = False) -> JsonObject:
    return {
        "id": name,
        "model": name,
        "isDefault": default,
        "defaultReasoningEffort": "medium",
        "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "medium"}],
    }


class FakePeer:
    """A scripted RPC boundary; transport parsing and processes are tested separately."""

    def __init__(self) -> None:
        """Prepare a successful catalog and an empty notification queue."""
        self.calls: list[tuple[str, JsonObject | None]] = []
        self.replies: list[tuple[RpcId, JsonValue]] = []
        self.rejections: list[tuple[RpcId, int, str]] = []
        self.events: asyncio.Queue[TransportEvent] = asyncio.Queue()
        self.started = False
        self.closed = False
        self.authenticated = True
        self.failure: str | None = None
        self.responses: dict[str, JsonValue] = {}
        self.rpc_errors: dict[str, int] = {}
        self.gates: dict[str, asyncio.Event] = {}
        self.close_gate: asyncio.Event | None = None
        self.close_calls = 0
        self.respond_gate: asyncio.Event | None = None
        self.goal: JsonObject | None = None
        self.terminals: list[str] = []

    async def start(self) -> None:
        """Record process startup."""
        self.started = True

    async def request(self, method: str, params: JsonObject | None) -> JsonValue:
        """Return protocol fixtures, preserving observable call ordering."""
        self.calls.append((method, params))
        if method in self.gates:
            await self.gates[method].wait()
        if method == self.failure:
            message = "PRIVATE-UPSTREAM-TEXT"
            raise TransportError(message)
        if method in self.rpc_errors:
            raise RpcError(self.rpc_errors[method], method)
        if method in self.responses:
            return self.responses[method]
        if method == "initialize":
            return {"userAgent": "codex/0.167.0", "platformOs": "windows"}
        if method == "account/read":
            return {
                "account": {"type": "chatgpt"} if self.authenticated else None,
                "requiresOpenaiAuth": True,
            }
        if method == "config/read":
            return {"config": {"mcp_servers": {"existing": {"enabled": True}}}}
        if method == "model/list":
            if params and params.get("cursor") == "page-2":
                return {"data": [_model("catalog-default", default=True)], "nextCursor": None}
            return {"data": [_model("other")], "nextCursor": "page-2"}
        if method in {"thread/start", "thread/resume"}:
            assert params is not None
            return {
                "thread": {"id": "thread-1"},
                "approvalPolicy": params["approvalPolicy"],
                "approvalsReviewer": params["approvalsReviewer"],
                "model": params["model"],
                "cwd": params["cwd"],
                "sandbox": {"type": "readOnly"}
                if params["sandbox"] == "read-only"
                else {
                    "type": "workspaceWrite",
                    "writableRoots": [],
                    "networkAccess": False,
                    "excludeTmpdirEnvVar": True,
                    "excludeSlashTmp": True,
                },
            }
        if method == "thread/goal/set":
            assert params is not None
            goal = dict(
                self.goal or {"threadId": "thread-1", "tokensUsed": 0, "timeUsedSeconds": 0}
            )
            goal.update(
                {key: value for key, value in params.items() if key in {"objective", "status"}}
            )
            self.goal = goal
            return {"goal": goal}
        if method == "thread/goal/get":
            return {"goal": self.goal}
        if method == "thread/goal/clear":
            self.goal = None
            return {"cleared": True}
        if method == "thread/backgroundTerminals/list":
            start = int(str(params.get("cursor", "0"))) if params else 0
            return {
                "data": [{"processId": item} for item in self.terminals[start : start + 1]],
                "nextCursor": str(start + 1) if start + 1 < len(self.terminals) else None,
            }
        if method == "thread/backgroundTerminals/terminate":
            assert params is not None
            self.terminals.remove(str(params["processId"]))
            return {"terminated": True}
        if method == "thread/unsubscribe":
            return {"status": "unsubscribed"}
        if method == "turn/start":
            return {"turn": {"id": "turn-1", "status": "inProgress", "items": []}}
        if method == "turn/steer":
            assert params is not None
            return {"turnId": params["expectedTurnId"]}
        if method == "account/login/start":
            return {"type": "chatgpt", "loginId": "login-1", "authUrl": "https://auth.openai.com/"}
        if method == "account/login/cancel":
            return {"status": "canceled"}
        return {}

    async def notify(self, method: str, params: JsonObject | None = None) -> None:
        """Record client notifications."""
        self.calls.append((method, params))

    async def respond(self, request_id: RpcId, result: JsonValue) -> None:
        """Record server-request answers."""
        self.replies.append((request_id, result))
        if self.respond_gate is not None:
            await self.respond_gate.wait()

    async def reject(self, request_id: RpcId, code: int, message: str) -> None:
        """Record explicit protocol rejection."""
        self.rejections.append((request_id, code, message))

    async def next_event(self) -> TransportEvent:
        """Wait for one scripted notification or request."""
        return await self.events.get()

    async def close(self) -> None:
        """Publish the terminal event without retaining a worker."""
        self.close_calls += 1
        if self.close_gate is not None:
            await self.close_gate.wait()
        self.closed = True
        self.events.put_nowait(TransportClosed("closed"))


def _run(scenario: Coroutine[object, object, None]) -> None:
    async def bounded() -> None:
        async with asyncio.timeout(5):
            await scenario

    asyncio.run(bounded())


async def _events_processed() -> None:
    # FakePeer RPCs finish immediately; also drain the shared stop task and its callback.
    for _ in range(5):
        await asyncio.sleep(0)


def test_open_validates_catalog_and_delivers_packaged_instructions(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        assert peer.started
        assert [method for method, _ in peer.calls] == [
            "initialize",
            "initialized",
            "account/read",
            "config/read",
            "model/list",
            "model/list",
        ]
        status = [event for event in events if isinstance(event, SessionStatus)][-1]
        assert (status.phase, status.model, status.effort, status.authenticated) == (
            "ready",
            "catalog-default",
            "medium",
            True,
        )
        await connection.send("確認だけ")
        assert [event for event in events if isinstance(event, TurnStarted)] == [
            TurnStarted("turn-1")
        ]
        start = next(params for method, params in peer.calls if method == "thread/start")
        assert start is not None
        assert start["developerInstructions"] == resources.files("gigas.prompts").joinpath(
            "play.md"
        ).read_text(encoding="utf-8")
        assert start["sandbox"] == "read-only"
        assert start["approvalsReviewer"] == "user"
        assert start["approvalPolicy"] == "on-request"
        assert start["ephemeral"] is False
        assert start["config"] == {"mcp_servers.existing.enabled": False}
        turn = next(params for method, params in peer.calls if method == "turn/start")
        assert turn == {
            "threadId": "thread-1",
            "input": [{"type": "text", "text": "確認だけ"}],
            "model": "catalog-default",
            "effort": "medium",
        }
        peer.events.put_nowait(_terminal())
        await _events_processed()
        peer.responses["turn/start"] = {"turn": {"id": "turn-2", "status": "inProgress"}}
        await connection.send("続けて確認")
        assert sum(method == "thread/start" for method, _ in peer.calls) == 1
        assert not any(
            method in {"thread/unsubscribe", "thread/resume"} for method, _ in peer.calls
        )
        await connection.close()
        assert peer.closed

    _run(scenario())


def test_login_is_explicit_and_completion_rechecks_account(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.authenticated = False
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        assert not any(method == "account/login/start" for method, _ in peer.calls)
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "login_required"
        with pytest.raises(CodexConnectionError):
            await connection.send("未認証")
        await connection.login()
        assert any(isinstance(event, LoginEvent) for event in events)
        assert not any(method == "thread/start" for method, _ in peer.calls)
        peer.authenticated = True
        peer.events.put_nowait(
            RpcNotification("account/login/completed", {"loginId": "login-1", "success": True})
        )
        await _events_processed()
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "ready"
        assert sum(method == "account/read" for method, _ in peer.calls) == 2
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize(("model", "effort"), [("missing", ""), ("other", "ultra")])
@pytest.mark.parametrize("authenticated", [True, False])
def test_invalid_defaults_preserve_login_and_allow_selection_before_sending(
    tmp_path: Path, model: str, effort: str, authenticated: bool
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.authenticated = authenticated
        events: list[ConnectionEvent] = []
        connection = CodexConnection(
            peer, ConnectionOptions(tmp_path, model, effort), events.append
        )
        try:
            await connection.open()
            assert not peer.closed
            status = events[-1]
            assert isinstance(status, SessionStatus)
            assert status.phase == ("ready" if authenticated else "login_required")
            assert status.detail
            selection = next(
                event for event in reversed(events) if isinstance(event, ModelSelection)
            )
            assert [choice.model for choice in selection.choices] == ["other", "catalog-default"]
            assert selection.model == ("other" if model == "other" else "")
            assert selection.effort == ""
            if not authenticated:
                await connection.login()
                peer.authenticated = True
                peer.events.put_nowait(
                    RpcNotification(
                        "account/login/completed", {"loginId": "login-1", "success": True}
                    )
                )
                await _events_processed()
                assert not peer.closed
                status = events[-1]
                assert isinstance(status, SessionStatus)
                assert status.phase == "ready"
                assert status.authenticated
            for operation in (connection.send, connection.set_goal):
                with pytest.raises(CodexConnectionError):
                    await operation("無効な選択のまま開始しない")
            assert not any(method == "thread/start" for method, _ in peer.calls)
            await connection.select_model("other", "low")
            status = events[-1]
            assert isinstance(status, SessionStatus)
            assert (status.model, status.effort, status.detail) == ("other", "low", "")
            await connection.send("選び直して開始")
            turn = next(params for method, params in peer.calls if method == "turn/start")
            assert turn is not None
            assert (turn["model"], turn["effort"]) == ("other", "low")
            assert sum(method == "initialize" for method, _ in peer.calls) == 1
        finally:
            await connection.close()

    _run(scenario())


def test_catalog_rpc_failure_before_login_does_not_block_login(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.authenticated = False
        peer.rpc_errors["model/list"] = -32000
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        try:
            await connection.open()
            status = events[-1]
            assert isinstance(status, SessionStatus)
            assert status.phase == "login_required"
            assert not status.model
            assert not status.effort
            assert status.detail
            assert ModelSelection((), "", "") in events
            await connection.login()
            del peer.rpc_errors["model/list"]
            peer.authenticated = True
            peer.events.put_nowait(
                RpcNotification("account/login/completed", {"loginId": "login-1", "success": True})
            )
            await _events_processed()
            status = events[-1]
            assert isinstance(status, SessionStatus)
            assert (status.phase, status.model, status.effort) == (
                "ready",
                "catalog-default",
                "medium",
            )
            assert not peer.closed
            await connection.send("ログイン後に開始")
        finally:
            await connection.close()

    _run(scenario())


def test_catalog_rejection_after_login_preserves_authenticated_connection(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.authenticated = False
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        try:
            await connection.open()
            await connection.login()
            peer.authenticated = True
            peer.rpc_errors["model/list"] = -32000
            peer.events.put_nowait(
                RpcNotification("account/login/completed", {"loginId": "login-1", "success": True})
            )
            await _events_processed()
            status = events[-1]
            assert isinstance(status, SessionStatus)
            assert status.phase == "ready"
            assert status.authenticated
            assert status.detail
            assert not status.model
            assert not status.effort
            assert not peer.closed
            assert events[-2] == ModelSelection((), "", "")
            with pytest.raises(CodexConnectionError):
                await connection.send("一覧取得失敗中")
            calls = list(peer.calls)
            await _events_processed()
            assert peer.calls == calls
        finally:
            await connection.close()

    _run(scenario())


@pytest.mark.parametrize("contents", ["", "   "])
def test_empty_packaged_instructions_do_not_start_a_thread(
    tmp_path: Path, monkeypatch: "MonkeyPatch", contents: str
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _: None)
        await connection.open()
        monkeypatch.setattr(
            "gigas.runtime.codex.connection.read_play_instructions", lambda: contents
        )
        with pytest.raises(CodexConnectionError):
            await connection.send("開始")
        assert not any(method == "thread/start" for method, _ in peer.calls)
        await connection.close()

    _run(scenario())


def test_initialization_failure_closes_peer_without_leaking_diagnostics(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.failure = "initialize"
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        with pytest.raises(CodexConnectionError) as error:
            await connection.open()
        assert "PRIVATE" not in str(error.value)
        assert "PRIVATE" not in repr(events)
        assert peer.closed
        status = next(event for event in reversed(events) if isinstance(event, SessionStatus))
        assert status.failure_id is not None
        assert status.failure_id == error.value.failure_id

    _run(scenario())


def test_login_failures_correlate_with_results_and_distinguish_retries(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.authenticated = False
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        try:
            await connection.open()
            initial = events[-1]
            assert isinstance(initial, SessionStatus)
            assert initial.phase == "login_required"
            assert initial.failure_id is None
            peer.failure = "account/login/start"
            failures: list[int] = []
            for _ in range(2):
                with pytest.raises(CodexConnectionError) as error:
                    await connection.login()
                status = events[-1]
                assert isinstance(status, SessionStatus)
                assert status.phase == "login_required"
                assert status.failure_id is not None
                assert error.value.failure_id == status.failure_id
                failures.append(status.failure_id)
            assert failures[1] > failures[0]
        finally:
            await connection.close()

    _run(scenario())


def test_failed_login_completion_has_a_failure_without_exposing_remote_text(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.authenticated = False
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        try:
            await connection.open()
            await connection.login()
            peer.events.put_nowait(
                RpcNotification(
                    "account/login/completed",
                    {"loginId": "login-1", "success": False, "error": "PRIVATE-LOGIN-ERROR"},
                )
            )
            await _events_processed()
            status = events[-1]
            assert isinstance(status, SessionStatus)
            assert status.phase == "login_required"
            assert status.detail
            assert status.failure_id is not None
            assert "PRIVATE-LOGIN-ERROR" not in repr(events)
        finally:
            await connection.close()

    _run(scenario())


@pytest.mark.parametrize("operation", ["send", "stop", "unsubscribe", "resume"])
def test_turn_operation_failures_share_status_id_with_exception(
    tmp_path: Path, operation: str
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        refreshing = operation in {"unsubscribe", "resume"}

        async def prepare(_prompt: str, _thread_id: str | None) -> ThreadConfiguration:
            return ThreadConfiguration(tmp_path)

        connection = CodexConnection(
            peer,
            ConnectionOptions(tmp_path),
            events.append,
            prepare_turn=prepare if refreshing else None,
        )
        try:
            await connection.open()
            if operation != "send":
                await connection.send("開始")
            if refreshing:
                peer.events.put_nowait(_terminal())
                await _events_processed()
                peer.failure = f"thread/{operation}"
            else:
                peer.failure = "turn/start" if operation == "send" else "turn/interrupt"
            call = connection.stop() if operation == "stop" else connection.send("開始")
            with pytest.raises(CodexConnectionError) as error:
                await call
            status = events[-1]
            assert isinstance(status, SessionStatus)
            assert status.phase == "error"
            assert status.failure_id is not None
            assert error.value.failure_id == status.failure_id
            if refreshing:
                assert sum(method == "turn/start" for method, _ in peer.calls) == 1
        finally:
            await connection.close()

    _run(scenario())


@pytest.mark.parametrize("case", ["malformed", "secret"])
def test_invalid_server_requests_report_distinct_failures_without_changing_phase(
    tmp_path: Path,
    case: str,
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        try:
            await connection.open()
            await connection.send("開始")
            failures: list[int] = []
            for request_id in ("invalid-1", "invalid-2"):
                params: JsonObject = {"threadId": "thread-1", "turnId": "turn-1", "itemId": "item"}
                if case == "secret":
                    params.update(
                        isBlocking=True,
                        questions=[
                            {"id": "route", "header": "進路", "question": "次の進路は?"},
                            {
                                "id": "private",
                                "header": "確認",
                                "question": "確認値は?",
                                "isSecret": True,
                            },
                        ],
                    )
                peer.events.put_nowait(
                    ServerRequest(
                        request_id,
                        "item/tool/requestUserInput",
                        params,
                    )
                )
                await _events_processed()
                status = events[-1]
                assert isinstance(status, SessionStatus)
                assert status.phase == "running"
                assert status.failure_id is not None
                failures.append(status.failure_id)
            assert failures[1] > failures[0]
            assert len(peer.rejections) == 2
            assert not any(isinstance(event, HumanRequest) for event in events)
            assert not peer.replies
            assert not any(method == "turn/interrupt" for method, _ in peer.calls)
        finally:
            await connection.close()

    _run(scenario())


def test_unimplemented_auth_method_is_explicit(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        connection = CodexConnection(
            peer, ConnectionOptions(tmp_path, auth_method="api_key"), lambda _: None
        )
        with pytest.raises(CodexConnectionError):
            await connection.open()
        assert not any(method == "account/login/start" for method, _ in peer.calls)

    _run(scenario())


def test_unreadable_instructions_never_reach_thread_start(
    tmp_path: Path, monkeypatch: "MonkeyPatch"
) -> None:
    def unavailable() -> str:
        message = "PRIVATE-RESOURCE-PATH"
        raise OSError(message)

    async def scenario() -> None:
        peer = FakePeer()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _: None)
        await connection.open()
        monkeypatch.setattr("gigas.runtime.codex.connection.read_play_instructions", unavailable)
        with pytest.raises(CodexConnectionError) as error:
            await connection.send("開始")
        assert "PRIVATE" not in str(error.value)
        assert not any(method == "thread/start" for method, _ in peer.calls)
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize(
    ("method", "override"),
    [
        ("thread/start", {"approvalsReviewer": "auto_review"}),
        ("thread/resume", {"approvalsReviewer": "auto_review"}),
        ("thread/resume", {"approvalPolicy": "never"}),
        ("thread/resume", {"model": "other"}),
        ("thread/resume", {"sandbox": {"type": "workspaceWrite"}}),
        ("thread/resume", {"thread": {"id": "different-thread"}}),
    ],
)
def test_unconfirmed_thread_settings_do_not_start_a_turn(
    tmp_path: Path, method: str, override: JsonObject
) -> None:
    async def scenario() -> None:
        peer = FakePeer()

        async def prepare(_prompt: str, _thread_id: str | None) -> ThreadConfiguration:
            return ThreadConfiguration(tmp_path)

        connection = CodexConnection(
            peer, ConnectionOptions(tmp_path), lambda _: None, prepare_turn=prepare
        )
        await connection.open()
        if method == "thread/resume":
            await connection.send("first")
            peer.events.put_nowait(_terminal())
            await _events_processed()
        peer.responses[method] = {
            "thread": {"id": "thread-1"},
            "model": "catalog-default",
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
            "sandbox": {"type": "readOnly"},
            **override,
        }
        with pytest.raises(CodexConnectionError):
            await connection.send("開始")
        assert sum(name == "turn/start" for name, _ in peer.calls) == int(method == "thread/resume")
        assert peer.closed
        await connection.close()

    _run(scenario())


def _question(request_id: RpcId = "question-1", *, blocking: bool = True) -> ServerRequest:
    return ServerRequest(
        request_id,
        "item/tool/requestUserInput",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "itemId": "question-item",
            "isBlocking": blocking,
            "questions": [
                {
                    "id": "route",
                    "header": "進路",
                    "question": "どちらへ進むか",
                    "isOther": True,
                    "isSecret": False,
                    "options": [{"label": "東", "description": "東側の道"}],
                }
            ],
        },
    )


def test_answer_transport_failure_is_distinct_from_validation_rejection(tmp_path: Path) -> None:
    class FailedWritePeer(FakePeer):
        """Fail a response after transport submission begins."""

        async def respond(self, request_id: RpcId, result: JsonValue) -> None:
            """Leave the delivery outcome deliberately unknown."""
            self.replies.append((request_id, result))
            message = "PRIVATE-WRITE-ERROR"
            raise TransportError(message)

    async def scenario() -> None:
        peer = FailedWritePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.send("開始")
        peer.events.put_nowait(_question())
        await _events_processed()
        request = next(event for event in events if isinstance(event, HumanRequest))
        with pytest.raises(CodexConnectionError) as invalid:
            await connection.answer(request.token, answers={})
        assert not isinstance(invalid.value, AnswerDeliveryError)
        assert invalid.value.failure_id is None
        assert peer.replies == []
        with pytest.raises(AnswerDeliveryError) as uncertain:
            await connection.answer(request.token, answers={"route": "東"})
        assert "PRIVATE-WRITE-ERROR" not in str(uncertain.value)
        status = events[-1]
        assert isinstance(status, SessionStatus)
        assert status.failure_id is not None
        assert uncertain.value.failure_id == status.failure_id
        assert len(peer.replies) == 1
        assert RequestResolved(request.token) in events
        assert peer.closed
        with pytest.raises(CodexConnectionError):
            await connection.answer(request.token, answers={"route": "東"})
        assert len(peer.replies) == 1

    _run(scenario())


def test_question_is_answered_once_and_preserves_original_request_id(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.send("開始")
        peer.events.put_nowait(_question(7))
        await _events_processed()
        request = next(event for event in events if isinstance(event, HumanRequest))
        assert request.rpc_id == 7
        assert request.kind == "question"
        assert request.questions[0].is_other
        assert request.questions[0].options[0].label == "東"
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "waiting"
        with pytest.raises(CodexConnectionError):
            await connection.answer(request.token, answers={})
        await connection.answer(request.token, answers={"route": "西"})
        assert peer.replies == [(7, {"answers": {"route": {"answers": ["西"]}}})]
        with pytest.raises(CodexConnectionError):
            await connection.answer(request.token, answers={"route": "東"})
        assert RequestResolved(request.token) in events
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize("kind", ["command", "file"])
def test_approvals_require_explicit_supported_decision(tmp_path: Path, kind: str) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.send("開始")
        peer.events.put_nowait(
            RpcNotification(
                "item/started",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {
                        "id": "item-1",
                        "type": "fileChange",
                        "changes": [
                            {"path": "save.md", "diff": "+記録", "kind": {"type": "add"}},
                        ],
                    },
                },
            )
        )
        params: JsonObject = {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "itemId": "item-1",
            "startedAtMs": 10,
            "reason": "確認",
            "command": "Get-Date",
            "cwd": str(tmp_path),
        }
        if kind == "command":
            params["environmentId"] = "local"
            params["availableDecisions"] = [
                "decline",
                "cancel",
                {"applyNetworkPolicyAmendment": {}},
            ]
        method = (
            "item/commandExecution/requestApproval"
            if kind == "command"
            else "item/fileChange/requestApproval"
        )
        peer.events.put_nowait(ServerRequest("approval-1", method, params))
        await _events_processed()
        request = next(event for event in events if isinstance(event, HumanRequest))
        assert not peer.replies
        assert request.kind == kind
        if kind == "command":
            assert request.decisions == ("decline", "cancel")
            assert "Get-Date" in request.details
            assert "local" in request.details
            with pytest.raises(CodexConnectionError):
                await connection.answer(request.token, decision="accept")
        else:
            assert "save.md" in request.details
            assert "+記録" in request.details
        with pytest.raises(CodexConnectionError):
            await connection.answer(request.token)
        await connection.answer(request.token, decision="decline")
        assert peer.replies == [("approval-1", {"decision": "decline"})]
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize(
    "terminal",
    ["resolved", "completed", "stop", "complete", "blocked", "usageLimited", "budgetLimited"],
)
@pytest.mark.parametrize("kind", ["question", "command"])
def test_resolved_completed_or_stopped_question_rejects_late_answers(
    tmp_path: Path, terminal: str, kind: str
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        goal_terminal = terminal in {"complete", "blocked", "usageLimited", "budgetLimited"}
        if goal_terminal:
            await connection.set_goal("goal")
            await connection._notification(
                RpcNotification("turn/started", {"threadId": "thread-1", "turn": {"id": "turn-1"}})
            )
        else:
            await connection.send("開始")
        peer.events.put_nowait(
            _question(blocking=False)
            if kind == "question"
            else ServerRequest(
                "question-1",
                "item/commandExecution/requestApproval",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "cmd",
                    "startedAtMs": 10,
                    "command": "Get-Date",
                    "cwd": str(tmp_path),
                    "environmentId": "local",
                },
            )
        )
        await _events_processed()
        request = next(event for event in events if isinstance(event, HumanRequest))
        if terminal == "resolved":
            peer.events.put_nowait(
                RpcNotification(
                    "serverRequest/resolved",
                    {
                        "threadId": "thread-1",
                        "requestId": "question-1",
                    },
                )
            )
        elif terminal == "completed":
            peer.events.put_nowait(
                RpcNotification(
                    "turn/completed",
                    {
                        "threadId": "thread-1",
                        "turn": {"id": "turn-1", "status": "completed"},
                    },
                )
            )
        elif goal_terminal:
            assert peer.goal is not None
            peer.goal = {**peer.goal, "status": terminal}
            await connection._notification(
                RpcNotification("thread/goal/updated", {"threadId": "thread-1", "goal": peer.goal})
            )
        else:
            await connection.stop()
        await _events_processed()
        with pytest.raises(CodexConnectionError):
            await connection.answer(
                request.token, answers={"route": "東"}
            ) if kind == "question" else await connection.answer(request.token, decision="decline")
        assert not peer.replies
        assert not peer.rejections
        assert RequestResolved(request.token) in events
        if terminal == "stop" or goal_terminal:
            assert ("turn/interrupt", {"threadId": "thread-1", "turnId": "turn-1"}) in peer.calls
            assert isinstance(events[-1], SessionStatus)
            assert events[-1].phase == "stopping"
        await connection.close()

    _run(scenario())


def test_unknown_and_unsupported_mcp_requests_are_explicitly_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.send("開始")
        peer.events.put_nowait(ServerRequest(1, "future/unknown", {}))
        peer.events.put_nowait(
            ServerRequest(
                2,
                "mcpServer/elicitation/request",
                {
                    "threadId": "thread-1",
                    "serverName": "external",
                    "mode": "openai/userVerification",
                },
            )
        )
        await _events_processed()
        assert [request_id for request_id, _, _ in peer.rejections] == [1, 2]
        assert not peer.replies
        assert not any(isinstance(event, HumanRequest) for event in events)
        await connection.close()

    _run(scenario())


def _terminal(status: str = "completed") -> RpcNotification:
    return RpcNotification(
        "turn/completed",
        {"threadId": "thread-1", "turn": {"id": "turn-1", "status": status}},
    )


@pytest.mark.parametrize(
    "method", ["thread/start", "thread/unsubscribe", "thread/resume", "turn/start"]
)
def test_stop_before_start_response_never_resurrects_turn(tmp_path: Path, method: str) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.gates[method] = asyncio.Event()
        events: list[ConnectionEvent] = []
        resuming = method in {"thread/unsubscribe", "thread/resume"}

        async def prepare(_prompt: str, _thread_id: str | None) -> ThreadConfiguration:
            return ThreadConfiguration(tmp_path)

        connection = CodexConnection(
            peer,
            ConnectionOptions(tmp_path),
            events.append,
            prepare_turn=prepare if resuming else None,
        )
        await connection.open()
        if resuming:
            await connection.send("first")
            peer.events.put_nowait(_terminal())
            await _events_processed()
        send = asyncio.create_task(connection.send("開始"))
        await _events_processed()
        await connection.stop()
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "stopping"
        peer.gates[method].set()
        assert await send == ("turn-1" if method == "turn/start" else None)
        if method != "turn/start":
            assert sum(name == "turn/start" for name, _ in peer.calls) == int(resuming)
            assert sum(isinstance(event, TurnStarted) for event in events) == int(resuming)
        else:
            await connection.stop()
            assert sum(name == "turn/interrupt" for name, _ in peer.calls) == 1
            peer.events.put_nowait(_terminal("interrupted"))
            await _events_processed()
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "stopped"
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize("started_notice", [True, False])
def test_notifications_before_start_response_preserve_text_requests_and_completion(
    tmp_path: Path,
    started_notice: bool,
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.gates["turn/start"] = asyncio.Event()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        send = asyncio.create_task(connection.send("開始"))
        await _events_processed()
        if started_notice:
            peer.events.put_nowait(
                RpcNotification("turn/started", {"threadId": "thread-1", "turn": {"id": "turn-1"}})
            )
        peer.events.put_nowait(_question())
        peer.events.put_nowait(
            RpcNotification(
                "turn/plan/updated",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "explanation": "画面を確認する",
                    "plan": [{"step": "観測", "status": "inProgress"}],
                },
            )
        )
        peer.events.put_nowait(
            RpcNotification(
                "item/agentMessage/delta",
                {"threadId": "thread-1", "turnId": "turn-1", "itemId": "a", "delta": "応"},
            )
        )
        peer.events.put_nowait(
            RpcNotification(
                "item/completed",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {"type": "agentMessage", "id": "a", "text": "応答"},
                },
            )
        )
        peer.events.put_nowait(_terminal())
        await _events_processed()
        peer.gates["turn/start"].set()
        assert await send == "turn-1"
        request = next(event for event in events if isinstance(event, HumanRequest))
        assert [event for event in events if isinstance(event, TurnStarted)] == [
            TurnStarted("turn-1")
        ]
        assert events.index(TurnStarted("turn-1")) < events.index(request)
        assert RequestResolved(request.token) in events
        assert TextEvent("a", "応") in events
        assert TextEvent("a", "応答", replace=True) in events
        plan = PlanEvent("turn-1", (PlanStep("観測", "inProgress"),), "画面を確認する")
        assert plan in events
        assert events.index(TurnStarted("turn-1")) < events.index(plan)
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "ready"
        with pytest.raises(CodexConnectionError):
            await connection.answer(request.token, answers={"route": "東"})
        peer.gates["turn/start"].clear()
        peer.responses["turn/start"] = {"turn": {"id": "turn-2", "status": "completed"}}
        second = asyncio.create_task(connection.send("次へ"))
        await _events_processed()
        peer.events.put_nowait(
            RpcNotification(
                "turn/completed",
                {"threadId": "thread-1", "turn": {"id": "turn-2", "status": "completed"}},
            )
        )
        await _events_processed()
        peer.gates["turn/start"].set()
        assert await second == "turn-2"
        assert [event for event in events if isinstance(event, TurnStarted)] == [
            TurnStarted("turn-1"),
            TurnStarted("turn-2"),
        ]
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize("completed_before_reply", [False, True])
def test_steer_targets_one_turn_and_preserves_waiting_request(
    tmp_path: Path, *, completed_before_reply: bool
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        preparations: list[str] = []

        async def prepare(prompt: str, _thread: str | None) -> ThreadConfiguration:
            preparations.append(prompt)
            return ThreadConfiguration(tmp_path)

        connection = CodexConnection(
            peer, ConnectionOptions(tmp_path), events.append, prepare_turn=prepare
        )
        await connection.open()
        assert await connection.send("開始") == "turn-1"
        peer.events.put_nowait(_question())
        await _events_processed()
        request = next(event for event in events if isinstance(event, HumanRequest))
        peer.gates["turn/steer"] = asyncio.Event()
        steering = asyncio.create_task(connection.steer("門の前で止まる", "turn-1"))
        await _events_processed()
        assert not steering.done()
        assert RequestResolved(request.token) not in events
        assert [event for event in events if isinstance(event, SessionStatus)][
            -1
        ].phase == "waiting"
        assert preparations == ["開始"]
        assert [params for method, params in peer.calls if method == "turn/steer"] == [
            {
                "threadId": "thread-1",
                "expectedTurnId": "turn-1",
                "input": [{"type": "text", "text": "門の前で止まる"}],
            }
        ]
        if completed_before_reply:
            peer.events.put_nowait(_terminal())
            await _events_processed()
        peer.gates["turn/steer"].set()
        assert await steering == "turn-1"
        if not completed_before_reply:
            assert RequestResolved(request.token) not in events
            await connection.answer(request.token, answers={"route": "東"})
        assert len([event for event in events if isinstance(event, TurnStarted)]) == 1
        assert [
            event.text for event in events if isinstance(event, TextEvent) and event.role == "user"
        ] == [
            "開始",
            "門の前で止まる",
        ]
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize("case", ["mismatch", "stopped", "completed", "empty"])
def test_stale_or_invalid_steer_never_sends_to_another_turn(tmp_path: Path, case: str) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _: None)
        await connection.open()
        await connection.send("開始")
        if case == "stopped":
            await connection.stop()
        elif case == "completed":
            peer.events.put_nowait(_terminal())
            await _events_processed()
            peer.responses["turn/start"] = {"turn": {"id": "turn-2", "status": "inProgress"}}
            await connection.send("次へ")
        with pytest.raises(CodexConnectionError):
            await connection.steer(
                "" if case == "empty" else "追加", "old" if case == "mismatch" else "turn-1"
            )
        assert not any(method == "turn/steer" for method, _ in peer.calls)
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize("method", ["turn/start", "turn/steer"])
@pytest.mark.parametrize("failure", ["rpc", "transport", "malformed"])
def test_message_rejection_is_distinct_from_unknown_acceptance(
    tmp_path: Path, method: str, failure: str
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _: None)
        await connection.open()
        if method == "turn/steer":
            await connection.send("開始")
        if failure == "rpc":
            peer.rpc_errors[method] = -32600
        elif failure == "transport":
            peer.failure = method
        else:
            peer.responses[method] = {}
        expected = CodexConnectionError if failure == "rpc" else MessageDeliveryError
        operation = (
            connection.steer("追加", "turn-1")
            if method == "turn/steer"
            else connection.send("開始")
        )
        with pytest.raises(expected) as caught:
            await operation
        assert isinstance(caught.value, MessageDeliveryError) == (failure != "rpc")
        assert "PRIVATE" not in str(caught.value)
        assert sum(name == method for name, _ in peer.calls) == 1
        assert peer.closed == (failure != "rpc")
        if failure == "rpc":
            peer.rpc_errors.clear()
            accepted = (
                await connection.steer("明示再送", "turn-1")
                if method == "turn/steer"
                else await connection.send("明示再送")
            )
            assert accepted == "turn-1"
        await connection.close()

    _run(scenario())


def test_start_acceptance_survives_subsequent_interrupt_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.gates["turn/start"] = asyncio.Event()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        sending = asyncio.create_task(connection.send("開始"))
        await _events_processed()
        await connection.stop()
        peer.failure = "turn/interrupt"
        peer.gates["turn/start"].set()
        assert await sending == "turn-1"
        assert peer.closed
        assert any(
            isinstance(event, SessionStatus) and event.failure_id is not None for event in events
        )

    _run(scenario())


def test_login_completion_before_start_response_does_not_leave_stale_url(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.authenticated = False
        peer.gates["account/login/start"] = asyncio.Event()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        login = asyncio.create_task(connection.login())
        await _events_processed()
        with pytest.raises(CodexConnectionError):
            await connection.login()
        peer.authenticated = True
        peer.events.put_nowait(
            RpcNotification("account/login/completed", {"loginId": "login-1", "success": True})
        )
        await _events_processed()
        peer.gates["account/login/start"].set()
        await login
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "ready"
        assert not any(isinstance(event, LoginEvent) for event in events)
        await connection.close()

    _run(scenario())


def test_public_progress_preserves_turn_identity_and_excludes_private_payloads(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.send("開始")

        def notify(method: str, fields: JsonObject, *, turn: str = "turn-1") -> None:
            peer.events.put_nowait(
                RpcNotification(method, {"threadId": "thread-1", "turnId": turn, **fields})
            )

        notify("turn/plan/updated", {"plan": [{"step": "観測", "status": "pending"}]})
        notify("turn/plan/updated", {"plan": [], "explanation": None})
        for index, delta in ((1, "次の確認"), (0, "公開"), (0, "要約")):
            notify(
                "item/reasoning/summaryTextDelta",
                {"itemId": "reason-1", "summaryIndex": index, "delta": delta},
            )
        notify(
            "item/reasoning/textDelta",
            {"itemId": "reason-1", "contentIndex": 0, "delta": "PRIVATE-REASONING"},
        )
        notify(
            "item/completed",
            {
                "item": {
                    "type": "reasoning",
                    "id": "reason-1",
                    "summary": ["確定した公開要約"],
                    "content": ["PRIVATE-REASONING"],
                }
            },
        )
        command: JsonObject = {
            "type": "commandExecution",
            "id": "cmd-1",
            "command": "Get-Date",
            "cwd": "C:/archive",
            "status": "inProgress",
        }
        notify("item/started", {"item": command})
        notify(
            "item/completed",
            {"item": {**command, "status": "completed", "aggregatedOutput": "時刻", "exitCode": 0}},
        )
        mcp: JsonObject = {
            "type": "mcpToolCall",
            "id": "mcp-1",
            "server": "game",
            "tool": "observe",
            "status": "inProgress",
            "arguments": {"authorization": "PRIVATE-ARGUMENT"},
        }
        notify("item/started", {"item": mcp})
        notify(
            "item/completed",
            {
                "item": {
                    **mcp,
                    "status": "completed",
                    "result": {
                        "content": [
                            {"type": "text", "text": "frame-1"},
                            {"type": "image", "data": "PRIVATE-IMAGE", "mimeType": "image/png"},
                        ],
                        "structuredContent": {"private": "PRIVATE-STRUCTURED"},
                        "_meta": {"private": "PRIVATE-META"},
                    },
                }
            },
        )
        notify(
            "item/completed",
            {
                "item": {
                    "type": "fileChange",
                    "id": "file-1",
                    "status": "completed",
                    "changes": [{"path": "memory.md", "kind": {"type": "add"}, "diff": "+記録"}],
                }
            },
        )
        notify("turn/plan/updated", {"plan": []}, turn="unknown-turn")
        peer.events.put_nowait(
            RpcNotification(
                "turn/plan/updated",
                {"threadId": "foreign-thread", "turnId": "turn-1", "plan": []},
            )
        )
        await _events_processed()
        assert [event for event in events if isinstance(event, PlanEvent)] == [
            PlanEvent("turn-1", (PlanStep("観測", "pending"),)),
            PlanEvent("turn-1", ()),
        ]
        assert [event for event in events if isinstance(event, ReasoningEvent)] == [
            ReasoningEvent("turn-1", "reason-1", ("次の確認",)),
            ReasoningEvent("turn-1", "reason-1", ("公開", "次の確認")),
            ReasoningEvent("turn-1", "reason-1", ("公開要約", "次の確認")),
            ReasoningEvent("turn-1", "reason-1", ("確定した公開要約",)),
        ]
        assert [event for event in events if isinstance(event, ActivityEvent)] == [
            ActivityEvent(
                "turn-1", "cmd-1", "commandExecution", "Get-Date", "inProgress", "C:/archive"
            ),
            ActivityEvent(
                "turn-1",
                "cmd-1",
                "commandExecution",
                "Get-Date",
                "completed",
                "C:/archive\n時刻\n終了コード: 0",
            ),
            ActivityEvent("turn-1", "mcp-1", "mcpToolCall", "game/observe", "inProgress"),
            ActivityEvent("turn-1", "mcp-1", "mcpToolCall", "game/observe", "completed", "frame-1"),
            ActivityEvent(
                "turn-1", "file-1", "fileChange", "memory.md", "completed", "add: memory.md\n+記録"
            ),
        ]
        assert "PRIVATE-" not in repr(events)

        peer.events.put_nowait(_terminal())
        await _events_processed()
        peer.responses["turn/start"] = {"turn": {"id": "turn-2", "status": "inProgress"}}
        await connection.send("次へ")
        before = len(events)
        notify("turn/plan/updated", {"plan": [{"step": "観測", "status": "completed"}]})
        notify("item/completed", {"item": {"type": "reasoning", "id": "reason-1", "summary": []}})
        notify(
            "item/completed", {"item": {**mcp, "status": "failed", "error": {"message": "停止"}}}
        )
        notify("turn/plan/updated", {"plan": []}, turn="unknown-turn")
        await _events_processed()
        assert [event for event in events[before:] if not isinstance(event, DialogueEvent)] == [
            PlanEvent("turn-1", (PlanStep("観測", "completed"),)),
            ReasoningEvent("turn-1", "reason-1", ()),
            ActivityEvent("turn-1", "mcp-1", "mcpToolCall", "game/observe", "failed", "停止"),
        ]
        assert [event for event in events if isinstance(event, SessionStatus)][
            -1
        ].phase == "running"
        assert [event for event in events if isinstance(event, TurnStarted)] == [
            TurnStarted("turn-1"),
            TurnStarted("turn-2"),
        ]
        await connection.close()

    _run(scenario())


def test_launch_enables_public_plan_tool(tmp_path: Path, monkeypatch: "MonkeyPatch") -> None:
    commands: list[list[str]] = []

    def transport(
        command: list[str], cwd: Path, env: dict[str, str], *, unset_env: tuple[str, ...] = ()
    ) -> FakePeer:
        assert unset_env == (
            "CODEX_WINDOWS_REGISTERED_CORE",
            "CODEX_WINDOWS_SANDBOX_PACKAGE_FAMILY",
        )
        assert cwd == tmp_path
        assert isinstance(env, dict)
        commands.append(command)
        return FakePeer()

    monkeypatch.setattr(launch, "StdioTransport", transport)
    launch.create_connection(ConnectionOptions(tmp_path), lambda _: None)
    assert "tools.update_plan.enabled=true" in commands[0]


def test_eof_expires_requests_closes_peer_and_prevents_resend(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.send("開始")
        peer.events.put_nowait(_question())
        await _events_processed()
        request = next(event for event in events if isinstance(event, HumanRequest))
        peer.events.put_nowait(TransportClosed("PRIVATE-UPSTREAM-TEXT"))
        for _ in range(8):
            await asyncio.sleep(0)
        assert peer.closed
        assert RequestResolved(request.token) in events
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "error"
        assert "PRIVATE" not in repr(events)
        with pytest.raises(CodexConnectionError):
            await connection.send("再送しない")
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize("method", ["initialize", "turn/start", "turn/steer"])
def test_cancelled_operation_closes_owned_peer(tmp_path: Path, method: str) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.gates[method] = asyncio.Event()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _: None)
        if method == "initialize":
            operation = asyncio.create_task(connection.open())
        else:
            await connection.open()
            if method == "turn/steer":
                await connection.send("開始")
                operation = asyncio.create_task(connection.steer("追加", "turn-1"))
            else:
                operation = asyncio.create_task(connection.send("開始"))
        await _events_processed()
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert peer.closed
        assert sum(name == method for name, _ in peer.calls) == 1
        await connection.close()

    _run(scenario())


def test_close_is_shared_and_survives_caller_cancellation(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.close_gate = asyncio.Event()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _: None)
        await connection.open()
        first = asyncio.create_task(connection.close())
        await _events_processed()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(connection.close())
        await _events_processed()
        assert not second.done()
        peer.close_gate.set()
        await second
        assert peer.closed
        assert peer.close_calls == 1

    _run(scenario())


@pytest.mark.parametrize("pending", [False, True])
def test_close_cancels_login_and_never_emits_a_late_url(tmp_path: Path, pending: bool) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.authenticated = False
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        if pending:
            peer.gates["account/login/start"] = asyncio.Event()
            login = asyncio.create_task(connection.login())
            await _events_processed()
            await connection.close()
            with pytest.raises(asyncio.CancelledError):
                await login
            assert not any(isinstance(event, LoginEvent) for event in events)
        else:
            await connection.login()
            await connection.close()
            assert ("account/login/cancel", {"loginId": "login-1"}) in peer.calls
        assert peer.closed
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "closed"

    _run(scenario())


@pytest.mark.parametrize(
    "goal_status", [None, "complete", "blocked", "usageLimited", "budgetLimited"]
)
def test_terminal_while_answer_write_waits_does_not_restore_running(
    tmp_path: Path, goal_status: str | None
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.respond_gate = asyncio.Event()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        if goal_status is None:
            await connection.send("開始")
        else:
            await connection.set_goal("goal")
            await connection._notification(
                RpcNotification("turn/started", {"threadId": "thread-1", "turn": {"id": "turn-1"}})
            )
        peer.events.put_nowait(_question())
        peer.events.put_nowait(_question())
        await _events_processed()
        requests = [event for event in events if isinstance(event, HumanRequest)]
        assert len(requests) == 1
        answer = asyncio.create_task(connection.answer(requests[0].token, answers={"route": "東"}))
        await _events_processed()
        if goal_status is None:
            peer.events.put_nowait(_terminal())
        else:
            assert peer.goal is not None
            peer.goal = {**peer.goal, "status": goal_status}
            peer.events.put_nowait(
                RpcNotification("thread/goal/updated", {"threadId": "thread-1", "goal": peer.goal})
            )
        peer.events.put_nowait(
            RpcNotification(
                "serverRequest/resolved", {"threadId": "thread-1", "requestId": "question-1"}
            )
        )
        await _events_processed()
        peer.respond_gate.set()
        await answer
        assert len(peer.replies) == 1
        assert events.count(RequestResolved(requests[0].token)) == 1
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == ("ready" if goal_status is None else "stopping")
        if goal_status is not None:
            assert peer.goal is not None
            assert peer.goal["status"] == goal_status
            assert sum(method == "thread/goal/set" for method, _ in peer.calls) == 1
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize("status", ["failed", "interrupted"])
@pytest.mark.parametrize("stopping", [False, True])
def test_terminal_status_and_late_text_do_not_masquerade_as_success(
    tmp_path: Path, status: str, *, stopping: bool
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.send("開始")
        if stopping:
            await connection.stop()
        peer.events.put_nowait(_terminal(status))
        peer.events.put_nowait(
            RpcNotification(
                "item/agentMessage/delta",
                {"threadId": "thread-1", "turnId": "turn-1", "itemId": "old", "delta": "遅い本文"},
            )
        )
        await _events_processed()
        assert not any(isinstance(event, TextEvent) and event.item_id == "old" for event in events)
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == ("stopped" if stopping or status == "interrupted" else "ready")
        if status == "failed":
            assert events[-1].failure_id is not None
            assert events[-1].detail == "ターンが失敗"
        assert not peer.closed
        assert len([call for call in peer.calls if call[0] == "turn/start"]) == 1
        peer.responses["turn/start"] = {
            "turn": {"id": "turn-2", "status": "inProgress", "items": []}
        }
        await connection.send("次の明示送信")
        assert len([call for call in peer.calls if call[0] == "turn/start"]) == 2
        assert TurnStarted("turn-2") in events
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize(
    ("kind", "extra"),
    [
        ("command", {"networkApprovalContext": {"host": "example.org", "protocol": "https"}}),
        ("command", {"additionalPermissions": {"network": {"enabled": True}}}),
        ("command", {"kind": "writeStdin"}),
        ("command", {"kind": "humanCommand"}),
        ("command", {"environmentId": "remote"}),
        ("command", {"command": None}),
        ("command", {"cwd": None}),
        ("file", {"grantRoot": "C:/private"}),
        ("file", {}),
    ],
)
def test_unsupported_or_incomplete_approvals_cannot_be_accepted(
    tmp_path: Path, kind: str, extra: JsonObject
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.send("開始")
        params: JsonObject = {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "itemId": "item-1",
            "startedAtMs": 10,
            "command": "Get-Date",
            "cwd": str(tmp_path),
            "availableDecisions": ["accept", "decline", "cancel"],
        }
        params.update(extra)
        method = (
            "item/commandExecution/requestApproval"
            if kind == "command"
            else "item/fileChange/requestApproval"
        )
        if kind == "file" and "grantRoot" in extra:
            peer.events.put_nowait(
                RpcNotification(
                    "item/started",
                    {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {
                            "id": "item-1",
                            "type": "fileChange",
                            "changes": [
                                {"path": "save.md", "diff": "+record", "kind": {"type": "add"}}
                            ],
                        },
                    },
                )
            )
        peer.events.put_nowait(ServerRequest("restricted", method, params))
        await _events_processed()
        assert not any(isinstance(event, HumanRequest) for event in events)
        assert not peer.replies
        assert [request_id for request_id, _, _ in peer.rejections] == ["restricted"]
        await connection.close()

    _run(scenario())


def test_file_approval_preserves_change_type_and_move_target(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.send("開始")
        peer.events.put_nowait(
            RpcNotification(
                "item/started",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {
                        "type": "fileChange",
                        "id": "file-1",
                        "changes": [
                            {
                                "path": "old.txt",
                                "diff": "-old\n+new",
                                "kind": {"type": "update", "move_path": "new.txt"},
                            }
                        ],
                    },
                },
            )
        )
        peer.events.put_nowait(
            ServerRequest(
                "file-approval",
                "item/fileChange/requestApproval",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "file-1",
                    "startedAtMs": 1,
                },
            )
        )
        await _events_processed()
        request = next(event for event in events if isinstance(event, HumanRequest))
        assert "old.txt" in request.details
        assert "new.txt" in request.details
        assert "update" in request.details
        await connection.answer(request.token, decision="accept")
        assert peer.replies == [("file-approval", {"decision": "accept"})]
        await connection.close()

    _run(scenario())


def test_changed_file_patch_invalidates_previously_presented_approval(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.send("開始")
        peer.events.put_nowait(
            RpcNotification(
                "item/fileChange/patchUpdated",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "file-1",
                    "changes": [{"path": "a.txt", "diff": "+first", "kind": {"type": "add"}}],
                },
            )
        )
        peer.events.put_nowait(
            ServerRequest(
                "file-approval",
                "item/fileChange/requestApproval",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "file-1",
                    "startedAtMs": 1,
                },
            )
        )
        await _events_processed()
        request = next(event for event in events if isinstance(event, HumanRequest))
        peer.events.put_nowait(
            RpcNotification(
                "item/fileChange/patchUpdated",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "file-1",
                    "changes": [{"path": "b.txt", "diff": "+different", "kind": {"type": "add"}}],
                },
            )
        )
        await _events_processed()
        with pytest.raises(CodexConnectionError):
            await connection.answer(request.token, decision="accept")
        assert RequestResolved(request.token) in events
        assert [request_id for request_id, _, _ in peer.rejections] == ["file-approval"]
        assert not peer.replies
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize("goal", [False, True])
def test_cancel_decision_disables_other_answers_before_write_and_terminal(
    tmp_path: Path, *, goal: bool
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.respond_gate = asyncio.Event()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        if goal:
            await connection.set_goal("開始")
            await connection._notification(
                RpcNotification("turn/started", {"threadId": "thread-1", "turn": {"id": "turn-1"}})
            )
        else:
            await connection.send("開始")
        peer.events.put_nowait(_question())
        peer.events.put_nowait(
            ServerRequest(
                "approval",
                "item/commandExecution/requestApproval",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "command",
                    "startedAtMs": 1,
                    "command": "Get-Date",
                    "cwd": str(tmp_path),
                    "availableDecisions": ["accept", "cancel"],
                },
            )
        )
        await _events_processed()
        question, approval = [event for event in events if isinstance(event, HumanRequest)]
        answer = asyncio.create_task(connection.answer(approval.token, decision="cancel"))
        await _events_processed()
        assert RequestResolved(question.token) in events
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "stopping"
        with pytest.raises(CodexConnectionError):
            await connection.answer(question.token, answers={"route": "東"})
        peer.terminals = ["writer"]
        peer.events.put_nowait(_terminal("interrupted"))
        await _events_processed()
        peer.respond_gate.set()
        await answer
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "stopped"
        assert peer.replies == [("approval", {"decision": "cancel"})]
        assert not peer.terminals
        if goal:
            assert peer.goal is not None
            assert peer.goal["status"] == "paused"
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize("stage", ["prepare", "record"])
def test_chat_preparation_failure_keeps_login_and_requires_explicit_retry(
    tmp_path: Path, stage: str
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        fail = True
        prepared: list[str] = []

        async def prepare(_prompt: str, _thread_id: str | None) -> ThreadConfiguration:
            if fail and stage == "prepare":
                raise OSError("PRIVATE-STORAGE-DETAIL")
            return ThreadConfiguration(tmp_path)

        async def record(thread_id: str) -> None:
            prepared.append(thread_id)
            if fail and stage == "record":
                raise OSError("PRIVATE-STORAGE-DETAIL")

        connection = CodexConnection(
            peer,
            ConnectionOptions(tmp_path),
            events.append,
            prepare_turn=prepare,
            on_thread_started=record,
        )
        await connection.open()
        assert prepared == []
        with pytest.raises(CodexConnectionError) as rejected:
            await connection.send("first")
        assert "PRIVATE" not in str(rejected.value)
        assert rejected.value.failure_id is not None
        assert not peer.closed
        assert not any(method == "turn/start" for method, _ in peer.calls)
        status = events[-1]
        assert isinstance(status, SessionStatus)
        assert status.phase == "ready"
        assert status.authenticated
        assert status.failure_id == rejected.value.failure_id
        assert len([m for m, _ in peer.calls if m == "thread/start"]) == int(stage == "record")
        fail = False
        await connection.send("explicit retry")
        assert len([m for m, _ in peer.calls if m == "thread/start"]) == 1 + int(stage == "record")
        assert len([m for m, _ in peer.calls if m == "turn/start"]) == 1
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize("stopping", [False, True])
@pytest.mark.parametrize("stage", ["prepare", "record", "prepare_again"])
def test_thread_preparation_and_recording_precede_turn_and_allow_stop(
    tmp_path: Path, stage: str, *, stopping: bool
) -> None:
    async def scenario() -> None:
        archive = tmp_path / "chat"
        archive.mkdir()
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        entered, release = asyncio.Event(), asyncio.Event()
        recorded: list[str] = []
        preparations: list[tuple[str, str | None]] = []
        config = ThreadConfiguration(
            archive,
            {
                "mcp_servers.gigas": {
                    "url": "http://127.0.0.1:12345/mcp",
                    "bearer_token_env_var": "TEST_TOKEN",
                }
            },
        )

        async def prepare(prompt: str, thread_id: str | None) -> ThreadConfiguration:
            preparations.append((prompt, thread_id))
            if stage == "prepare" or (stage == "prepare_again" and thread_id is not None):
                entered.set()
                await release.wait()
            return config

        async def record(thread_id: str) -> None:
            if stage == "record":
                entered.set()
                await release.wait()
            recorded.append(thread_id)

        connection = CodexConnection(
            peer,
            ConnectionOptions(tmp_path),
            events.append,
            prepare_turn=prepare,
            on_thread_started=record,
        )
        await connection.open()
        assert not entered.is_set()
        peer.responses["config/read"] = {
            "config": {"mcp_servers": {"archive_external": {"enabled": True}}}
        }
        if stage == "prepare_again":
            await connection.send("first")
            peer.events.put_nowait(_terminal())
            await _events_processed()
            peer.responses["turn/start"] = {"turn": {"id": "turn-2", "status": "inProgress"}}
        sending = asyncio.create_task(connection.send("start"))
        await entered.wait()
        initial_turns = int(stage == "prepare_again")
        assert sum(method == "turn/start" for method, _ in peer.calls) == initial_turns
        if stopping:
            await connection.stop()
        release.set()
        await sending
        if stopping:
            assert sum(method == "turn/start" for method, _ in peer.calls) == initial_turns
            assert isinstance(events[-1], SessionStatus)
            assert events[-1].phase == "stopped"
        if stage != "prepare" or not stopping:
            start = next(params for method, params in peer.calls if method == "thread/start")
            assert start is not None
            assert start["cwd"] == str(archive)
            assert start["ephemeral"] is False
            assert start["config"] == {
                "mcp_servers.existing.enabled": False,
                "mcp_servers.archive_external.enabled": False,
                **config.mcp_config,
            }
            assert recorded == ["thread-1"]
        if not stopping:
            assert sum(method == "turn/start" for method, _ in peer.calls) == initial_turns + 1
        assert preparations == (
            [("first", None), ("start", "thread-1")]
            if stage == "prepare_again"
            else [("start", None)]
        )
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize("unsubscribe_status", ["unsubscribed", "notSubscribed", "notLoaded"])
def test_each_send_refreshes_mcp_on_the_same_thread_before_starting_turn(
    tmp_path: Path, unsubscribe_status: str
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.responses["thread/unsubscribe"] = {"status": unsubscribe_status}
        events: list[ConnectionEvent] = []
        preparations: list[tuple[str, str | None]] = []
        recorded: list[str] = []

        async def prepare(prompt: str, thread_id: str | None) -> ThreadConfiguration:
            preparations.append((prompt, thread_id))
            return ThreadConfiguration(
                tmp_path,
                {
                    f"mcp_servers.gigas_{len(preparations)}": {
                        "url": f"http://127.0.0.1:{12000 + len(preparations)}/mcp"
                    }
                },
            )

        async def record(thread_id: str) -> None:
            recorded.append(thread_id)

        connection = CodexConnection(
            peer,
            ConnectionOptions(tmp_path),
            events.append,
            prepare_turn=prepare,
            on_thread_started=record,
        )
        await connection.open()
        await connection.send("first")
        peer.events.put_nowait(_terminal())
        await _events_processed()
        peer.responses["turn/start"] = {"turn": {"id": "turn-2", "status": "inProgress"}}
        peer.gates["thread/unsubscribe"] = asyncio.Event()
        sending = asyncio.create_task(connection.send("second"))
        await _events_processed()
        assert preparations == [("first", None), ("second", "thread-1")]
        assert peer.calls[-1] == ("thread/unsubscribe", {"threadId": "thread-1"})
        assert not any(method == "thread/resume" for method, _ in peer.calls)
        peer.gates["thread/unsubscribe"].set()
        await sending
        assert [method for method, _ in peer.calls][-3:] == [
            "thread/unsubscribe",
            "thread/resume",
            "turn/start",
        ]
        resumed = next(params for method, params in peer.calls if method == "thread/resume")
        assert resumed is not None
        assert resumed["threadId"] == "thread-1"
        assert resumed["config"] == {
            "mcp_servers.existing.enabled": False,
            "mcp_servers.gigas_2": {"url": "http://127.0.0.1:12002/mcp"},
        }
        assert resumed["model"] == "catalog-default"
        assert resumed["sandbox"] == "read-only"
        assert resumed["approvalPolicy"] == "on-request"
        assert resumed["approvalsReviewer"] == "user"
        assert "ephemeral" not in resumed
        assert recorded == ["thread-1"]
        assert sum(method == "thread/start" for method, _ in peer.calls) == 1
        assert [event for event in events if isinstance(event, TurnStarted)] == [
            TurnStarted("turn-1"),
            TurnStarted("turn-2"),
        ]
        await connection.close()

    _run(scenario())


def test_next_turn_selection_preserves_running_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _event: None)
        await connection.open()
        await connection.send("first")
        before = list(peer.calls)
        await connection.select_model("other", "low")
        assert peer.calls == before
        await connection.steer("continue", "turn-1")
        with pytest.raises(CodexConnectionError):
            await connection.select_model("other", "invalid")
        await connection._notification(
            RpcNotification(
                "turn/completed",
                {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed"}},
            )
        )
        await connection.send("second")
        turns = [params for method, params in peer.calls if method == "turn/start"]
        assert turns[0] is not None
        assert turns[1] is not None
        assert turns[0]["model"] == "catalog-default"
        assert turns[1]["model"] == "other"
        assert turns[1]["effort"] == "low"
        await connection.close()

    asyncio.run(scenario())


def test_goal_continues_updates_and_pauses_without_starting_extra_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.goal = {
            "threadId": "thread-1",
            "objective": "first",
            "status": "active",
            "tokensUsed": 12,
            "timeUsedSeconds": 3,
        }
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        assert await connection.set_goal("first")
        for turn_id in ("auto-1", "auto-2"):
            await connection._notification(
                RpcNotification("turn/started", {"threadId": "thread-1", "turn": {"id": turn_id}})
            )
            assert isinstance(events[-1], SessionStatus)
            assert events[-1].phase == "running"
            await connection._notification(
                RpcNotification(
                    "turn/completed",
                    {"threadId": "thread-1", "turn": {"id": turn_id, "status": "completed"}},
                )
            )
        assert await connection.set_goal("revised")
        sets = [params for method, params in peer.calls if method == "thread/goal/set"]
        assert sets[-1] == {"threadId": "thread-1", "objective": "revised", "origin": "user"}
        assert not any(method == "turn/start" for method, _ in peer.calls)
        await connection.stop()
        assert peer.goal["status"] == "paused"
        assert await connection.resume_goal()
        assert peer.goal["tokensUsed"] == 12
        assert [method for method, _ in peer.calls].count("thread/resume") == 1
        await connection.clear_goal()
        assert peer.goal is None
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize("fail_pause", [False, True])
def test_goal_stop_interrupts_even_when_pause_fails_and_rejects_late_turn_input(
    tmp_path: Path, fail_pause: bool
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.set_goal("goal")
        await connection._notification(
            RpcNotification("turn/started", {"threadId": "thread-1", "turn": {"id": "auto-1"}})
        )
        if fail_pause:
            peer.rpc_errors["thread/goal/set"] = -32600
        peer.terminals = ["writer"]
        if fail_pause:
            with pytest.raises(CodexConnectionError):
                await connection.stop()
        else:
            await connection.stop()
        assert ("turn/interrupt", {"threadId": "thread-1", "turnId": "auto-1"}) in peer.calls
        assert not peer.terminals
        await connection._notification(
            RpcNotification(
                "turn/completed",
                {"threadId": "thread-1", "turn": {"id": "auto-1", "status": "interrupted"}},
            )
        )
        await connection._notification(
            RpcNotification("turn/started", {"threadId": "thread-1", "turn": {"id": "late"}})
        )
        assert ("turn/interrupt", {"threadId": "thread-1", "turnId": "late"}) in peer.calls
        with pytest.raises(CodexConnectionError):
            await connection.steer("late instruction", "late")
        assert not any(
            isinstance(e, SessionStatus) and e.phase == "running"
            for e in events[events.index(TurnStarted("late")) :]
        )
        await connection.close()

    _run(scenario())


def test_goal_start_pause_race_is_serialized_without_duplicate_start(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.gates["thread/goal/set"] = asyncio.Event()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _event: None)
        await connection.open()
        starting = asyncio.create_task(connection.set_goal("goal"))
        await _events_processed()
        stopping = asyncio.create_task(connection.stop())
        await _events_processed()
        peer.gates["thread/goal/set"].set()
        assert await starting
        await stopping
        assert peer.goal is not None
        assert peer.goal["status"] == "paused"
        sets = [params for method, params in peer.calls if method == "thread/goal/set"]
        assert len(sets) == 2
        assert not any(method == "turn/start" for method, _ in peer.calls)
        await connection.close()

    _run(scenario())


def test_memory_sandbox_and_goal_settings_use_current_selection(tmp_path: Path) -> None:
    async def scenario() -> None:
        memory = tmp_path / "memory"
        memory.mkdir()

        async def prepare(_prompt: str, _thread_id: str | None) -> ThreadConfiguration:
            return ThreadConfiguration(tmp_path, memory_dir=memory)

        peer = FakePeer()
        connection = CodexConnection(
            peer, ConnectionOptions(tmp_path), lambda _event: None, prepare_turn=prepare
        )
        await connection.open()
        await connection.set_goal("goal")
        await connection.select_model("other", "low")
        assert peer.calls[-1] == (
            "thread/settings/update",
            {"threadId": "thread-1", "model": "other", "effort": "low"},
        )
        await connection.stop()
        await connection.resume_goal()
        resumed = next(params for method, params in peer.calls if method == "thread/resume")
        assert resumed is not None
        assert resumed["cwd"] == str(memory)
        assert resumed["runtimeWorkspaceRoots"] == [str(memory)]
        assert resumed["sandbox"] == "workspace-write"
        assert resumed["model"] == "other"
        config = resumed["config"]
        assert isinstance(config, dict)
        assert config["sandbox_workspace_write.writable_roots"] == []
        await connection.close()

    _run(scenario())


def test_dialogue_snapshots_flush_pending_text_once_on_interruption(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.send("start")
        for delta in ("one", " two"):
            await connection._notification(
                RpcNotification(
                    "item/agentMessage/delta",
                    {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "itemId": "answer",
                        "delta": delta,
                    },
                )
            )
        assert not any(isinstance(event, DialogueEvent) for event in events)
        await connection.stop()
        await connection._notification(_terminal(status="interrupted"))
        await connection.close()
        records = [event for event in events if isinstance(event, DialogueEvent)]
        assert records == [
            DialogueEvent(
                "message",
                {"item_id": "answer", "kind": "message", "text": "one two", "partial": True},
                "turn-1",
            )
        ]

    _run(scenario())


def test_goal_update_rejection_preserves_active_turn_and_objective(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.set_goal("original")
        await connection._notification(
            RpcNotification("turn/started", {"threadId": "thread-1", "turn": {"id": "auto"}})
        )
        peer.rpc_errors["thread/goal/set"] = -32600
        with pytest.raises(CodexConnectionError):
            await connection.set_goal("rejected")
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "running"
        assert peer.goal is not None
        assert peer.goal["objective"] == "original"
        await connection.steer("continue", "auto")
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize("status", ["failed", "interrupted"])
def test_autonomous_turn_failure_pauses_goal_before_further_input(
    tmp_path: Path, status: str
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.set_goal("goal")
        await connection._notification(
            RpcNotification("turn/started", {"threadId": "thread-1", "turn": {"id": "auto"}})
        )
        await connection._notification(
            RpcNotification(
                "turn/completed", {"threadId": "thread-1", "turn": {"id": "auto", "status": status}}
            )
        )
        assert peer.goal is not None
        assert peer.goal["status"] == "paused"
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "stopped"
        await connection.close()

    _run(scenario())


def test_cleared_goal_still_interrupts_a_late_autonomous_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _event: None)
        await connection.open()
        await connection.set_goal("goal")
        await connection.clear_goal()
        assert peer.goal is None
        await connection._notification(
            RpcNotification("turn/started", {"threadId": "thread-1", "turn": {"id": "late"}})
        )
        assert ("turn/interrupt", {"threadId": "thread-1", "turnId": "late"}) in peer.calls
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize("trigger", ["pause", "clear", "inactive", "failed"])
def test_goal_stop_waits_for_terminal_cleanup_before_new_execution(
    tmp_path: Path, trigger: str
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.set_goal("goal")
        await connection._notification(
            RpcNotification("turn/started", {"threadId": "thread-1", "turn": {"id": "auto"}})
        )
        peer.terminals = ["writer-1", "writer-2"]
        gate = peer.gates["thread/backgroundTerminals/terminate"] = asyncio.Event()
        if trigger == "pause":
            stopping = asyncio.create_task(connection.stop())
        elif trigger == "clear":
            stopping = asyncio.create_task(connection.clear_goal())
        elif trigger == "inactive":
            assert peer.goal is not None
            stopping = asyncio.create_task(
                connection._notification(
                    RpcNotification(
                        "thread/goal/updated",
                        {"threadId": "thread-1", "goal": {**peer.goal, "status": "blocked"}},
                    )
                )
            )
        else:
            stopping = asyncio.create_task(
                connection._notification(
                    RpcNotification(
                        "turn/completed",
                        {"threadId": "thread-1", "turn": {"id": "auto", "status": "failed"}},
                    )
                )
            )
        for _ in range(20):
            await asyncio.sleep(0)
        assert not stopping.done()
        if trigger != "failed":
            terminal = asyncio.create_task(
                connection._notification(
                    RpcNotification(
                        "turn/completed",
                        {"threadId": "thread-1", "turn": {"id": "auto", "status": "interrupted"}},
                    )
                )
            )
            await _events_processed()
        else:
            terminal = stopping
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "stopping"
        with pytest.raises(CodexConnectionError):
            await connection.resume_goal()
        with pytest.raises(CodexConnectionError):
            await connection.set_goal("new")
        with pytest.raises(CodexConnectionError):
            await connection.send("chat")
        gate.set()
        await stopping
        await terminal
        await _events_processed()
        assert not peer.terminals
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "stopped"
        terminated = [params for method, params in peer.calls if method.endswith("/terminate")]
        assert terminated == [
            {"threadId": "thread-1", "processId": "writer-1"},
            {"threadId": "thread-1", "processId": "writer-2"},
        ]
        terminal_calls = [method for method, _ in peer.calls if "/backgroundTerminals/" in method]
        assert terminal_calls[-1] == "thread/backgroundTerminals/list"
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize("failure", ["rejected", "rpc", "remains"])
def test_unconfirmed_terminal_cleanup_keeps_execution_closed(tmp_path: Path, failure: str) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.set_goal("goal")
        peer.terminals = ["writer"]
        if failure == "rejected":
            peer.responses["thread/backgroundTerminals/terminate"] = {"terminated": False}
        elif failure == "rpc":
            peer.rpc_errors["thread/backgroundTerminals/terminate"] = -32600
        else:
            peer.responses["thread/backgroundTerminals/terminate"] = {"terminated": True}
        with pytest.raises(CodexConnectionError):
            await connection.stop()
        for operation in (
            connection.resume_goal(),
            connection.send("chat"),
            connection.set_goal("new"),
        ):
            with pytest.raises(CodexConnectionError):
                await operation
        assert len([method for method, _ in peer.calls if method.endswith("/terminate")]) == 1
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "error"
        await connection.close()

    _run(scenario())


def test_close_collects_pending_terminal_cleanup(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _event: None)
        await connection.open()
        await connection.set_goal("goal")
        peer.terminals = ["writer"]
        peer.gates["thread/backgroundTerminals/terminate"] = asyncio.Event()
        stopping = asyncio.create_task(connection.stop())
        for _ in range(20):
            await asyncio.sleep(0)
        assert not stopping.done()
        await connection.close()
        await asyncio.gather(stopping, return_exceptions=True)
        assert peer.closed
        assert not any(task.get_name() == "gigas-codex-stop" for task in asyncio.all_tasks())

    _run(scenario())
