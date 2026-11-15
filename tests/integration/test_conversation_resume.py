import asyncio
from pathlib import Path

import pytest

from gigas.runtime.codex.connection import CodexConnectionError
from gigas.runtime.codex.models import ExecutionSettings, GoalState, HumanRequest, TextEvent
from gigas.runtime.codex.transport import RpcNotification
from gigas.services._conversation_resources import ConversationResources
from gigas.storage.records import GameIoRecordWriter, RecordError
from tests.integration.peers.codex_resume import ResumePeer
from tests.integration.test_conversation_execution import Conversation
from tests.integration.test_conversation_selection import _RequestGate
from tests.unit.test_codex_connection import _run, _until
from tests.unit.test_human_requests import question


def _stored(c: Conversation, cwd: Path, goal: GoalState | None = None) -> None:
    assert isinstance(c.peer, ResumePeer)
    c.selection.adopt_started("thread-1", cwd, c.peer.project_id)
    c.selection.retain_goal("thread-1", goal)
    if c.peer.project_id is not None:
        c.peer.responses["project/read"] = {
            "project": {"id": c.peer.project_id, "name": "Project", "roots": [{"path": str(cwd)}]}
        }
    if goal is not None:
        c.peer.goal = {
            "objective": goal.objective,
            "status": goal.status,
            "tokensUsed": goal.tokens_used,
            "timeUsedSeconds": goal.time_used_seconds,
            "tokenBudget": goal.token_budget,
        }


@pytest.mark.parametrize("project_id", [None, "project-1"])
def test_existing_send_resumes_saved_cwd_with_current_settings_and_same_records(
    tmp_path: Path, project_id: str | None
) -> None:
    cwd = tmp_path / "workspace"
    records = cwd / "records" / "thread-1"
    records.parent.mkdir(parents=True)
    writer = GameIoRecordWriter.create(records)
    writer.record_dialogue("dialogue.submitted", "before")
    writer.close()
    original = (records / "events.jsonl").read_bytes()

    async def scenario() -> None:
        c = Conversation(tmp_path, peer=ResumePeer(project_id))
        _stored(c, cwd)
        await c.connection.open()
        try:
            await c.execution.select_model("other", "low")
            c.execution.set_execution_settings(ExecutionSettings(False, False, "user"))
            assert await c.execution.send("continue") == "turn-1"
            assert c.execution.loaded
            assert await c.execution.send("additional") == "turn-1"
            requests = [(m, p) for m, p in c.peer.calls if m.startswith(("thread/", "turn/"))]
            reads = ["thread/list", "thread/list"] if project_id is not None else []
            assert [m for m, _ in requests] == [*reads, "thread/resume", "turn/start", "turn/start"]
            params = next(p for m, p in requests if m == "thread/resume")
            assert params is not None
            assert (params["threadId"], params["cwd"], params["model"]) == (
                "thread-1",
                str(cwd),
                "other",
            )
            assert params["runtimeWorkspaceRoots"] == [str(cwd)]
            assert params["excludeTurns"] is True
            assert "projectId" not in params
            config = params["config"]
            assert isinstance(config, dict)
            assert config["model_reasoning_effort"] == "low"
            assert config["web_search"] == "disabled"
            assert config["memories.use_memories"] is False
            assert params["approvalsReviewer"] == "user"
            c.resources.record_dialogue("dialogue.message", "after")
        finally:
            await c.close()

    _run(scenario())
    assert (records / "events.jsonl").read_bytes().startswith(original)
    assert b"after" in (records / "events.jsonl").read_bytes()
    assert not (tmp_path / "unassigned").exists()


def test_resume_owns_id_and_records_before_reply_and_keeps_early_goal_and_requests(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "workspace"
    cwd.mkdir()

    async def scenario() -> None:
        peer = ResumePeer()
        gate = peer.gates["thread/resume"] = _RequestGate()
        c = Conversation(tmp_path, peer=peer)
        _stored(c, cwd, GoalState("old", "active", 1))
        await c.connection.open()
        try:
            sending = asyncio.create_task(c.execution.send("after resume"))
            await gate.entered.wait()
            assert c.execution.snapshot.thread_id == "thread-1"
            assert not c.execution.loaded
            assert c.resources.records_bound
            c.turn("turn/started")
            c.receive(
                RpcNotification(
                    "thread/goal/updated",
                    {
                        "threadId": "thread-1",
                        "goal": {
                            "objective": "new fact",
                            "status": "complete",
                            "tokensUsed": 9,
                            "timeUsedSeconds": 2,
                        },
                    },
                )
            )
            c.receive(question("early-question"))
            assert any(isinstance(event, HumanRequest) for event in c.events)
            with pytest.raises(CodexConnectionError):
                await c.execution.select_model("other", "low")
            gate.set()
            assert await sending == "turn-1"
            assert c.execution.snapshot.goal == GoalState("new fact", "complete", 9, 2)
        finally:
            gate.set()
            await c.close()

    _run(scenario())


@pytest.mark.parametrize("operation", ["send", "set_goal", "resume_goal"])
def test_stored_paused_goal_operations_resume_before_applying_the_requested_operation(
    tmp_path: Path, operation: str
) -> None:
    cwd = tmp_path / "workspace"
    cwd.mkdir()

    async def scenario() -> None:
        c = Conversation(tmp_path, peer=ResumePeer())
        _stored(c, cwd, GoalState("retained", "paused", 9, 4, 500))
        await c.connection.open()
        try:
            if operation == "send":
                await c.execution.send("ordinary")
            elif operation == "set_goal":
                await c.execution.set_goal("edited")
            else:
                await c.execution.resume_goal()
            relevant = [(m, p) for m, p in c.peer.calls if m.startswith(("thread/", "turn/"))]
            assert relevant[0][0] == "thread/resume"
            if operation == "send":
                assert relevant[1][0] == "turn/start"
                assert c.execution.snapshot.goal == GoalState("retained", "paused", 9, 4, 500)
            else:
                assert relevant[1][0] == "thread/goal/set"
                expected = (
                    {"threadId": "thread-1", "objective": "edited"}
                    if operation == "set_goal"
                    else {"threadId": "thread-1", "status": "active"}
                )
                expected["origin"] = "user"
                assert relevant[1][1] == expected
                assert c.execution.snapshot.goal is not None
                assert c.execution.snapshot.goal.tokens_used == 9
        finally:
            await c.close()

    _run(scenario())


def test_stop_before_resume_cancels_preparation_without_pausing_the_browsed_goal(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "workspace"
    cwd.mkdir()

    async def scenario() -> None:
        peer = ResumePeer()
        gate = peer.gates["config/read"] = _RequestGate()
        c = Conversation(tmp_path, peer=peer)
        _stored(c, cwd, GoalState("retained", "active"))
        await c.connection.open()
        try:
            sending = asyncio.create_task(c.execution.send("cancel"))
            await gate.entered.wait()
            await c.execution.stop()
            assert await sending is None
            assert c.execution.snapshot.thread_id is None
            assert c.selection.goal == GoalState("retained", "active")
            assert not any(
                m.startswith(("thread/resume", "thread/goal/", "turn/")) for m, _ in peer.calls
            )
        finally:
            gate.set()
            await c.close()

    _run(scenario())


def test_stop_waits_for_sent_resume_and_interrupts_early_turn_without_sending_body(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "workspace"
    cwd.mkdir()

    async def scenario() -> None:
        peer = ResumePeer()
        gate = peer.gates["thread/resume"] = _RequestGate()
        c = Conversation(tmp_path, peer=peer)
        _stored(c, cwd, GoalState("retained", "active"))
        await c.connection.open()
        try:
            sending = asyncio.create_task(c.execution.send("not submitted"))
            await gate.entered.wait()
            stopping = asyncio.create_task(c.execution.stop())
            await _until(lambda: c.execution.snapshot.stopping)
            c.turn("turn/started", "autonomous-1")
            assert not sending.done()
            assert not stopping.done()
            gate.set()
            assert await sending is None
            await stopping
            assert c.execution.loaded
            assert c.execution.snapshot.goal == GoalState("retained", "paused")
            assert c.execution.snapshot.turn_id is None
            assert not any(m == "turn/start" for m, _ in peer.calls)
            assert not any(
                isinstance(event, TextEvent) and event.role == "user" for event in c.events
            )
            assert [(m, p) for m, p in peer.calls if m == "turn/interrupt"] == [
                ("turn/interrupt", {"threadId": "thread-1", "turnId": "autonomous-1"})
            ]
        finally:
            gate.set()
            await c.close()

    _run(scenario())


@pytest.mark.parametrize("failure", ["rpc", "response", "transport"])
def test_sent_resume_failure_retains_id_but_requires_reconnect_before_new_execution(
    tmp_path: Path, failure: str
) -> None:
    cwd = tmp_path / "workspace"
    cwd.mkdir()

    async def scenario() -> None:
        peer = ResumePeer()
        if failure == "rpc":
            peer.rpc_errors["thread/resume"] = -32602
        elif failure == "response":
            peer.responses["thread/resume"] = {"thread": {"id": "wrong"}}
        else:
            peer.failure = "thread/resume"
        c = Conversation(tmp_path, peer=peer)
        _stored(c, cwd)
        await c.connection.open()
        try:
            with pytest.raises(CodexConnectionError):
                await c.execution.send("not submitted")
            assert c.execution.snapshot.thread_id == "thread-1"
            assert not c.execution.loaded
            with pytest.raises(CodexConnectionError):
                await c.execution.send("manual retry")
            with pytest.raises(CodexConnectionError):
                await c.execution.select_model("other", "low")
            assert len([m for m, _ in peer.calls if m == "thread/resume"]) == 1
            assert not any(
                m in {"thread/start", "turn/start", "thread/settings/update"} for m, _ in peer.calls
            )
        finally:
            await c.close()

    _run(scenario())


def test_missing_workspace_does_not_resume_or_erase_browsed_goal(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path, peer=ResumePeer())
        target = tmp_path / "missing"
        _stored(c, target, GoalState("retained", "paused"))
        await c.connection.open()
        try:
            with pytest.raises(CodexConnectionError):
                await c.execution.send("cannot prepare")
            assert c.execution.snapshot.thread_id is None
            assert c.selection.goal == GoalState("retained", "paused")
            assert not target.exists()
            assert not any(m == "thread/resume" for m, _ in c.peer.calls)
        finally:
            await c.close()

    _run(scenario())


def test_bind_failure_can_retry_before_resume_and_keeps_browsed_goal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    bind = ConversationResources.bind_records
    attempts = 0

    async def fail_once(resources: ConversationResources, thread_id: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            message = "cannot open records"
            raise RecordError(message)
        await bind(resources, thread_id)

    monkeypatch.setattr(ConversationResources, "bind_records", fail_once)

    async def scenario() -> None:
        c = Conversation(tmp_path, peer=ResumePeer())
        _stored(c, cwd, GoalState("retained", "paused"))
        await c.connection.open()
        try:
            with pytest.raises(CodexConnectionError):
                await c.execution.send("not submitted")
            assert c.execution.snapshot.thread_id is None
            assert c.selection.goal == GoalState("retained", "paused")
            assert not any(m == "thread/resume" for m, _ in c.peer.calls)
            assert await c.execution.send("retry") == "turn-1"
            assert c.execution.snapshot.goal == GoalState("retained", "paused")
            assert len([m for m, _ in c.peer.calls if m == "thread/resume"]) == 1
        finally:
            await c.close()

    _run(scenario())


def test_resume_uses_settings_captured_before_preparation_and_keeps_them_while_loaded(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "workspace"
    cwd.mkdir()

    async def scenario() -> None:
        peer = ResumePeer()
        gate = peer.gates["config/read"] = _RequestGate()
        c = Conversation(tmp_path, peer=peer)
        _stored(c, cwd)
        await c.connection.open()
        try:
            sending = asyncio.create_task(c.execution.send("current settings"))
            await gate.entered.wait()
            c.execution.set_execution_settings(ExecutionSettings(False, False, "user"))
            gate.set()
            await sending
            await c.execution.send("same loaded settings")
            params = next(p for m, p in peer.calls if m == "thread/resume")
            assert params is not None
            config = params["config"]
            assert isinstance(config, dict)
            assert config["web_search"] == "live"
            assert config["memories.use_memories"] is True
            assert params["approvalsReviewer"] == "auto_review"
            assert len([m for m, _ in peer.calls if m == "thread/resume"]) == 1
            assert not any(m == "thread/settings/update" for m, _ in peer.calls)
        finally:
            gate.set()
            await c.close()

    _run(scenario())


def test_goal_clear_after_unvalidated_resume_needs_no_execution_readiness(tmp_path: Path) -> None:
    cwd = tmp_path / "workspace"
    cwd.mkdir()

    async def scenario() -> None:
        peer = ResumePeer()
        peer.responses["thread/resume"] = {"thread": {"id": "wrong"}}
        c = Conversation(tmp_path, peer=peer)
        _stored(c, cwd, GoalState("retained", "active"))
        await c.connection.open()
        try:
            with pytest.raises(CodexConnectionError):
                await c.execution.send("not submitted")
            peer.responses["windowsSandbox/readiness"] = {"status": "notReady"}
            await c.execution.clear_goal()
            assert c.execution.snapshot.goal is None
            assert not c.execution.loaded
            assert len([m for m, _ in peer.calls if m == "thread/resume"]) == 1
            assert not any(m == "thread/goal/set" for m, _ in peer.calls)
        finally:
            await c.close()

    _run(scenario())
