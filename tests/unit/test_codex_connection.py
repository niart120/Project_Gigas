import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from gigas.runtime.codex import launch
from gigas.runtime.codex.connection import (
    CodexConnection,
    CodexConnectionError,
)
from gigas.runtime.codex.models import (
    AccountInfo,
    ConnectionOptions,
    LoginEvent,
    ModelSelection,
    SandboxEvent,
    SessionStatus,
    UsageEvent,
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
    from collections.abc import Callable

    from _pytest.monkeypatch import MonkeyPatch

    from gigas.runtime.codex.connection import RuntimeEvent


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
        self.auto_turn_started = True

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
        if method == "account/rateLimits/read":
            return {"rateLimits": {}, "ordinaryUsageAllowed": True}
        if method == "windowsSandbox/readiness":
            return {"status": "ready"}
        if method == "windowsSandbox/setupStart":
            return {"started": True}
        if method == "config/read":
            return {"config": {"mcp_servers": {"existing": {"enabled": True}}}}
        if method == "model/list":
            if params and params.get("cursor") == "page-2":
                return {"data": [_model("catalog-default", default=True)], "nextCursor": None}
            return {"data": [_model("other")], "nextCursor": "page-2"}
        if method == "thread/start":
            assert params is not None
            return {
                "thread": {"id": "thread-1"},
                "approvalPolicy": params["approvalPolicy"],
                "approvalsReviewer": params["approvalsReviewer"],
                "model": params["model"],
                "cwd": params["cwd"],
                "sandbox": {
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
        if method == "turn/start":
            if self.auto_turn_started:
                self.events.put_nowait(
                    RpcNotification(
                        "turn/started",
                        {
                            "threadId": "thread-1",
                            "turn": {"id": "turn-1", "status": "inProgress"},
                        },
                    )
                )
            return {"turn": {"id": "turn-1", "status": "inProgress", "items": []}}
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


def _phase(events: list["RuntimeEvent"]) -> str:
    return next(event.phase for event in reversed(events) if isinstance(event, SessionStatus))


async def _until(condition: "Callable[[], bool]") -> None:
    while not condition():  # noqa: ASYNC110 -- public observations, bounded by _run deadline.
        await asyncio.sleep(0)


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


def _terminal(status: str = "completed") -> RpcNotification:
    return RpcNotification(
        "turn/completed",
        {"threadId": "thread-1", "turn": {"id": "turn-1", "status": status}},
    )


def test_login_is_explicit_and_completion_rechecks_account(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.authenticated = False
        events: list[RuntimeEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        assert not any(method == "account/login/start" for method, _ in peer.calls)
        status = next(event for event in reversed(events) if isinstance(event, SessionStatus))
        assert status.phase == "login_required"
        with pytest.raises(CodexConnectionError):
            await connection.require_ready()
        await connection.login()
        assert any(isinstance(event, LoginEvent) for event in events)
        assert not any(method == "thread/start" for method, _ in peer.calls)
        peer.authenticated = True
        peer.events.put_nowait(
            RpcNotification("account/login/completed", {"loginId": "login-1", "success": True})
        )
        await _events_processed()
        status = next(event for event in reversed(events) if isinstance(event, SessionStatus))
        assert status.phase == "ready"
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
        events: list[RuntimeEvent] = []
        connection = CodexConnection(
            peer, ConnectionOptions(tmp_path, model, effort), events.append
        )
        try:
            await connection.open()
            assert not peer.closed
            status = next(event for event in reversed(events) if isinstance(event, SessionStatus))
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
                status = next(
                    event for event in reversed(events) if isinstance(event, SessionStatus)
                )
                assert isinstance(status, SessionStatus)
                assert status.phase == "ready"
                assert status.authenticated
            with pytest.raises(CodexConnectionError):
                await connection.require_ready()
            assert not any(method == "thread/start" for method, _ in peer.calls)
            connection.commit_model(connection.propose_model("other", "low"))
            status = next(event for event in reversed(events) if isinstance(event, SessionStatus))
            assert isinstance(status, SessionStatus)
            assert (status.model, status.effort, status.detail) == ("other", "low", "")
            await connection.require_ready()
            assert connection.ready
            assert (connection.selection.model, connection.selection.effort) == ("other", "low")
            assert sum(method == "initialize" for method, _ in peer.calls) == 1
        finally:
            await connection.close()

    _run(scenario())


def test_catalog_rpc_failure_before_login_does_not_block_login(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.authenticated = False
        peer.rpc_errors["model/list"] = -32000
        events: list[RuntimeEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        try:
            await connection.open()
            status = next(event for event in reversed(events) if isinstance(event, SessionStatus))
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
            status = next(event for event in reversed(events) if isinstance(event, SessionStatus))
            assert isinstance(status, SessionStatus)
            assert (status.phase, status.model, status.effort) == (
                "ready",
                "catalog-default",
                "medium",
            )
            assert not peer.closed
            await connection.require_ready()
        finally:
            await connection.close()

    _run(scenario())


def test_catalog_rejection_after_login_preserves_authenticated_connection(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.authenticated = False
        events: list[RuntimeEvent] = []
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
            status = next(event for event in reversed(events) if isinstance(event, SessionStatus))
            assert isinstance(status, SessionStatus)
            assert status.phase == "ready"
            assert status.authenticated
            assert status.detail
            assert not status.model
            assert not status.effort
            assert not peer.closed
            assert next(
                event for event in reversed(events) if isinstance(event, ModelSelection)
            ) == ModelSelection((), "", "")
            with pytest.raises(CodexConnectionError):
                await connection.require_ready()
            calls = list(peer.calls)
            await _events_processed()
            assert peer.calls == calls
        finally:
            await connection.close()

    _run(scenario())


def test_account_usage_is_public_and_zero_credits_do_not_block_included_usage(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.responses["account/read"] = {
            "account": {
                "type": "chatgpt",
                "email": "user@example.test",
                "planType": "pro",
                "workspaceName": "workspace",
            }
        }
        peer.responses["account/rateLimits/read"] = {
            "rateLimits": {
                "limitId": "codex",
                "limitName": "Codex",
                "primary": {"usedPercent": 12, "windowDurationMins": 300, "resetsAt": 1000},
                "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
            },
            "ordinaryUsageAllowed": True,
        }
        events: list[RuntimeEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        try:
            await connection.open()
            await _events_processed()
            assert AccountInfo("user@example.test", "pro", "workspace", True) in events
            usage = next(event for event in reversed(events) if isinstance(event, UsageEvent))
            assert usage.available
            assert usage.ordinary_usage_allowed is True
            bucket = usage.buckets[0]
            assert bucket.limit_id == "codex"
            assert bucket.primary is not None
            assert (
                bucket.primary.used_percent,
                bucket.primary.window_minutes,
                bucket.primary.resets_at,
            ) == (12, 300, 1000)
            assert bucket.credits is not None
            assert bucket.credits.balance == "0"
            await connection.require_ready()
        finally:
            await connection.close()

    _run(scenario())


def test_usage_notifications_during_open_wait_for_initial_read(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        catalog = peer.gates["model/list"] = asyncio.Event()
        peer.responses["account/rateLimits/read"] = {
            "rateLimits": {},
            "ordinaryUsageAllowed": False,
        }
        events: list[RuntimeEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        opening = asyncio.create_task(connection.open())
        try:
            await _until(lambda: any(method == "model/list" for method, _ in peer.calls))
            peer.events.put_nowait(RpcNotification("account/rateLimits/updated", {}))
            await _events_processed()
            assert _phase(events) == "connecting"
            assert not any(method == "account/rateLimits/read" for method, _ in peer.calls)

            catalog.set()
            await opening
            await _until(lambda: any(isinstance(event, UsageEvent) for event in events))
            assert sum(method == "account/rateLimits/read" for method, _ in peer.calls) == 1
            usage = next(event for event in reversed(events) if isinstance(event, UsageEvent))
            assert usage.ordinary_usage_allowed is False
            await connection.require_ready()
            assert connection.ready
            assert _phase(events) == "ready"
        finally:
            catalog.set()
            await connection.close()
            await asyncio.gather(opening, return_exceptions=True)

    _run(scenario())


def test_usage_refresh_is_nonblocking_and_sparse_notifications_refetch_full_snapshot(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[RuntimeEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        try:
            await connection.open()
            await _events_processed()
            await connection.require_ready()
            peer.gates["account/rateLimits/read"] = asyncio.Event()
            peer.responses["account/rateLimits/read"] = {
                "rateLimits": {
                    "limitId": "codex",
                    "limitName": "Codex",
                    "primary": {"usedPercent": 25},
                },
                "ordinaryUsageAllowed": True,
            }
            before = sum(method == "account/rateLimits/read" for method, _ in peer.calls)
            for _ in range(3):
                peer.events.put_nowait(
                    RpcNotification(
                        "account/rateLimits/updated",
                        {"rateLimits": {"limitId": "codex", "limitName": None, "credits": None}},
                    )
                )
                await _events_processed()
            peer.events.put_nowait(
                RpcNotification(
                    "item/agentMessage/delta",
                    {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "itemId": "message",
                        "delta": "still received",
                    },
                )
            )
            await _events_processed()
            assert any(
                isinstance(event, RpcNotification)
                and event.method == "item/agentMessage/delta"
                and event.params is not None
                and event.params["delta"] == "still received"
                for event in events
            )
            assert (
                sum(method == "account/rateLimits/read" for method, _ in peer.calls) == before + 1
            )
            peer.gates["account/rateLimits/read"].set()
            await _events_processed()
            usage = next(event for event in reversed(events) if isinstance(event, UsageEvent))
            assert usage.buckets[0].limit_name == "Codex"
            assert (
                sum(method == "account/rateLimits/read" for method, _ in peer.calls) == before + 2
            )
        finally:
            await connection.close()

    _run(scenario())


@pytest.mark.parametrize("initial_failure", [True, False])
def test_usage_read_failure_does_not_stop_or_retry_execution(
    tmp_path: Path, initial_failure: bool
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        if initial_failure:
            peer.failure = "account/rateLimits/read"
        events: list[RuntimeEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        try:
            await connection.open()
            await _events_processed()
            await connection.require_ready()
            if not initial_failure:
                peer.failure = "account/rateLimits/read"
                peer.events.put_nowait(
                    RpcNotification("account/rateLimits/updated", {"rateLimits": {}})
                )
                await _events_processed()
            usage = next(event for event in reversed(events) if isinstance(event, UsageEvent))
            assert not usage.available
            assert usage.ordinary_usage_allowed is (None if initial_failure else True)
            assert not any(method == "turn/interrupt" for method, _ in peer.calls)
            assert not peer.closed
            calls = list(peer.calls)
            await _events_processed()
            assert peer.calls == calls
        finally:
            await connection.close()

    _run(scenario())


def test_close_cancels_pending_usage_read_without_late_publication(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.gates["account/rateLimits/read"] = asyncio.Event()
        events: list[RuntimeEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await _events_processed()
        assert ("account/rateLimits/read", {}) in peer.calls
        await connection.close()
        assert peer.closed
        before = list(events)
        peer.gates["account/rateLimits/read"].set()
        await _events_processed()
        assert events == before
        assert not any(isinstance(event, UsageEvent) for event in events)

    _run(scenario())


@pytest.mark.parametrize("early_completion", [True, False])
def test_explicit_sandbox_setup_preserves_login_and_handles_early_completion(
    tmp_path: Path, early_completion: bool
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.authenticated = False
        peer.responses["windowsSandbox/readiness"] = {"status": "notConfigured"}
        events: list[RuntimeEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        try:
            await connection.open()
            await _events_processed()
            assert SandboxEvent("notConfigured") in events
            assert not any(method == "windowsSandbox/setupStart" for method, _ in peer.calls)
            peer.gates["windowsSandbox/setupStart"] = asyncio.Event()
            setting_up = asyncio.create_task(connection.setup_windows_sandbox())
            await _events_processed()
            assert ("windowsSandbox/setupStart", {"mode": "elevated"}) in peer.calls
            with pytest.raises(CodexConnectionError):
                await connection.setup_windows_sandbox()
            if not early_completion:
                peer.gates["windowsSandbox/setupStart"].set()
                await _events_processed()
            peer.responses["windowsSandbox/readiness"] = {"status": "ready"}
            peer.events.put_nowait(
                RpcNotification(
                    "windowsSandbox/setupCompleted", {"mode": "elevated", "success": True}
                )
            )
            await _events_processed()
            peer.gates["windowsSandbox/setupStart"].set()
            await setting_up
            assert next(
                event for event in reversed(events) if isinstance(event, SandboxEvent)
            ) == SandboxEvent("ready")
            status = next(event for event in reversed(events) if isinstance(event, SessionStatus))
            assert status.phase == "login_required"
            assert not peer.closed
            await connection.login()
        finally:
            await connection.close()

    _run(scenario())


@pytest.mark.parametrize(
    ("outcome", "setup_error"),
    [
        ("failed", "setup helper exited with status 1"),
        ("failed", None),
        ("failed", ""),
        ("not_ready", "ignored error on success"),
        ("read_failed", "ignored error on success"),
    ],
)
def test_sandbox_setup_failure_preserves_connection_and_requires_ready_confirmation(
    tmp_path: Path, outcome: str, setup_error: str | None
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.responses["windowsSandbox/readiness"] = {"status": "notConfigured"}
        events: list[RuntimeEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        try:
            await connection.open()
            await _events_processed()
            setting_up = asyncio.create_task(connection.setup_windows_sandbox())
            await _events_processed()
            if outcome == "read_failed":
                peer.failure = "windowsSandbox/readiness"
            peer.events.put_nowait(
                RpcNotification(
                    "windowsSandbox/setupCompleted",
                    {
                        "mode": "elevated",
                        "success": outcome != "failed",
                        "error": setup_error,
                    },
                )
            )
            if outcome == "not_ready":
                await setting_up
                event = next(event for event in reversed(events) if isinstance(event, SandboxEvent))
                assert event.reconnect_required
                assert not event.setting_up
                assert not event.detail
                assert not peer.closed
                return
            with pytest.raises(CodexConnectionError) as failure:
                await setting_up
            event = next(event for event in reversed(events) if isinstance(event, SandboxEvent))
            assert not event.setting_up
            assert event.readiness != "ready"
            assert event.detail == str(failure.value)
            assert event.detail
            assert not peer.closed
            if outcome == "failed" and setup_error:
                assert event.detail == setup_error
            elif setup_error:
                assert setup_error not in event.detail
        finally:
            await connection.close()

    _run(scenario())


def test_unknown_sandbox_setup_acceptance_waits_without_retry_and_disconnects(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.failure = "windowsSandbox/setupStart"
        events: list[RuntimeEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        setting_up = asyncio.create_task(connection.setup_windows_sandbox())
        await _events_processed()
        event = next(event for event in reversed(events) if isinstance(event, SandboxEvent))
        assert event.setting_up
        assert event.detail
        with pytest.raises(CodexConnectionError):
            await connection.setup_windows_sandbox()
        assert sum(method == "windowsSandbox/setupStart" for method, _ in peer.calls) == 1
        await connection.close()
        with pytest.raises(asyncio.CancelledError):
            await setting_up
        before = list(events)
        peer.events.put_nowait(
            RpcNotification("windowsSandbox/setupCompleted", {"mode": "elevated", "success": True})
        )
        await _events_processed()
        assert events == before
        assert peer.closed

    _run(scenario())


def test_close_before_setup_readiness_reply_does_not_publish_setup_completion(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        closing: asyncio.Task[None] | None = None

        class CloseBeforeReadinessPeer(FakePeer):
            reads = 0

            async def request(self, method: str, params: JsonObject | None) -> JsonValue:
                nonlocal closing
                result = await super().request(method, params)
                if method == "windowsSandbox/readiness":
                    self.reads += 1
                    if self.reads == 2:
                        closing = asyncio.create_task(connection.close())
                        await asyncio.sleep(0)
                return result

        peer = CloseBeforeReadinessPeer()
        peer.responses["windowsSandbox/readiness"] = {"status": "notConfigured"}
        events: list[RuntimeEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        setting_up = asyncio.create_task(connection.setup_windows_sandbox())
        await _events_processed()
        peer.responses["windowsSandbox/readiness"] = {"status": "ready"}
        peer.events.put_nowait(
            RpcNotification("windowsSandbox/setupCompleted", {"mode": "elevated", "success": True})
        )
        outcomes = await asyncio.gather(setting_up, return_exceptions=True)
        assert isinstance(outcomes[0], asyncio.CancelledError)
        assert closing is not None
        await closing
        assert peer.closed
        assert SandboxEvent("ready") not in events

    _run(scenario())


@pytest.mark.parametrize("rpc_rejection", [True, False])
def test_rejected_sandbox_start_does_not_accept_unrelated_completion(
    tmp_path: Path, rpc_rejection: bool
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        if rpc_rejection:
            peer.rpc_errors["windowsSandbox/setupStart"] = -32600
        else:
            peer.responses["windowsSandbox/setupStart"] = {"started": False}
        events: list[RuntimeEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        try:
            await connection.open()
            await _events_processed()
            with pytest.raises(CodexConnectionError):
                await connection.setup_windows_sandbox()
            event = next(event for event in reversed(events) if isinstance(event, SandboxEvent))
            assert not event.setting_up
            assert event.detail
            before = list(events)
            peer.events.put_nowait(
                RpcNotification(
                    "windowsSandbox/setupCompleted", {"mode": "elevated", "success": True}
                )
            )
            await _events_processed()
            assert events == before
            assert sum(method == "windowsSandbox/setupStart" for method, _ in peer.calls) == 1
            await connection.require_ready()
            assert not peer.closed
        finally:
            await connection.close()

    _run(scenario())


def test_initialization_failure_closes_peer_without_leaking_diagnostics(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.failure = "initialize"
        events: list[RuntimeEvent] = []
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
        events: list[RuntimeEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        try:
            await connection.open()
            initial = next(event for event in reversed(events) if isinstance(event, SessionStatus))
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
        events: list[RuntimeEvent] = []
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


def test_login_completion_before_start_response_does_not_leave_stale_url(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.authenticated = False
        peer.gates["account/login/start"] = asyncio.Event()
        events: list[RuntimeEvent] = []
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
        status = next(event for event in reversed(events) if isinstance(event, SessionStatus))
        assert status.phase == "ready"
        assert not any(isinstance(event, LoginEvent) for event in events)
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
        events: list[RuntimeEvent] = []
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


def test_raw_conversation_events_keep_receiving_while_request_waits(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[object] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        peer.gates["turn/start"] = asyncio.Event()
        pending = asyncio.create_task(connection.request("turn/start", {"threadId": "thread-1"}))
        await _until(lambda: any(method == "turn/start" for method, _ in peer.calls))
        notification = RpcNotification(
            "turn/started", {"threadId": "thread-1", "turn": {"id": "turn-1"}}
        )
        question = _question()
        peer.events.put_nowait(notification)
        peer.events.put_nowait(question)
        await _until(lambda: question in events)
        assert notification in events
        assert not pending.done()
        assert connection.status.phase == "ready"
        assert not peer.replies
        await connection.respond(question.id, {"answers": {}})
        assert peer.replies == [(question.id, {"answers": {}})]
        peer.gates["turn/start"].set()
        await pending
        await connection.close()

    _run(scenario())


def test_open_loads_connection_facts_without_starting_conversation(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _: None)
        await connection.open()
        await _events_processed()
        assert peer.started
        assert connection.ready
        assert connection.status == SessionStatus("ready", "catalog-default", "medium", True)
        assert [method for method, _ in peer.calls] == [
            "initialize",
            "initialized",
            "account/read",
            "model/list",
            "model/list",
            "account/rateLimits/read",
            "windowsSandbox/readiness",
        ]
        assert not (tmp_path / "memory").exists()
        await connection.close()
        assert not connection.ready
        assert connection.status.phase == "closed"

    _run(scenario())


def test_model_candidate_does_not_change_selection_until_committed(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _: None)
        await connection.open()
        original = connection.selection
        proposal = connection.propose_model("other", "low")
        assert connection.selection == original
        with pytest.raises(CodexConnectionError):
            connection.propose_model("missing", "low")
        assert connection.selection == original
        connection.commit_model(proposal)
        assert connection.selection == proposal
        assert connection.status.model == "other"
        assert not any(method == "thread/settings/update" for method, _ in peer.calls)
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize("readiness", ["notConfigured", "updateRequired", None])
def test_execution_readiness_requires_sandbox_without_closing_connection(
    tmp_path: Path, readiness: str | None
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        if readiness is None:
            peer.failure = "windowsSandbox/readiness"
        else:
            peer.responses["windowsSandbox/readiness"] = {"status": readiness}
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _: None)
        await connection.open()
        with pytest.raises(CodexConnectionError, match="サンドボックス"):
            await connection.require_ready()
        assert not peer.closed
        assert connection.ready
        await connection.close()

    _run(scenario())


def test_cancelled_preparation_does_not_cancel_shared_sandbox_read(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        gate = peer.gates["windowsSandbox/readiness"] = asyncio.Event()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _: None)
        await connection.open()
        first = asyncio.create_task(connection.require_ready())
        await _events_processed()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not peer.closed
        gate.set()
        await connection.require_ready()
        assert sum(method == "windowsSandbox/readiness" for method, _ in peer.calls) == 1
        await connection.close()

    _run(scenario())


def test_connection_closure_finishes_receive_loop_and_rejects_new_rpc(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[RuntimeEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        peer.events.put_nowait(TransportClosed("PRIVATE-UPSTREAM-TEXT"))
        await _until(lambda: peer.closed)
        await connection.close()
        assert not connection.ready
        assert any(isinstance(event, SessionStatus) and event.failure_id for event in events)
        assert "PRIVATE" not in repr(events)
        before = list(events)
        peer.events.put_nowait(_terminal())
        await _events_processed()
        assert events == before
        with pytest.raises(CodexConnectionError):
            await connection.request("turn/start", {})
        assert not any(method == "turn/start" for method, _ in peer.calls)

    _run(scenario())


def test_cancelled_open_closes_owned_transport(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.gates["initialize"] = asyncio.Event()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _: None)
        opening = asyncio.create_task(connection.open())
        await _until(lambda: any(method == "initialize" for method, _ in peer.calls))
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await opening
        assert peer.closed
        assert peer.close_calls == 1

    _run(scenario())


@pytest.mark.parametrize("failure", ["rpc", "transport", "cancelled"])
def test_request_failure_remains_owned_by_caller(tmp_path: Path, failure: str) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _: None)
        await connection.open()
        if failure == "rpc":
            peer.rpc_errors["turn/start"] = -32000
            with pytest.raises(RpcError):
                await connection.request("turn/start", {})
        elif failure == "transport":
            peer.failure = "turn/start"
            with pytest.raises(TransportError):
                await connection.request("turn/start", {})
        else:
            peer.gates["turn/start"] = asyncio.Event()
            request = asyncio.create_task(connection.request("turn/start", {}))
            await _until(lambda: any(method == "turn/start" for method, _ in peer.calls))
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        assert sum(method == "turn/start" for method, _ in peer.calls) == 1
        assert connection.ready
        assert not peer.closed
        await connection.close()

    _run(scenario())


def test_login_completion_read_does_not_block_conversation_event_delivery(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.authenticated = False
        events: list[RuntimeEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        await connection.login()
        peer.authenticated = True
        gate = peer.gates["account/read"] = asyncio.Event()
        peer.events.put_nowait(
            RpcNotification("account/login/completed", {"loginId": "login-1", "success": True})
        )
        await _until(lambda: sum(method == "account/read" for method, _ in peer.calls) == 2)
        notification = _terminal()
        peer.events.put_nowait(notification)
        await _until(lambda: notification in events)
        assert not connection.ready
        gate.set()
        await _until(lambda: connection.ready)
        await connection.close()

    _run(scenario())
