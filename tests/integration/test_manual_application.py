import asyncio
from pathlib import Path
from threading import Event

import pytest
from pytestqt.qtbot import QtBot

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.game_io.input.config import resolve_serial_config
from gigas.game_io.input.models import InputState, InputStep
from gigas.runtime.codex.connection import CodexConnection
from gigas.runtime.codex.models import ConnectionOptions
from gigas.services.application import ApplicationServiceThread
from gigas.services.codex import CodexService
from gigas.services.input import InputService
from tests.integration.peers.codex_resume import ResumePeer
from tests.integration.test_input_service import Device
from tests.unit.test_codex_connection import FakePeer, _run, _until


@pytest.mark.parametrize("held", [False, True])
def test_codex_disconnect_only_joins_its_captured_finite_operation(
    tmp_path: Path, *, held: bool
) -> None:
    async def scenario() -> None:
        entered, finish = asyncio.Event(), asyncio.Event()

        class SlowClose(FakePeer):
            async def close(self) -> None:
                entered.set()
                await finish.wait()
                await super().close()

        peer = SlowClose()
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
            finite = None
            if held:
                inputs.update_manual(InputState(buttons=("A",)))
            else:
                finite = inputs.start((InputStep("press", 10000, InputState(buttons=("A",))),))
                assert isinstance(finite, tuple)
            await device.pressed.wait()
            service.disconnect_runtime()
            await entered.wait()
            if finite is not None:
                assert (await finite[1]).code == "INTERRUPTED"
            else:
                assert device.calls == ["open", InputState(buttons=("A",))]
            assert inputs.update_manual(InputState(buttons=("B",))).code == "OK"
            await _until(lambda: InputState(buttons=("B",)) in device.calls)
            finish.set()
            disconnected = await results.get()
            assert disconnected.operation == "disconnect"
            assert not disconnected.error
            await service.close()
            assert inputs.connected
            assert device.calls[-1] == InputState(buttons=("B",))
        finally:
            finish.set()
            await inputs.release_manual()
            await service.close()
            await inputs.close()

    _run(scenario())


def test_manual_delivery_order_goal_gaps_and_stop(qtbot: QtBot, tmp_path: Path) -> None:
    device = Device()
    peer = ResumePeer()
    results = []
    applied = []
    availability = []
    service = ApplicationServiceThread(
        tmp_path,
        CaptureReader(CaptureSnapshot),
        lambda options, emit: CodexConnection(peer, options, emit),
        input_factory=lambda _config: device,
    )
    service.completed.connect(results.append)
    service.input_changed.connect(applied.append)
    service.manual_changed.connect(lambda allowed, _protocol: availability.append(allowed))
    service.start()
    try:
        service.apply_input(resolve_serial_config("COM8", "ch552", None))
        qtbot.waitUntil(lambda: bool(applied))
        service.update_manual(InputState(buttons=("A",)))
        qtbot.waitUntil(lambda: InputState(buttons=("A",)) in device.calls)
        service.connect_runtime(ConnectionOptions(tmp_path))
        qtbot.waitUntil(lambda: any(r.operation == "connect" for r in results))
        service.set_goal("run")
        service.update_manual(InputState(buttons=("B",)))
        qtbot.waitUntil(lambda: any(r.operation == "goal_set" for r in results))
        assert InputState(buttons=("B",)) not in device.calls
        assert device.calls[-1] == InputState()
        assert not availability[-1]
        before = list(device.calls)
        service.release_manual()
        service.update_manual(InputState(buttons=("X",)))
        service.interrupt()
        qtbot.waitUntil(lambda: any(r.operation == "stop" for r in results))
        assert device.calls == before
        assert availability[-1]
        service.update_manual(InputState(buttons=("Y",)))
        qtbot.waitUntil(lambda: InputState(buttons=("Y",)) in device.calls)
        service.release_manual()
        qtbot.waitUntil(lambda: device.calls[-1] == InputState())
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0), timeout=10000)
    assert not service.shutdown_error


def test_manual_rejected_while_stop_or_exit_request_is_pending(
    qtbot: QtBot, tmp_path: Path
) -> None:
    entered, finish = Event(), Event()

    class SlowNeutral(Device):
        async def send(self, state: InputState) -> None:
            if state == InputState():
                entered.set()
                if not await asyncio.to_thread(finish.wait, 5):
                    raise TimeoutError
            await super().send(state)

    device = SlowNeutral()
    results = []
    applied = []
    availability = []
    service = ApplicationServiceThread(
        tmp_path, CaptureReader(CaptureSnapshot), input_factory=lambda _config: device
    )
    service.completed.connect(results.append)
    service.input_changed.connect(applied.append)
    service.manual_changed.connect(lambda allowed, _protocol: availability.append(allowed))
    service.start()
    try:
        service.apply_input(resolve_serial_config("COM8", "ch552", None))
        qtbot.waitUntil(lambda: bool(applied) and bool(availability) and availability[-1])
        service.update_manual(InputState(buttons=("A",)))
        qtbot.waitUntil(lambda: InputState(buttons=("A",)) in device.calls)
        service.interrupt()
        qtbot.waitUntil(entered.is_set)
        service.update_manual(InputState(buttons=("B",)))
        service.set_exit_requested(True)
        qtbot.waitUntil(lambda: not availability[-1])
        assert not any(result.operation == "stop" for result in results)
        finish.set()
        qtbot.waitUntil(lambda: any(result.operation == "stop" for result in results))
        assert not availability[-1]
        assert InputState(buttons=("B",)) not in device.calls
        service.set_exit_requested(False)
        qtbot.waitUntil(lambda: availability[-1])
        assert device.calls[-1] == InputState()
        service.update_manual(InputState(buttons=("X",)))
        qtbot.waitUntil(lambda: InputState(buttons=("X",)) in device.calls)
    finally:
        finish.set()
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0), timeout=10000)
    assert not service.shutdown_error


def test_manual_release_failure_is_reported_when_application_closes(
    qtbot: QtBot, tmp_path: Path
) -> None:
    device = Device()
    applied = []
    failures = []
    service = ApplicationServiceThread(
        tmp_path, CaptureReader(CaptureSnapshot), input_factory=lambda _config: device
    )
    service.input_changed.connect(applied.append)
    service.manual_completed.connect(failures.append)
    service.start()
    try:
        service.apply_input(resolve_serial_config("COM8", "ch552", None))
        qtbot.waitUntil(lambda: bool(applied))
        service.update_manual(InputState(buttons=("A",)))
        qtbot.waitUntil(lambda: InputState(buttons=("A",)) in device.calls)
        device.fail_neutral = True
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0), timeout=10000)
    assert service.shutdown_error
    assert failures[-1].neutral == "unconfirmed"
    assert device.calls[-1] == "close"


def test_goal_resume_release_failure_keeps_goal_paused_and_reports_input_reason(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = ResumePeer()
    device = Device()
    results = []
    applied = []
    manual = []
    service = ApplicationServiceThread(
        tmp_path,
        CaptureReader(CaptureSnapshot),
        lambda options, emit: CodexConnection(peer, options, emit),
        input_factory=lambda _config: device,
    )
    service.completed.connect(results.append)
    service.input_changed.connect(applied.append)
    service.manual_completed.connect(manual.append)
    service.start()
    try:
        service.apply_input(resolve_serial_config("COM8", "ch552", None))
        service.connect_runtime(ConnectionOptions(tmp_path))
        qtbot.waitUntil(lambda: bool(applied) and any(r.operation == "connect" for r in results))
        service.set_goal("run")
        qtbot.waitUntil(lambda: any(r.operation == "goal_set" for r in results))
        service.interrupt()
        qtbot.waitUntil(lambda: any(r.operation == "stop" for r in results))
        service.update_manual(InputState(buttons=("A",)))
        qtbot.waitUntil(lambda: InputState(buttons=("A",)) in device.calls)
        device.fail_neutral = True
        before = sum(method == "thread/goal/set" for method, _ in peer.calls)
        service.resume_goal()
        qtbot.waitUntil(lambda: any(r.operation == "goal_resume" for r in results))
        resumed = next(r for r in results if r.operation == "goal_resume")
        assert "入力" in resumed.error
        assert resumed.message is None
        assert sum(method == "thread/goal/set" for method, _ in peer.calls) == before
        assert peer.goal is not None
        assert peer.goal["status"] == "paused"
        assert manual[-1].neutral == "unconfirmed"
        service.send("ordinary chat")
        qtbot.waitUntil(lambda: any(r.operation == "send" for r in results))
        assert not next(r.error for r in results if r.operation == "send")
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0), timeout=10000)


@pytest.mark.parametrize("before_exit", [False, True])
def test_exit_cancellation_cannot_replay_queued_manual_input(
    qtbot: QtBot, tmp_path: Path, *, before_exit: bool
) -> None:
    device = Device()
    applied = []
    finished = []
    dispatched = Event()
    service = ApplicationServiceThread(
        tmp_path, CaptureReader(CaptureSnapshot), input_factory=lambda _config: device
    )
    service.input_changed.connect(applied.append)
    service.manual_completed.connect(finished.append)
    service.start()
    try:
        service.apply_input(resolve_serial_config("COM8", "ch552", None))
        qtbot.waitUntil(lambda: bool(applied))

        def enqueue_batch() -> None:
            def mark_dispatched() -> None:
                asyncio.get_running_loop().call_soon(dispatched.set)

            if before_exit:
                service.update_manual(InputState(buttons=("A",)))
            service.set_exit_requested(True)
            if not before_exit:
                service.update_manual(InputState(buttons=("A",)))
            service.set_exit_requested(False)
            service._enqueue(mark_dispatched)

        service._enqueue(enqueue_batch)
        qtbot.waitUntil(dispatched.is_set)
        if before_exit:
            qtbot.waitUntil(lambda: bool(finished))
            assert device.calls == ["open", InputState(buttons=("A",)), InputState()]
        else:
            assert not service.inputs.busy
            assert device.calls == ["open"]
        assert service.inputs.manual_available
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0), timeout=10000)
