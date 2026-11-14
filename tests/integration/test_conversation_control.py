import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path

import pytest

from gigas.game_io.input.models import InputState
from gigas.runtime.codex.connection import (
    AnswerDeliveryError,
    CodexConnectionError,
    MessageDeliveryError,
)
from gigas.runtime.codex.models import (
    ConversationStatus,
    DialogueEvent,
    GoalStatus,
    HumanRequest,
    RequestResolved,
    UsageEvent,
)
from gigas.runtime.codex.transport import (
    JsonObject,
    JsonValue,
    RpcId,
    RpcNotification,
    TransportError,
)
from gigas.services.input import OperationResult
from tests.integration.test_conversation_execution import Conversation
from tests.integration.test_input_service import Device, press
from tests.unit.test_human_requests import approval, question


def run(scenario: Coroutine[object, object, None]) -> None:
    async def bounded() -> None:
        async with asyncio.timeout(5):
            await scenario

    asyncio.run(bounded())


def notify_goal(conversation: Conversation, state: JsonObject | None) -> None:
    conversation.execution.receive(
        RpcNotification(
            "thread/goal/updated" if state is not None else "thread/goal/cleared",
            {"threadId": "thread-1", "goal": state},
        )
    )


def goal(
    conversation: Conversation,
    status: GoalStatus,
    objective: str = "タマムシシティへ",
    *,
    tokens: int = 4,
    budget: int | None = 10,
) -> JsonObject:
    state: JsonObject = {
        "threadId": "thread-1",
        "objective": objective,
        "status": status,
        "tokensUsed": tokens,
        "timeUsedSeconds": 1,
        "tokenBudget": budget,
    }
    conversation.peer.goal = dict(state)
    notify_goal(conversation, state)
    return state


def calls(conversation: Conversation, method: str) -> list[JsonObject | None]:
    return [params for called, params in conversation.peer.calls if called == method]


async def until(condition: Callable[[], bool]) -> None:
    while not condition():  # noqa: ASYNC110 -- observing RPC calls within run's deadline
        await asyncio.sleep(0)


def test_pending_message_and_goal_completion_preserve_input_and_original_answer(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        device = Device()
        reply = asyncio.Event()
        sending: asyncio.Task[str | None] | None = None
        await c.connection.open()
        await c.inputs.connect(device)
        try:
            await c.execution.send("進めて")
            goal(c, "active")
            started = c.execution.start_input((press(duration=10**9),))
            assert isinstance(started, tuple)
            await device.pressed.wait()
            c.peer.auto_turn_started = False
            c.peer.gates["turn/start"] = reply
            sending = asyncio.create_task(c.execution.send("到着したら周囲を確認して"))
            await until(lambda: len(calls(c, "turn/start")) == 2)
            pending_question = question("route-question")
            assert pending_question.params is not None
            pending_question.params["isBlocking"] = True
            c.execution.receive(pending_question)
            request = next(event for event in c.events if isinstance(event, HumanRequest))
            goal(c, "complete")
            usage = UsageEvent(ordinary_usage_allowed=False, available=True)
            c.execution.receive(usage)
            assert c.execution.snapshot.waiting_for_answer
            assert c.execution.snapshot.sending
            assert usage in c.events

            await c.execution.answer(request.token, answers={"q": "右"})
            assert c.peer.replies == [("route-question", {"answers": {"q": {"answers": ["右"]}}})]
            assert not sending.done()
            assert not started[1].done()
            assert device.calls == ["open", InputState(buttons=("A",))]
            assert calls(c, "turn/interrupt") == []
            assert calls(c, "thread/goal/set") == []
            reply.set()
            assert await sending == "turn-1"
            assert not started[1].done()
            assert c.execution.snapshot.goal is not None
            assert c.execution.snapshot.goal.status == "complete"
        finally:
            reply.set()
            await c.close()
            if sending is not None:
                await asyncio.gather(sending, return_exceptions=True)
            await c.inputs.close()

    run(scenario())


def test_goal_creation_waits_for_notifications_and_edit_sends_only_objective(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        try:
            creating = asyncio.create_task(c.execution.set_goal("タマムシシティへ"))
            await until(lambda: bool(calls(c, "thread/goal/set")))
            assert c.execution.snapshot.goal is None
            assert calls(c, "thread/goal/set") == [
                {
                    "threadId": "thread-1",
                    "objective": "タマムシシティへ",
                    "status": "active",
                    "origin": "user",
                }
            ]
            notify_goal(c, c.peer.goal)
            assert await creating
            c.turn("turn/started")
            original = c.execution.snapshot.goal
            editing = asyncio.create_task(c.execution.set_goal("クチバシティへ"))
            await until(lambda: len(calls(c, "thread/goal/set")) == 2)
            assert calls(c, "thread/goal/set")[-1] == {
                "threadId": "thread-1",
                "objective": "クチバシティへ",
                "origin": "user",
            }
            assert c.execution.snapshot.goal == original
            notify_goal(c, c.peer.goal)
            assert await editing
            assert c.execution.snapshot.goal is not None
            assert c.execution.snapshot.goal.objective == "クチバシティへ"
            assert len(calls(c, "thread/start")) == 1
            assert calls(c, "turn/start") == []
            assert calls(c, "turn/interrupt") == []
        finally:
            await c.close()

    run(scenario())


def test_failed_turn_and_autonomous_next_turn_preserve_running_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        device = Device()
        await c.connection.open()
        await c.inputs.connect(device)
        try:
            await c.execution.send("進めて")
            goal(c, "active")
            started = c.execution.start_input((press(duration=10**9),))
            assert isinstance(started, tuple)
            await device.pressed.wait()
            c.execution.receive(
                RpcNotification(
                    "turn/completed",
                    {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "failed"}},
                )
            )
            assert DialogueEvent("turn_completed", {"status": "failed"}, "turn-1") in c.events
            assert c.execution.snapshot.turn_id is None
            c.turn("turn/started", "turn-2")
            goal(c, "paused")
            await asyncio.sleep(0)  # Allow a cancellation, if incorrectly scheduled, to run.
            assert c.execution.snapshot.turn_id == "turn-2"
            assert not c.execution.snapshot.stopping
            assert not started[1].done()
            assert device.calls == ["open", InputState(buttons=("A",))]
            assert len(calls(c, "turn/start")) == 1
            assert calls(c, "turn/interrupt") == []
            assert calls(c, "thread/goal/set") == []
        finally:
            await c.close()
            await c.inputs.close()

    run(scenario())


def test_budget_limited_resume_can_be_requested_again_without_replacing_objective(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        try:
            await c.execution.send("画面を確認して")
            limited = goal(c, "budgetLimited", tokens=10)
            c.peer.responses["thread/goal/set"] = {"goal": limited}
            original = c.execution.snapshot.goal
            assert await c.execution.resume_goal()
            assert c.execution.snapshot.goal == original
            assert not c.execution.snapshot.sending
            assert await c.execution.resume_goal()
            assert calls(c, "thread/goal/set") == [
                {"threadId": "thread-1", "status": "active", "origin": "user"},
                {"threadId": "thread-1", "status": "active", "origin": "user"},
            ]
            assert c.execution.snapshot.goal == original
        finally:
            await c.close()

    run(scenario())


def test_clear_goal_does_not_interrupt_turn_or_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        device = Device()
        await c.connection.open()
        await c.inputs.connect(device)
        try:
            await c.execution.send("進めて")
            goal(c, "active")
            c.turn("turn/started")
            started = c.execution.start_input((press(duration=10**9),))
            assert isinstance(started, tuple)
            await device.pressed.wait()
            clearing = asyncio.create_task(c.execution.clear_goal())
            await until(lambda: bool(calls(c, "thread/goal/clear")))
            assert c.execution.snapshot.goal is not None
            notify_goal(c, None)
            await clearing
            assert c.execution.snapshot.goal is None
            assert c.execution.snapshot.turn_id == "turn-1"
            assert not started[1].done()
            assert calls(c, "turn/interrupt") == []
            assert calls(c, "thread/goal/clear") == [{"threadId": "thread-1", "origin": "user"}]
        finally:
            await c.close()
            await c.inputs.close()

    run(scenario())


def test_clear_without_a_server_goal_does_not_wait_for_a_notification(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        try:
            await c.execution.send("確認して")
            goal(c, "paused")
            c.peer.responses["thread/goal/clear"] = {"cleared": False}
            await asyncio.wait_for(c.execution.clear_goal(), 0.5)
            assert not c.execution.snapshot.sending
            assert await c.execution.send("次の指示") == "turn-1"
            assert calls(c, "turn/interrupt") == []
        finally:
            await c.close()

    run(scenario())


def test_changed_approval_updates_answer_waiting_projection(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        try:
            await c.execution.send("記録して")
            c.turn("turn/started")

            def patch(path: str) -> None:
                c.execution.receive(
                    RpcNotification(
                        "item/fileChange/patchUpdated",
                        {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "itemId": "item-1",
                            "changes": [{"path": path, "diff": "+record", "kind": {"type": "add"}}],
                        },
                    )
                )

            patch("first.md")
            c.execution.receive(approval("approval", "file"))
            request = next(event for event in c.events if isinstance(event, HumanRequest))
            assert c.execution.snapshot.waiting_for_answer
            patch("second.md")
            assert RequestResolved(request.token) in c.events
            published = [event for event in c.events if isinstance(event, ConversationStatus)]
            assert not published[-1].waiting_for_answer
            with pytest.raises(CodexConnectionError):
                await c.execution.answer(request.token, decision="accept")
        finally:
            await c.close()

    run(scenario())


class ReleaseDevice(Device):
    """Let the test observe release beginning before completing its write."""

    def __init__(self) -> None:
        """Start with neutral release blocked until explicitly allowed."""
        super().__init__()
        self.releasing = asyncio.Event()
        self.release = asyncio.Event()

    async def send(self, state: InputState) -> None:
        """Record each write and let the test delay neutral completion."""
        await super().send(state)
        if state == InputState():
            self.releasing.set()
            await self.release.wait()


def test_repeated_stop_waits_for_goal_turn_and_input_release_then_allows_next_input(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        device = ReleaseDevice()
        await c.connection.open()
        await c.inputs.connect(device)
        try:
            await c.execution.send("進めて")
            goal(c, "active")
            c.turn("turn/started")
            started = c.execution.start_input((press(duration=10**9), press("B")))
            assert isinstance(started, tuple)
            await device.pressed.wait()
            first = asyncio.create_task(c.execution.stop())
            await device.releasing.wait()
            second = asyncio.create_task(c.execution.stop())
            await until(lambda: bool(calls(c, "turn/interrupt")))
            await until(lambda: bool(calls(c, "thread/goal/set")))
            assert calls(c, "turn/interrupt") == [{"threadId": "thread-1", "turnId": "turn-1"}]
            assert calls(c, "thread/goal/set") == [
                {"threadId": "thread-1", "status": "paused", "origin": "user"}
            ]
            refused = c.execution.start_input((press("B"),))
            assert isinstance(refused, OperationResult)
            assert refused.code == "INPUT_DISABLED"
            assert not first.done()
            goal(c, "paused")
            c.turn("turn/completed")
            assert not first.done()
            device.release.set()
            await asyncio.gather(first, second)
            result = await started[1]
            assert result.code == "INTERRUPTED"
            assert result.neutral == "sent"
            assert not c.execution.snapshot.stopping
            assert c.execution.snapshot.goal is not None
            assert c.execution.snapshot.goal.status == "paused"
            following = c.execution.start_input((press("B"),))
            assert isinstance(following, tuple)
            assert (await following[1]).code == "OK"
            assert len(calls(c, "turn/interrupt")) == 1
        finally:
            device.release.set()
            await c.close()
            await c.inputs.close()

    run(scenario())


@pytest.mark.parametrize(
    "status", ["paused", "blocked", "complete", "budgetLimited", "usageLimited"]
)
def test_stop_preserves_nonactive_goal_and_still_interrupts_current_turn(
    tmp_path: Path, status: GoalStatus
) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        try:
            await c.execution.send("確認して")
            goal(c, status)
            c.turn("turn/started")
            original = c.execution.snapshot.goal
            stopping = asyncio.create_task(c.execution.stop())
            await until(lambda: bool(calls(c, "turn/interrupt")))
            assert not stopping.done()
            c.turn("turn/completed")
            await stopping
            assert c.execution.snapshot.goal == original
            assert calls(c, "thread/goal/set") == []
        finally:
            await c.close()

    run(scenario())


def test_explicit_close_releases_stop_waiting_only_for_terminal_notification(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        try:
            await c.execution.send("確認して")
            c.turn("turn/started")
            stopping = asyncio.create_task(c.execution.stop())
            await until(lambda: bool(calls(c, "turn/interrupt")))
            await asyncio.sleep(0)
            assert not stopping.done()
            await c.close()
            with pytest.raises(CodexConnectionError):
                await stopping
            assert c.peer.closed
            assert not c.execution.snapshot.stopping
            assert c.peer.close_calls == 1
        finally:
            await c.close()

    run(scenario())


def test_stop_interrupts_known_and_autonomous_turns_while_goal_change_is_pending(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        resume_reply = asyncio.Event()
        pause_reply = asyncio.Event()
        await c.connection.open()
        try:
            await c.execution.send("画面を見て")
            goal(c, "paused")
            c.turn("turn/started")
            c.peer.gates["thread/goal/set"] = resume_reply
            resuming = asyncio.create_task(c.execution.resume_goal())
            await until(lambda: len(calls(c, "thread/goal/set")) == 1)
            stopping = asyncio.create_task(c.execution.stop())
            await until(lambda: len(calls(c, "turn/interrupt")) == 1)
            assert not resuming.done()
            assert not stopping.done()
            assert calls(c, "turn/interrupt")[0] == {"threadId": "thread-1", "turnId": "turn-1"}
            resume_reply.set()
            await until(lambda: c.peer.goal is not None and c.peer.goal.get("status") == "active")
            c.peer.gates["thread/goal/set"] = pause_reply
            notify_goal(c, c.peer.goal)
            assert await resuming
            await until(lambda: len(calls(c, "thread/goal/set")) == 2)
            c.turn("turn/completed")
            c.turn("turn/started", "turn-2")
            await until(lambda: len(calls(c, "turn/interrupt")) == 2)
            assert calls(c, "turn/interrupt")[1] == {"threadId": "thread-1", "turnId": "turn-2"}
            assert not stopping.done()
            goal(c, "paused")
            pause_reply.set()
            c.turn("turn/completed", "turn-2")
            await stopping
            assert not c.execution.snapshot.stopping
            assert c.execution.snapshot.goal is not None
            assert c.execution.snapshot.goal.status == "paused"
            assert calls(c, "thread/goal/set") == [
                {"threadId": "thread-1", "status": "active", "origin": "user"},
                {"threadId": "thread-1", "status": "paused", "origin": "user"},
            ]
        finally:
            resume_reply.set()
            pause_reply.set()
            await c.close()

    run(scenario())


def test_server_interrupt_failure_closes_without_waiting_on_its_own_stop(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        device = Device()
        await c.connection.open()
        await c.inputs.connect(device)
        try:
            await c.execution.send("進めて")
            c.turn("turn/started")
            started = c.execution.start_input((press(duration=10**9),))
            assert isinstance(started, tuple)
            await device.pressed.wait()
            c.peer.rpc_errors["turn/interrupt"] = -32000
            with pytest.raises(CodexConnectionError, match="停止"):
                await c.execution.stop()
            assert c.peer.closed
            assert c.peer.close_calls == 1
            assert not c.execution.snapshot.stopping
            assert (await started[1]).neutral == "sent"
        finally:
            await c.close()
            await c.inputs.close()

    run(scenario())


def test_input_release_failure_does_not_disable_ordinary_sending(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        device = Device()
        await c.connection.open()
        await c.inputs.connect(device)
        try:
            await c.execution.send("進めて")
            c.turn("turn/started")
            started = c.execution.start_input((press(duration=10**9),))
            assert isinstance(started, tuple)
            await device.pressed.wait()
            device.fail_neutral = True
            stopping = asyncio.create_task(c.execution.stop())
            await until(lambda: bool(calls(c, "turn/interrupt")))
            c.turn("turn/completed")
            with pytest.raises(CodexConnectionError, match="injected send failure"):
                await stopping
            assert not c.execution.snapshot.stopping
            assert not c.peer.closed
            assert (await started[1]).neutral == "unconfirmed"
            assert await c.execution.send("状況を説明して") == "turn-1"
            refused = c.execution.start_input((press(),))
            assert isinstance(refused, OperationResult)
            assert refused.code == "INPUT_DISABLED"
        finally:
            await c.close()
            await c.inputs.close()

    run(scenario())


def test_stop_goal_pause_rejection_closes_and_recovers_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        device = Device()
        await c.connection.open()
        await c.inputs.connect(device)
        try:
            await c.execution.send("進めて")
            original = goal(c, "active")
            started = c.execution.start_input((press(duration=10**9), press("B")))
            assert isinstance(started, tuple)
            await device.pressed.wait()
            c.peer.rpc_errors["thread/goal/set"] = -32000
            with pytest.raises(CodexConnectionError, match="停止"):
                await c.execution.stop()
            assert calls(c, "thread/goal/set") == [
                {"threadId": "thread-1", "status": "paused", "origin": "user"}
            ]
            assert calls(c, "thread/goal/clear") == []
            assert calls(c, "turn/interrupt") == [{"threadId": "thread-1", "turnId": "turn-1"}]
            assert c.peer.goal == original
            assert c.peer.closed
            assert c.peer.close_calls == 1
            assert not c.execution.snapshot.stopping
            result = await started[1]
            assert result.code == "INTERRUPTED"
            assert result.neutral == "sent"
            assert device.calls == ["open", InputState(buttons=("A",)), InputState()]
        finally:
            await c.close()
            await c.inputs.close()

    run(scenario())


def test_stop_collects_late_old_turn_terminal_without_erasing_new_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        try:
            await c.execution.send("画面を確認して")
            c.turn("turn/started")
            stopping = asyncio.create_task(c.execution.stop())
            await until(lambda: len(calls(c, "turn/interrupt")) == 1)
            c.turn("turn/started", "turn-2")
            await until(lambda: len(calls(c, "turn/interrupt")) == 2)
            c.turn("turn/completed", "turn-1")
            assert c.execution.snapshot.turn_id == "turn-2"
            assert not stopping.done()
            c.turn("turn/completed", "turn-2")
            await stopping
            assert c.execution.snapshot.turn_id is None
            assert not c.execution.snapshot.stopping
            assert calls(c, "turn/interrupt") == [
                {"threadId": "thread-1", "turnId": "turn-1"},
                {"threadId": "thread-1", "turnId": "turn-2"},
            ]
        finally:
            await c.close()

    run(scenario())


@pytest.mark.parametrize("status", ["paused", "complete"])
def test_edit_retains_nonactive_status_budget_and_usage(tmp_path: Path, status: GoalStatus) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        try:
            await c.execution.send("画面を確認して")
            goal(c, status)
            original = c.execution.snapshot.goal
            editing = asyncio.create_task(c.execution.set_goal("クチバシティへ"))
            await until(lambda: bool(calls(c, "thread/goal/set")))
            assert c.execution.snapshot.goal == original
            assert calls(c, "thread/goal/set") == [
                {"threadId": "thread-1", "objective": "クチバシティへ", "origin": "user"}
            ]
            notify_goal(c, c.peer.goal)
            assert await editing
            changed = c.execution.snapshot.goal
            assert changed is not None
            assert changed.objective == "クチバシティへ"
            assert changed.status == status
            assert changed.tokens_used == 4
            assert changed.token_budget == 10
            assert calls(c, "thread/goal/clear") == []
        finally:
            await c.close()

    run(scenario())


def test_resume_rejection_preserves_server_state_and_allows_another_request(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        try:
            await c.execution.send("確認して")
            limited = goal(c, "budgetLimited", tokens=10)
            original = c.execution.snapshot.goal
            c.peer.rpc_errors["thread/goal/set"] = -32000
            with pytest.raises(CodexConnectionError, match="拒否"):
                await c.execution.resume_goal()
            assert c.execution.snapshot.goal == original
            assert not c.execution.snapshot.sending
            assert not c.peer.closed
            del c.peer.rpc_errors["thread/goal/set"]
            c.peer.responses["thread/goal/set"] = {"goal": limited}
            assert await c.execution.resume_goal()
            assert c.execution.snapshot.goal == original
            assert len(calls(c, "thread/goal/set")) == 2
        finally:
            await c.close()

    run(scenario())


def test_late_active_reply_does_not_overwrite_later_limit_notification(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        reply = asyncio.Event()
        await c.connection.open()
        try:
            await c.execution.send("確認して")
            paused = goal(c, "paused")
            active = dict(paused, status="active")
            c.peer.responses["thread/goal/set"] = {"goal": active}
            c.peer.gates["thread/goal/set"] = reply
            resuming = asyncio.create_task(c.execution.resume_goal())
            await until(lambda: bool(calls(c, "thread/goal/set")))
            notify_goal(c, active)
            limited = goal(c, "usageLimited")
            reply.set()
            assert await resuming
            assert c.execution.snapshot.goal is not None
            assert c.execution.snapshot.goal.status == "usageLimited"
            c.peer.responses["thread/goal/set"] = {"goal": limited}
            assert await c.execution.resume_goal()
            assert c.execution.snapshot.goal.status == "usageLimited"
        finally:
            reply.set()
            await c.close()

    run(scenario())


def test_goal_and_ordinary_commands_share_one_submission_slot(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        reply = asyncio.Event()
        await c.connection.open()
        try:
            await c.execution.send("確認して")
            goal(c, "paused")
            c.peer.gates["thread/goal/set"] = reply
            resuming = asyncio.create_task(c.execution.resume_goal())
            await until(lambda: bool(calls(c, "thread/goal/set")))
            with pytest.raises(CodexConnectionError):
                await c.execution.send("右へ")
            with pytest.raises(CodexConnectionError):
                await c.execution.clear_goal()
            assert len(calls(c, "turn/start")) == 1
            assert calls(c, "thread/goal/clear") == []
            reply.set()
            await until(lambda: c.peer.goal is not None and c.peer.goal.get("status") == "active")
            notify_goal(c, c.peer.goal)
            assert await resuming
        finally:
            reply.set()
            await c.close()

    run(scenario())


def test_disconnect_releases_goal_waiting_for_its_notification(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        resuming: asyncio.Task[bool] | None = None
        await c.connection.open()
        try:
            await c.execution.send("確認して")
            goal(c, "paused")
            original = c.execution.snapshot.goal
            resuming = asyncio.create_task(c.execution.resume_goal())
            await until(lambda: c.peer.goal is not None and c.peer.goal.get("status") == "active")
            # The RPC has returned active; the corresponding notification is still absent.
            assert not resuming.done()
            assert c.execution.snapshot.sending
            assert c.execution.snapshot.goal == original
            await c.close()
            with pytest.raises(MessageDeliveryError, match="通知"):
                await resuming
            assert not c.execution.snapshot.sending
            assert c.execution.snapshot.goal == original
            assert c.peer.closed
            assert c.peer.close_calls == 1
            assert len(calls(c, "thread/goal/set")) == 1
        finally:
            await c.close()
            if resuming is not None:
                await asyncio.gather(resuming, return_exceptions=True)

    run(scenario())


def test_resume_text_is_an_ordinary_message_for_a_paused_goal(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        try:
            await c.execution.send("確認して")
            goal(c, "paused")
            c.turn("turn/started")
            c.turn("turn/completed")
            original = c.execution.snapshot.goal
            assert await c.execution.send("再開して") == "turn-1"
            assert c.execution.snapshot.goal == original
            assert len(calls(c, "turn/start")) == 2
            assert calls(c, "thread/goal/set") == []
        finally:
            await c.close()

    run(scenario())


def test_stop_closes_if_inflight_goal_change_has_unknown_acceptance(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        reply = asyncio.Event()
        await c.connection.open()
        try:
            await c.execution.send("確認して")
            goal(c, "paused")
            c.turn("turn/started")
            c.peer.gates["thread/goal/set"] = reply
            resuming = asyncio.create_task(c.execution.resume_goal())
            await until(lambda: bool(calls(c, "thread/goal/set")))
            stopping = asyncio.create_task(c.execution.stop())
            await until(lambda: bool(calls(c, "turn/interrupt")))
            c.turn("turn/completed")
            c.peer.failure = "thread/goal/set"
            reply.set()
            with pytest.raises(MessageDeliveryError):
                await resuming
            with pytest.raises(CodexConnectionError, match="停止"):
                await stopping
            assert c.peer.closed
            assert c.peer.close_calls == 1
        finally:
            reply.set()
            await c.close()

    run(scenario())


def test_stop_closes_if_an_inflight_answer_has_unknown_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        entered = asyncio.Event()
        reply = asyncio.Event()

        async def respond(_request_id: RpcId, _payload: JsonValue) -> None:
            entered.set()
            await reply.wait()
            message = "response write failed"
            raise TransportError(message)

        monkeypatch.setattr(c.peer, "respond", respond)
        await c.connection.open()
        try:
            await c.execution.send("確認して")
            c.turn("turn/started")
            c.execution.receive(question("question-1"))
            request = next(event for event in c.events if isinstance(event, HumanRequest))
            answering = asyncio.create_task(c.execution.answer(request.token, answers={"q": "右"}))
            await entered.wait()
            stopping = asyncio.create_task(c.execution.stop())
            await until(lambda: bool(calls(c, "turn/interrupt")))
            c.turn("turn/completed")
            reply.set()
            with pytest.raises(AnswerDeliveryError):
                await answering
            with pytest.raises(CodexConnectionError, match="停止"):
                await stopping
            assert c.peer.closed
        finally:
            reply.set()
            await c.close()

    run(scenario())


def test_interrupt_failure_closes_while_goal_response_is_still_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        reply = asyncio.Event()
        original_close = c.peer.close

        async def close() -> None:
            # A real transport close fails pending RPCs. Unblock this one scripted RPC.
            c.peer.failure = "thread/goal/set"
            reply.set()
            await original_close()

        monkeypatch.setattr(c.peer, "close", close)
        await c.connection.open()
        try:
            await c.execution.send("確認して")
            goal(c, "paused")
            c.turn("turn/started")
            c.peer.gates["thread/goal/set"] = reply
            resuming = asyncio.create_task(c.execution.resume_goal())
            await until(lambda: bool(calls(c, "thread/goal/set")))
            c.peer.rpc_errors["turn/interrupt"] = -32000
            stopping = asyncio.create_task(c.execution.stop())
            with pytest.raises(CodexConnectionError, match="停止"):
                await stopping
            with pytest.raises(MessageDeliveryError):
                await resuming
            assert c.peer.closed
            assert c.peer.close_calls == 1
        finally:
            reply.set()
            await c.close()

    run(scenario())


def test_explicit_goal_rejection_during_stop_does_not_require_connection_close(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        reply = asyncio.Event()
        await c.connection.open()
        try:
            await c.execution.send("確認して")
            goal(c, "paused")
            c.turn("turn/started")
            c.peer.gates["thread/goal/set"] = reply
            c.peer.rpc_errors["thread/goal/set"] = -32000
            resuming = asyncio.create_task(c.execution.resume_goal())
            await until(lambda: bool(calls(c, "thread/goal/set")))
            stopping = asyncio.create_task(c.execution.stop())
            await until(lambda: bool(calls(c, "turn/interrupt")))
            c.turn("turn/completed")
            reply.set()
            with pytest.raises(CodexConnectionError, match="拒否"):
                await resuming
            await stopping
            assert not c.peer.closed
            assert c.execution.snapshot.goal is not None
            assert c.execution.snapshot.goal.status == "paused"
            assert await c.execution.send("状況を説明して") == "turn-1"
        finally:
            reply.set()
            await c.close()

    run(scenario())


def test_stop_cancels_unsubmitted_answer_without_treating_it_as_unknown_delivery(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        try:
            await c.execution.send("確認して")
            c.turn("turn/started")
            c.execution.receive(question("question-1"))
            request = next(event for event in c.events if isinstance(event, HumanRequest))
            answering = asyncio.create_task(c.execution.answer(request.token, answers={"q": "右"}))
            stopping = asyncio.create_task(c.execution.stop())
            await until(lambda: bool(calls(c, "turn/interrupt")))
            c.turn("turn/completed")
            await stopping
            with pytest.raises(CodexConnectionError) as failure:
                await answering
            assert not isinstance(failure.value, AnswerDeliveryError)
            assert c.peer.replies == []
            assert not c.peer.closed
            assert not c.execution.snapshot.stopping
        finally:
            await c.close()

    run(scenario())


def test_turn_started_during_input_release_is_collected_before_stop_completes(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        device = ReleaseDevice()
        stopping: asyncio.Task[None] | None = None
        await c.connection.open()
        await c.inputs.connect(device)
        try:
            await c.execution.send("進めて")
            c.turn("turn/started")
            started = c.execution.start_input((press(duration=10**9),))
            assert isinstance(started, tuple)
            await device.pressed.wait()
            # Natural turn completion leaves the already-started operation alone.
            c.turn("turn/completed")
            stopping = asyncio.create_task(c.execution.stop())
            await device.releasing.wait()
            assert c.execution.snapshot.stopping
            assert calls(c, "turn/interrupt") == []
            c.turn("turn/started", "turn-2")
            await until(lambda: bool(calls(c, "turn/interrupt")))
            assert calls(c, "turn/interrupt") == [{"threadId": "thread-1", "turnId": "turn-2"}]
            device.release.set()
            assert (await started[1]).neutral == "sent"
            # This bounded observation does not cancel the stop or its interrupt.
            done, _ = await asyncio.wait({stopping}, timeout=0.05)
            assert not done
            assert c.execution.snapshot.stopping
            c.turn("turn/completed", "turn-2")
            await stopping
            assert not c.execution.snapshot.stopping
            assert c.execution.snapshot.turn_id is None
        finally:
            device.release.set()
            c.turn("turn/completed", "turn-2")
            await c.close()
            if stopping is not None:
                await asyncio.gather(stopping, return_exceptions=True)
            await c.inputs.close()

    run(scenario())
