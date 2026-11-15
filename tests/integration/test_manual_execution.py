import asyncio
from pathlib import Path

import pytest

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.game_io.input.models import InputState
from gigas.runtime.codex.connection import CodexConnection
from gigas.runtime.codex.models import ConnectionOptions, GoalState
from gigas.services.codex import CodexService
from gigas.services.codex_models import MessageResult
from gigas.services.input import InputService
from tests.integration.peers.codex_resume import ResumePeer
from tests.integration.test_conversation_control import goal
from tests.integration.test_conversation_execution import Conversation
from tests.integration.test_conversation_resume import _stored
from tests.integration.test_input_service import Device, press
from tests.unit.test_codex_connection import FakePeer, _run, _until


async def _begin(c: Conversation, kind: str) -> None:
    if kind.endswith("send"):
        await c.execution.send("start")
    elif kind.endswith("resume"):
        await c.execution.resume_goal()
    else:
        await c.execution.set_goal("goal")


@pytest.mark.parametrize(
    "kind", ["new_send", "new_goal", "loaded_goal", "stored_send", "stored_goal", "stored_resume"]
)
def test_execution_releases_manual_before_any_thread_preparation(tmp_path: Path, kind: str) -> None:
    async def scenario() -> None:
        entered, finish = asyncio.Event(), asyncio.Event()

        class SlowNeutral(Device):
            async def send(self, state: InputState) -> None:
                if state == InputState():
                    entered.set()
                    await finish.wait()
                await super().send(state)

        c = Conversation(tmp_path, peer=ResumePeer())
        if kind.startswith("stored"):
            _stored(c, tmp_path, GoalState("old", "paused"))
        device = SlowNeutral()
        await c.connection.open()
        await c.inputs.connect(device)
        sending = None
        try:
            if kind == "loaded_goal":
                await c.execution.send("initial")
                c.turn("turn/completed")
            before = list(c.peer.calls)
            c.inputs.update_manual(InputState(buttons=("A",)))
            await device.pressed.wait()
            sending = asyncio.create_task(_begin(c, kind))
            await _until(lambda: entered.is_set() or sending.done())
            assert entered.is_set()
            assert c.execution.manual_blocked
            assert c.peer.calls == before
            finish.set()
            await sending
            assert device.calls == ["open", InputState(buttons=("A",)), InputState()]
        finally:
            finish.set()
            await c.close()
            if sending is not None:
                await asyncio.gather(sending, return_exceptions=True)
            await c.inputs.close()

    _run(scenario())


def test_stop_during_manual_release_never_revives_preparation(tmp_path: Path) -> None:
    async def scenario() -> None:
        entered, finish = asyncio.Event(), asyncio.Event()

        class SlowNeutral(Device):
            async def send(self, state: InputState) -> None:
                if state == InputState():
                    entered.set()
                    await finish.wait()
                await super().send(state)

        c = Conversation(tmp_path)
        device = SlowNeutral()
        await c.connection.open()
        await c.inputs.connect(device)
        try:
            c.inputs.update_manual(InputState(buttons=("A",)))
            await device.pressed.wait()
            sending = asyncio.create_task(c.execution.send("must not start"))
            await _until(lambda: entered.is_set() or sending.done())
            assert entered.is_set()
            stopping = asyncio.create_task(c.execution.stop())
            await _until(lambda: c.execution.snapshot.stopping)
            assert not stopping.done()
            finish.set()
            await stopping
            assert await sending is None
            assert not any(
                method in {"thread/start", "thread/resume", "turn/start"}
                for method, _ in c.peer.calls
            )
            assert not c.execution.manual_blocked
        finally:
            finish.set()
            await c.close()
            await c.inputs.close()

    _run(scenario())


def test_loaded_goal_edits_and_model_changes_keep_manual_hold(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path, peer=ResumePeer())
        device = Device()
        await c.connection.open()
        await c.inputs.connect(device)
        gate = asyncio.Event()
        try:
            await c.execution.send("initial")
            c.turn("turn/completed")
            goal(c, "paused")
            c.inputs.update_manual(InputState(buttons=("A",)))
            await device.pressed.wait()
            c.peer.gates["thread/goal/set"] = gate
            editing = asyncio.create_task(c.execution.set_goal("edit"))
            await _until(lambda: any(m == "thread/goal/set" for m, _ in c.peer.calls))
            assert c.execution.snapshot.sending
            assert not c.execution.manual_blocked
            gate.set()
            await editing
            await c.execution.select_model("other", "low")
            await c.execution.clear_goal()
            assert device.calls == ["open", InputState(buttons=("A",))]
            assert not c.execution.manual_blocked
        finally:
            gate.set()
            await c.close()
            await c.inputs.close()

    _run(scenario())


def test_additional_send_does_not_cancel_finite_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        device = Device()
        await c.connection.open()
        await c.inputs.connect(device)
        try:
            await c.execution.send("initial")
            finite = c.execution.start_input((press(duration=10000),))
            assert isinstance(finite, tuple)
            await device.pressed.wait()
            await c.execution.send("additional")
            assert not finite[1].done()
            assert device.calls == ["open", InputState(buttons=("A",))]
        finally:
            await c.close()
            await c.inputs.close()

    _run(scenario())


@pytest.mark.parametrize("operation", ["send", "goal_set"])
def test_failed_manual_release_reports_not_sent_then_allows_ordinary_chat(
    tmp_path: Path, operation: str
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        device = Device()
        inputs = InputService()
        results = asyncio.Queue()
        service = CodexService(
            tmp_path,
            CaptureReader(CaptureSnapshot),
            inputs,
            factory=lambda options, emit: CodexConnection(peer, options, emit),
            received=lambda _event: None,
            completed=results.put_nowait,
            project_completed=lambda _result: None,
            management_completed=lambda _result: None,
            browse_goal_changed=lambda _generation, _event: None,
            recording_failed=lambda _operation, _path, _reason: None,
        )
        await inputs.connect(device)
        try:
            service.connect_runtime(ConnectionOptions(tmp_path), 1)
            await results.get()
            inputs.update_manual(InputState(buttons=("A",)))
            await device.pressed.wait()
            device.fail_neutral = True
            if operation == "send":
                service.send("must not start")
            else:
                service.set_goal("must not start")
            failed = await results.get()
            assert failed.message == MessageResult("not_sent")
            assert "入力" in failed.error
            assert not inputs.connected
            assert not any(m in {"thread/start", "turn/start"} for m, _ in peer.calls)
            service.send("ordinary chat")
            accepted = await results.get()
            assert accepted.message == MessageResult("accepted", "turn-1")
        finally:
            await service.close()
            await inputs.close()

    _run(scenario())
