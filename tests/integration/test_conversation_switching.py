import asyncio
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import httpx
import pytest
from pytestqt.qtbot import QtBot

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.mcp.game_io import GameIoMcpServer
from gigas.runtime.codex.connection import CodexConnection
from gigas.runtime.codex.management import ProjectPage, ThreadPage
from gigas.runtime.codex.models import (
    ConnectionOptions,
    ConversationStatus,
    GoalState,
    HumanRequest,
)
from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcNotification, ServerRequest
from gigas.services._conversation_resources import ConversationResources
from gigas.services.codex import CodexServiceThread, ServiceResult
from gigas.services.conversations import (
    BrowseResult,
    HistoryResult,
    ManagementResult,
    NewConversationTarget,
    StoredConversationTarget,
)
from tests.integration.peers.codex_resume import ResumePeer
from tests.integration.test_codex_service import _mcp_endpoint
from tests.integration.test_conversation_selection import _saved
from tests.unit.test_codex_connection import FakePeer

if TYPE_CHECKING:
    from gigas.runtime.codex.connection import RuntimeEvent
    from gigas.services.codex import ServiceEvent


class _Session:
    """Drive public Qt commands while observing actual service result delivery."""

    def __init__(
        self, qtbot: QtBot, path: Path, peer: FakePeer, reader: CaptureReader | None = None
    ) -> None:
        """Keep result streams and the runtime callback for controlled notifications."""
        self.qtbot = qtbot
        self.peer = peer
        self.results: list[ServiceResult] = []
        self.management: list[ManagementResult] = []
        self.events: list[ServiceEvent] = []
        self.notifications: Callable[[RuntimeEvent], None] = lambda _: None

        def factory(
            options: ConnectionOptions, emit: "Callable[[RuntimeEvent], None]"
        ) -> CodexConnection:
            self.notifications = emit
            return CodexConnection(self.peer, options, emit)

        self.service = CodexServiceThread(path, reader or CaptureReader(CaptureSnapshot), factory)
        self.service.completed.connect(self.results.append)
        self.service.management_completed.connect(self.management.append)
        self.service.received.connect(self.events.append)

    def command(self, operation: str, call: Callable[[], object]) -> ServiceResult:
        """Wait for one newly delivered ordinary operation result."""
        before = sum(item.operation == operation for item in self.results)
        call()
        self.qtbot.waitUntil(
            lambda: sum(item.operation == operation for item in self.results) > before
        )
        return next(item for item in reversed(self.results) if item.operation == operation)

    def manage(self, operation: str, call: Callable[[], object]) -> ManagementResult:
        """Wait for one newly delivered management result."""
        before = sum(item.operation == operation for item in self.management)
        call()
        self.qtbot.waitUntil(
            lambda: sum(item.operation == operation for item in self.management) > before
        )
        return next(item for item in reversed(self.management) if item.operation == operation)

    def notify(self, event: "RuntimeEvent") -> None:
        """Deliver a server event on the service loop and drain preceding callbacks."""
        done = Event()

        def deliver() -> None:
            self.notifications(event)
            done.set()

        self.service._enqueue(deliver)
        self.qtbot.waitUntil(done.is_set)


@contextmanager
def _session(
    qtbot: QtBot, path: Path, peer: FakePeer, reader: CaptureReader | None = None
) -> Generator[_Session]:
    session = _Session(qtbot, path, peer, reader)
    session.service.start()
    try:
        assert not session.command(
            "connect", lambda: session.service.connect_runtime(ConnectionOptions(path / "runtime"))
        ).error
        yield session
    finally:
        session.service.shutdown()
        qtbot.waitUntil(lambda: session.service.wait(0))
        assert not session.service.shutdown_error


def _terminal(thread_id: str = "thread-1") -> RpcNotification:
    return RpcNotification(
        "turn/completed", {"threadId": thread_id, "turn": {"id": "turn-1", "status": "completed"}}
    )


@contextmanager
def _release_after(gate: Event) -> Generator[None]:
    try:
        yield
    finally:
        gate.set()


def test_browse_and_new_selection_do_not_execute_or_pause_saved_active_goal(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = FakePeer()
    peer.authenticated = False
    _saved(peer, tmp_path / "missing")
    with _session(qtbot, tmp_path, peer) as s:
        result = s.manage("select", lambda: s.service.select_conversation("stored-1"))
        assert not result.error
        assert isinstance(result.value, BrowseResult)
        assert result.value.goal == GoalState("ラッキーを捕獲する", "active", 30, 2, 500)
        assert not s.command("stop", s.service.interrupt).error
        new = s.manage("new", lambda: s.service.new_conversation("project-next"))
        assert new.value == NewConversationTarget("project-next")
        assert not new.error
    methods = [method for method, _ in peer.calls]
    assert "thread/start" not in methods
    assert "thread/resume" not in methods
    assert "thread/goal/set" not in methods
    assert "thread/unsubscribe" not in methods
    assert not (tmp_path / "unassigned").exists()
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("status", ["notLoaded", "notSubscribed", "unsubscribed"])
def test_switch_requires_idle_and_releases_old_resources_before_adopting_same_cwd(
    qtbot: QtBot, tmp_path: Path, status: str
) -> None:
    peer = ResumePeer("project-1")
    peer.responses["thread/unsubscribe"] = {"status": status}
    with _session(qtbot, tmp_path, peer) as s:
        assert not s.command("send", lambda: s.service.send("first")).error
        params = next(params for method, params in peer.calls if method == "thread/start")
        assert params is not None
        cwd = Path(str(params["cwd"]))
        _saved(peer, cwd, "stored-2")
        refused = s.manage("select", lambda: s.service.select_conversation("stored-2"))
        assert refused.error
        assert not any(method == "thread/read" for method, _ in peer.calls)
        s.notify(_terminal())
        selected = s.manage("select", lambda: s.service.select_conversation("stored-2"))
        assert not selected.error
        assert not s.service._resources.records_bound
        assert s.service._resources._mcp is None
        assert s.service._conversation is not None
        assert s.service._conversation.snapshot == ConversationStatus()
        assert [(m, p) for m, p in peer.calls if m == "thread/unsubscribe"] == [
            ("thread/unsubscribe", {"threadId": "thread-1"})
        ]
        before = len(s.events)
        s.notify(
            RpcNotification(
                "turn/started",
                {"threadId": "thread-1", "turn": {"id": "late", "status": "inProgress"}},
            )
        )
        s.notify(
            ServerRequest(
                900, "item/tool/requestUserInput", {"threadId": "thread-1", "turnId": "late"}
            )
        )
        s.qtbot.waitUntil(lambda: bool(peer.rejections))
        assert not any(
            isinstance(event.event, ConversationStatus) and event.event.turn_id == "late"
            for event in s.events[before:]
        )
        resumed = s.command("send", lambda: s.service.send("resume selected"))
        assert not resumed.error
        assert resumed.message is not None
        assert resumed.message.outcome == "accepted"
        assert len([method for method, _ in peer.calls if method == "thread/start"]) == 1
        resumed_params = next(p for m, p in peer.calls if m == "thread/resume")
        assert resumed_params is not None
        assert resumed_params["threadId"] == "stored-2"
        assert _mcp_endpoint(resumed_params) != _mcp_endpoint(params)


def test_disconnect_keeps_started_target_and_explicit_execution_resumes_it(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = FakePeer()
    with _session(qtbot, tmp_path, peer) as s:
        assert not s.command("send", lambda: s.service.send("first")).error
        target = s.service._selection.target
        assert isinstance(target, StoredConversationTarget)
        s.notify(
            RpcNotification(
                "thread/goal/updated",
                {
                    "threadId": "thread-1",
                    "goal": {
                        "objective": "saved goal",
                        "status": "active",
                        "tokensUsed": 7,
                        "timeUsedSeconds": 1,
                    },
                },
            )
        )
        assert not s.command("disconnect", s.service.disconnect_runtime).error
        assert s.service._selection.target is target
        assert s.service._selection.goal == GoalState("saved goal", "active", 7, 1)
        s.peer = next_peer = ResumePeer()
        assert not s.command(
            "connect", lambda: s.service.connect_runtime(ConnectionOptions(tmp_path / "runtime"))
        ).error
        assert not s.command("send", lambda: s.service.send("existing")).error
        assert len([m for m, _ in peer.calls if m == "thread/start"]) == 1
        assert len([m for m, _ in next_peer.calls if m == "thread/resume"]) == 1
        assert not any(m == "thread/start" for m, _ in next_peer.calls)
        s.notify(_terminal())
        s.notify(RpcNotification("thread/goal/cleared", {"threadId": "thread-1"}))
        next_peer.responses["thread/unsubscribe"] = {"status": "unsubscribed"}
        assert not s.manage("new", s.service.new_conversation).error
        assert not s.command("send", lambda: s.service.send("new")).error
        assert len([m for m, _ in next_peer.calls if m == "thread/start"]) == 1


def test_browse_model_commits_catalog_choice_without_thread_settings(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = FakePeer()
    _saved(peer, tmp_path / "missing")
    with _session(qtbot, tmp_path, peer) as s:
        assert not s.manage("select", lambda: s.service.select_conversation("stored-1")).error
        assert not s.command("select_model", lambda: s.service.select_model("other", "low")).error
        assert s.command("select_model", lambda: s.service.select_model("not-a-model", "low")).error
        assert s.service._connection is not None
        assert s.service._connection.selection.model == "other"
    assert not any(
        m in {"thread/settings/update", "thread/resume", "thread/start"} for m, _ in peer.calls
    )


def test_model_change_is_rejected_while_new_start_is_waiting_for_its_id(
    qtbot: QtBot, tmp_path: Path
) -> None:
    entered, release = Event(), Event()

    class StartingPeer(FakePeer):
        async def request(self, method: str, params: JsonObject | None) -> JsonValue:
            result = await super().request(method, params)
            if method == "thread/start":
                entered.set()
                assert await asyncio.to_thread(release.wait, 10)
            return result

    peer = StartingPeer()
    with _session(qtbot, tmp_path, peer) as s, _release_after(release):
        s.service.send("first")
        qtbot.waitUntil(entered.is_set)
        result = s.command("select_model", lambda: s.service.select_model("other", "low"))
        assert result.error
        assert s.service._connection is not None
        assert s.service._connection.selection.model == "catalog-default"
        assert s.command("goal_clear", s.service.clear_goal).error
        release.set()
        qtbot.waitUntil(lambda: any(r.operation == "send" for r in s.results))
        assert not next(r.error for r in s.results if r.operation == "send")


@pytest.mark.parametrize("cleared", [False, True])
def test_browse_goal_clear_correlates_global_notification_without_readiness(
    qtbot: QtBot, tmp_path: Path, *, cleared: bool
) -> None:
    peer = FakePeer()
    peer.responses["windowsSandbox/readiness"] = {"status": "notConfigured"}
    peer.responses["model/list"] = {"data": []}
    _saved(peer, tmp_path / "missing")
    peer.responses["thread/goal/clear"] = {"cleared": cleared}
    with _session(qtbot, tmp_path, peer) as s:
        notifications = []
        s.service.browse_goal_changed.connect(
            lambda generation, event: notifications.append((generation, event))
        )
        assert not s.manage("select", lambda: s.service.select_conversation("stored-1")).error
        s.service.clear_goal()
        qtbot.waitUntil(lambda: any(m == "thread/goal/clear" for m, _ in peer.calls))
        if cleared:
            assert not any(r.operation == "goal_clear" for r in s.results)
            s.notify(RpcNotification("thread/goal/cleared", {"threadId": "other"}))
            assert not any(r.operation == "goal_clear" for r in s.results)
            s.notify(RpcNotification("thread/goal/cleared", {"threadId": "stored-1"}))
        qtbot.waitUntil(lambda: any(r.operation == "goal_clear" for r in s.results))
        assert not s.results[-1].error
        assert (s.service._selection.goal is None) == cleared
        if cleared:
            assert notifications[-1][0] == 1
            assert notifications[-1][1].target is s.service.selected_target
            assert notifications[-1][1].goal is None
        assert not s.command("stop", s.service.interrupt).error
    assert not any(m in {"thread/start", "thread/resume", "thread/goal/set"} for m, _ in peer.calls)


def test_unauthenticated_browsing_cannot_clear_a_goal(qtbot: QtBot, tmp_path: Path) -> None:
    peer = FakePeer()
    peer.authenticated = False
    _saved(peer, tmp_path / "missing")
    with _session(qtbot, tmp_path, peer) as s:
        assert not s.manage("select", lambda: s.service.select_conversation("stored-1")).error
        assert s.command("goal_clear", s.service.clear_goal).error
    assert not any(m == "thread/goal/clear" for m, _ in peer.calls)


def test_public_listing_results_keep_scope_and_one_opaque_page(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = FakePeer()
    peer.responses["project/list"] = {"data": [], "nextCursor": "projects:/+?"}
    peer.responses["thread/list"] = {"data": [], "nextCursor": "threads:/+?"}
    with _session(qtbot, tmp_path, peer) as s:
        project = s.manage("projects", lambda: s.service.list_projects(cursor="projects:/+?"))
        thread = s.manage("threads", lambda: s.service.list_threads(None, cursor="threads:/+?"))
        assert project.value == ProjectPage((), "projects:/+?")
        assert thread.value == ThreadPage((), "threads:/+?", None)
        assert thread.project_id is None
        assert s.service.selected_target == NewConversationTarget()
    assert [
        (m, p)
        for m, p in peer.calls
        if m.endswith("/list") and m.startswith(("project/", "thread/"))
    ] == [
        ("project/list", {"cursor": "projects:/+?"}),
        ("thread/list", {"cursor": "threads:/+?", "projectId": None, "modelProviders": []}),
    ]


@pytest.mark.parametrize("close", ["disconnect", "shutdown"])
def test_pending_browse_clear_keeps_stop_available_and_closure_wakes_it(
    qtbot: QtBot, tmp_path: Path, close: str
) -> None:
    peer = FakePeer()
    _saved(peer, tmp_path / "missing")
    with _session(qtbot, tmp_path, peer) as s:
        assert not s.manage("select", lambda: s.service.select_conversation("stored-1")).error
        s.service.clear_goal()
        qtbot.waitUntil(lambda: any(m == "thread/goal/clear" for m, _ in peer.calls))
        assert not any(r.operation == "goal_clear" for r in s.results)
        assert not s.command("stop", s.service.interrupt).error
        assert not any(m == "thread/goal/set" for m, _ in peer.calls)
        if close == "disconnect":
            assert not s.command("disconnect", s.service.disconnect_runtime).error
        else:
            s.service.shutdown()
            qtbot.waitUntil(lambda: s.service.wait(0))
        qtbot.waitUntil(lambda: any(r.operation == "goal_clear" for r in s.results))
        assert next(r.error for r in s.results if r.operation == "goal_clear")


def test_old_mcp_closes_while_answer_delivery_settles_before_selection_result(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release, endpoint_closed = Event(), Event(), Event()
    original_close = GameIoMcpServer.close

    async def close(server: GameIoMcpServer) -> None:
        await original_close(server)
        endpoint_closed.set()

    class AnswerPeer(FakePeer):
        async def respond(self, request_id: str | int, result: JsonValue) -> None:
            await super().respond(request_id, result)
            entered.set()
            assert await asyncio.to_thread(release.wait, 10)

    monkeypatch.setattr(GameIoMcpServer, "close", close)
    peer = AnswerPeer()
    peer.responses["thread/unsubscribe"] = {"status": "unsubscribed"}
    try:
        with _session(qtbot, tmp_path, peer) as s, _release_after(release):
            assert not s.command("send", lambda: s.service.send("first")).error
            params = next(p for m, p in peer.calls if m == "thread/start")
            assert params is not None
            url, headers = _mcp_endpoint(params)
            _saved(peer, Path(str(params["cwd"])), "stored-2")
            s.notify(
                ServerRequest(
                    1,
                    "item/commandExecution/requestApproval",
                    {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "itemId": "command-1",
                        "command": "echo hello",
                        "cwd": str(tmp_path),
                        "startedAtMs": 1000,
                        "availableDecisions": ["accept", "decline"],
                    },
                )
            )
            qtbot.waitUntil(lambda: any(isinstance(e.event, HumanRequest) for e in s.events))
            request = next(e.event for e in s.events if isinstance(e.event, HumanRequest))
            order: list[str] = []
            s.service.completed.connect(lambda result: order.append(result.operation))
            s.service.management_completed.connect(lambda result: order.append(result.operation))
            s.service.answer(request.token, decision="accept")
            qtbot.waitUntil(entered.is_set)
            s.notify(_terminal())
            s.service.select_conversation("stored-2")
            qtbot.waitUntil(endpoint_closed.is_set)
            assert not s.management
            with (
                httpx.Client(headers=headers, trust_env=False, timeout=1) as client,
                pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)),
            ):
                client.post(
                    url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "observe", "arguments": {}},
                    },
                )
            release.set()
            qtbot.waitUntil(lambda: bool(s.management))
            assert not s.management[-1].error
            assert order.index("answer") < order.index("select")
    finally:
        release.set()


def test_failed_resource_release_retains_target_goal_but_publishes_unloaded_execution(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_close = ConversationResources.close
    failed = False

    async def close(resources: ConversationResources) -> None:
        nonlocal failed
        await original_close(resources)
        if not failed:
            failed = True
            reason = "resource close fixture"
            raise OSError(reason)

    monkeypatch.setattr(ConversationResources, "close", close)
    peer = FakePeer()
    peer.responses["thread/unsubscribe"] = {"status": "unsubscribed"}
    with _session(qtbot, tmp_path, peer) as s:
        assert not s.command("send", lambda: s.service.send("first")).error
        old_target = s.service._selection.target
        s.notify(_terminal())
        s.notify(
            RpcNotification(
                "thread/goal/updated",
                {
                    "threadId": "thread-1",
                    "goal": {
                        "objective": "retained",
                        "status": "paused",
                        "tokensUsed": 5,
                        "timeUsedSeconds": 2,
                    },
                },
            )
        )
        _saved(peer, tmp_path / "other", "stored-2")
        result = s.manage("select", lambda: s.service.select_conversation("stored-2"))
        assert result.error
        assert s.service._selection.target is old_target
        assert s.service._selection.goal == GoalState("retained", "paused", 5, 2)
        statuses = [e.event for e in s.events if isinstance(e.event, ConversationStatus)]
        assert statuses[-1] == ConversationStatus()


def test_selection_blocks_submission_but_does_not_block_stop_or_disconnect(
    qtbot: QtBot, tmp_path: Path
) -> None:
    entered, release = Event(), Event()

    class HeldPeer(FakePeer):
        async def request(self, method: str, params: JsonObject | None) -> JsonValue:
            if method == "thread/read":
                entered.set()
                assert await asyncio.to_thread(release.wait, 10)
            return await super().request(method, params)

    peer = HeldPeer()
    _saved(peer, tmp_path / "missing")
    try:
        with _session(qtbot, tmp_path, peer) as s, _release_after(release):
            s.service.select_conversation("stored-1")
            qtbot.waitUntil(entered.is_set)
            assert s.command("send", lambda: s.service.send("blocked")).error
            assert s.command("goal_set", lambda: s.service.set_goal("blocked")).error
            assert s.command("select_model", lambda: s.service.select_model("other", "low")).error
            assert not s.command("stop", s.service.interrupt).error
            assert not s.command("disconnect", s.service.disconnect_runtime).error
            release.set()
            qtbot.waitUntil(lambda: bool(s.management))
            assert s.management[-1].error
            assert s.service._selection.target == NewConversationTarget()
    finally:
        release.set()


@pytest.mark.parametrize("fails", [False, True])
def test_old_history_success_and_failure_are_discarded_after_same_id_is_reselected(
    qtbot: QtBot, tmp_path: Path, *, fails: bool
) -> None:
    entered, release, finished = Event(), Event(), Event()

    class HistoryPeer(FakePeer):
        async def request(self, method: str, params: JsonObject | None) -> JsonValue:
            if method == "thread/turns/list" and params and "cursor" in params:
                entered.set()
                assert await asyncio.to_thread(release.wait, 10)
                if fails:
                    finished.set()
                    reason = "late history failure"
                    raise OSError(reason)
                result = await super().request(method, params)
                finished.set()
                return result
            return await super().request(method, params)

    peer = HistoryPeer()
    _saved(peer, tmp_path / "missing")
    try:
        with _session(qtbot, tmp_path, peer) as s, _release_after(release):
            assert not s.manage("select", lambda: s.service.select_conversation("stored-1")).error
            old_target = s.service._selection.target
            s.service.load_history(cursor="opaque:/+?")
            qtbot.waitUntil(entered.is_set)
            assert not s.manage("new", s.service.new_conversation).error
            assert not s.manage("select", lambda: s.service.select_conversation("stored-1")).error
            assert s.service._selection.target == old_target
            assert s.service._selection.target is not old_target
            release.set()
            qtbot.waitUntil(finished.is_set)
            s.notify(RpcNotification("unrelated", {}))
            latest = s.manage("history", s.service.load_history)
            assert isinstance(latest.value, HistoryResult)
            assert len([r for r in s.management if r.operation == "history"]) == 1
    finally:
        release.set()
