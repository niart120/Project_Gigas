import asyncio
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest
from pytestqt.qtbot import QtBot

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.game_io.input.config import resolve_serial_config
from gigas.runtime.codex.connection import CodexConnection
from gigas.runtime.codex.models import ConnectionOptions
from gigas.services.application import ApplicationServiceThread
from gigas.services.input import InputService
from tests.integration.test_input_service import Device
from tests.unit.test_codex_connection import FakePeer, _terminal

if TYPE_CHECKING:
    from gigas.game_io.input.config import SerialConfig


def test_input_settings_lifecycle_without_codex() -> None:
    async def scenario() -> None:
        devices: list[Device] = []
        results = []

        def factory(_config: "SerialConfig") -> Device:
            device = Device()
            devices.append(device)
            return device

        service = InputService(factory=factory, on_connection_changed=results.append)
        config = resolve_serial_config("COM3", "ch552", None)
        try:
            for target in (config, config, None, config):
                service.request_config(target)
                await service.apply_config()
            assert [result.connected for result in results] == [True, True, False, True]
            assert not any(result.error for result in results)
            assert len(devices) == 2
            assert devices[0].calls == ["open", "close"]
        finally:
            await service.close()
        assert devices[1].calls == ["open", "close"]

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["complete", "failure", "shutdown"])
def test_connection_change_waits_for_stop_and_uses_latest_target(
    qtbot: QtBot, tmp_path: Path, outcome: str
) -> None:
    peer = FakePeer()
    devices: list[tuple[str, Device]] = []
    results = []
    applied = []
    queued = Event()

    def factory(config: "SerialConfig") -> Device:
        device = Device()
        devices.append((config.port, device))
        return device

    service = ApplicationServiceThread(
        tmp_path,
        CaptureReader(CaptureSnapshot),
        lambda options, emit: CodexConnection(peer, options, emit),
        input_factory=factory,
    )
    service.completed.connect(results.append)
    service.input_changed.connect(applied.append)
    service.start()
    try:
        initial = resolve_serial_config("COM1", "ch552", None)
        service.apply_input(initial)
        service.connect_runtime(ConnectionOptions(tmp_path))
        qtbot.waitUntil(lambda: bool(applied) and any(r.operation == "connect" for r in results))
        service.send("start")
        qtbot.waitUntil(lambda: any(r.operation == "send" for r in results))
        service.apply_input(initial)
        qtbot.waitUntil(lambda: len(applied) == 2)
        assert not any(method == "turn/interrupt" for method, _ in peer.calls)
        if outcome == "failure":
            peer.failure = "turn/interrupt"

        def change_batch() -> None:
            service._accept_input(initial)
            service._accept_input(resolve_serial_config("COM2", "ch552", None))
            service._accept_input(resolve_serial_config("COM3", "ch552", None))
            queued.set()

        service._enqueue(change_batch)
        qtbot.waitUntil(queued.is_set)
        qtbot.waitUntil(lambda: any(method == "turn/interrupt" for method, _ in peer.calls))
        assert [port for port, _ in devices] == ["COM1"]
        if outcome == "shutdown":
            service.shutdown()
            qtbot.waitUntil(lambda: service.wait(0), timeout=10000)
        elif outcome == "failure":
            qtbot.waitUntil(lambda: len(applied) > 2)
            assert applied[-1].error
            qtbot.waitUntil(lambda: peer.closed)
        else:
            service._enqueue(lambda: peer.events.put_nowait(_terminal("interrupted")))
            qtbot.waitUntil(lambda: len(applied) > 2)
            assert not applied[-1].error
            assert [port for port, _ in devices] == ["COM1", "COM3"]
            assert devices[0][1].calls == ["open", "close"]
        if outcome != "complete":
            assert [port for port, _ in devices] == ["COM1"]
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0), timeout=10000)
    assert not service.shutdown_error


def test_next_connection_change_stops_turn_started_during_open(
    qtbot: QtBot, tmp_path: Path
) -> None:
    entered, release = Event(), Event()
    peer = FakePeer()
    created: list[str] = []
    results = []
    applied = []

    class SlowDevice(Device):
        async def open(self) -> None:
            entered.set()
            if not await asyncio.to_thread(release.wait, 5):
                raise TimeoutError
            await super().open()

    def factory(config: "SerialConfig") -> Device:
        created.append(config.port)
        return SlowDevice() if config.port == "COM2" else Device()

    service = ApplicationServiceThread(
        tmp_path,
        CaptureReader(CaptureSnapshot),
        lambda options, emit: CodexConnection(peer, options, emit),
        input_factory=factory,
    )
    service.completed.connect(results.append)
    service.input_changed.connect(applied.append)
    service.start()
    try:
        service.connect_runtime(ConnectionOptions(tmp_path))
        qtbot.waitUntil(lambda: any(r.operation == "connect" for r in results))
        service.apply_input(resolve_serial_config("COM2", "ch552", None))
        qtbot.waitUntil(entered.is_set)
        service.send("start while opening")
        qtbot.waitUntil(lambda: any(r.operation == "send" for r in results))
        service.apply_input(resolve_serial_config("COM3", "ch552", None))
        release.set()
        qtbot.waitUntil(lambda: bool(applied))
        qtbot.waitUntil(lambda: any(method == "turn/interrupt" for method, _ in peer.calls))
        assert created == ["COM2"]
        service._enqueue(lambda: peer.events.put_nowait(_terminal("interrupted")))
        qtbot.waitUntil(lambda: len(applied) == 2)
        assert created == ["COM2", "COM3"]
        assert not applied[-1].error
    finally:
        release.set()
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0), timeout=10000)
