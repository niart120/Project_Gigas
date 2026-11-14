import asyncio
import json
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.game_io.input.config import get_protocol, resolve_serial_config
from gigas.game_io.input.models import InputState, InputStep
from gigas.game_io.input.serial import SerialInput
from gigas.services import input as input_module
from gigas.services.input import InputService, InputServiceError, OperationResult
from gigas.services.observation import ObservationService
from gigas.services.recording import RecordingService
from gigas.storage.project import ProjectWriter
from tests.integration.test_observation_storage import sample_frame

if TYPE_CHECKING:
    from collections.abc import Iterator


class Device:
    """A state-level input boundary with observable sends and injected failures."""

    def __init__(self) -> None:
        """Prepare a disconnected input device."""
        self.protocol = get_protocol("ch552")
        self.calls: list[str | InputState] = []
        self.pressed = asyncio.Event()
        self.fail_send = False
        self.fail_neutral = False
        self.fail_close = False

    async def open(self) -> None:
        """Open with the transport's initial neutral guarantee."""
        self.calls.append("open")

    async def send(self, state: InputState) -> None:
        """Record the requested state, optionally rejecting its delivery."""
        self.calls.append(state)
        if state != InputState():
            self.pressed.set()
        if self.fail_send or (self.fail_neutral and state == InputState()):
            message = "injected send failure"
            raise OSError(message)

    async def close(self) -> None:
        """Close only when the test allows it."""
        self.calls.append("close")
        if self.fail_close:
            message = "injected close failure"
            raise OSError(message)


class Harness:
    """Actual recording and observation services with a controllable input boundary."""

    def __init__(self, writer: ProjectWriter) -> None:
        """Borrow the test writer and a streaming frame."""
        self.writer = writer
        self.frame = sample_frame()
        self.snapshot = CaptureSnapshot(
            phase="streaming", frame=self.frame, generation=self.frame.generation
        )
        self.reader = CaptureReader(self.read_snapshot)
        self.recording = RecordingService(writer)
        self.observations = ObservationService(self.reader, self.recording)
        self.service = InputService()
        self.failures: list[Exception] = []
        self.recording.on_failure = self.failures.append
        self.device = Device()

    def read_snapshot(self) -> CaptureSnapshot:
        """Return the current cache without generating a new frame."""
        return self.snapshot

    def records(self) -> list[dict[str, object]]:
        """Read persisted records after recording has drained."""
        return [
            json.loads(line)
            for line in (self.writer.path / "events.jsonl").read_bytes().splitlines()
        ]

    async def close(self) -> None:
        """Close inputs, then drain independently owned persistence."""
        await self.service.close()
        await self.observations.drain()
        await self.recording.drain()


@pytest.fixture
def harness(tmp_path: Path) -> "Iterator[Harness]":
    writer = ProjectWriter.create(tmp_path / "game")
    value = Harness(writer)

    try:
        yield value
    finally:
        writer.close()


def press(button: str = "A", duration: int = 1) -> InputStep:
    return InputStep("press", duration, InputState(buttons=(button,)))


def test_finite_sequence_runs_without_permission_context(harness: Harness) -> None:
    async def scenario() -> None:
        await harness.service.connect(harness.device)
        started = harness.service.start((press(), InputStep("wait", 1), press("B")))
        assert isinstance(started, tuple)
        operation_id, task = started
        result = await task
        assert result.code == "OK"
        assert result.operation_id == operation_id
        assert result.completed_steps == 3
        assert result.input_sent == "sent"
        assert result.neutral == "sent"
        assert harness.device.calls == [
            "open",
            InputState(buttons=("A",)),
            InputState(),
            InputState(buttons=("B",)),
            InputState(),
        ]
        await harness.close()

    asyncio.run(scenario())


def test_stop_releases_and_next_sequence_needs_no_permission(harness: Harness) -> None:
    async def scenario() -> None:
        await harness.service.connect(harness.device)
        started = harness.service.start((press(duration=10**100), press("B")))
        assert isinstance(started, tuple)
        _, task = started
        await harness.device.pressed.wait()
        busy = harness.service.start((press("B"),))
        assert isinstance(busy, OperationResult)
        assert busy.code == "BUSY"
        harness.service.stop()
        harness.service.stop()
        result = await asyncio.wait_for(harness.service.drain_input(), 2)
        assert result == await task
        assert result is not None
        assert result.code == "INTERRUPTED"
        assert result.completed_steps == 0
        assert result.neutral == "sent"
        assert harness.device.calls == ["open", InputState(buttons=("A",)), InputState()]
        following = harness.service.start((press("B"),))
        assert isinstance(following, tuple)
        assert (await following[1]).code == "OK"
        await harness.close()

    asyncio.run(scenario())


def test_stop_before_task_starts_sends_nothing(harness: Harness) -> None:
    async def scenario() -> None:
        await harness.service.connect(harness.device)
        started = harness.service.start((press(),))
        assert isinstance(started, tuple)
        harness.service.stop()
        assert await harness.service.drain_input() is None
        assert started[1].cancelled()
        assert harness.device.calls == ["open"]
        await harness.close()

    asyncio.run(scenario())


def test_completed_failure_is_not_replayed_by_later_stop(harness: Harness) -> None:
    async def scenario() -> None:
        await harness.service.connect(harness.device)
        harness.device.fail_send = True
        started = harness.service.start((press(),))
        assert isinstance(started, tuple)
        assert (await started[1]).code == "DEVICE_UNAVAILABLE"
        harness.service.stop()
        assert await harness.service.drain_input() is None
        assert not harness.service.connected
        await harness.close()

    asyncio.run(scenario())


def test_unavailable_or_invalid_whole_sequence_sends_nothing(harness: Harness) -> None:
    async def scenario() -> None:
        disabled = harness.service.start((press(),))
        assert isinstance(disabled, OperationResult)
        assert disabled.code == "INPUT_DISABLED"
        await harness.service.connect(harness.device)
        for unsupported in (
            press("START"),
            press("UNKNOWN"),
            InputStep("press", 1, InputState(touch=(0, 0))),
        ):
            sequence = (press(), unsupported)
            invalid = harness.service.start(sequence)
            assert isinstance(invalid, OperationResult)
            assert invalid.code == "INVALID_SEQUENCE"
            assert invalid.operation_id is None
        assert harness.device.calls == ["open"]
        await harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["send", "neutral"])
def test_send_or_release_failure_closes_without_retry(harness: Harness, failure: str) -> None:
    async def scenario() -> None:
        await harness.service.connect(harness.device)
        harness.device.fail_send = failure == "send"
        harness.device.fail_neutral = failure == "neutral"
        started = harness.service.start((press(), press("B")))
        assert isinstance(started, tuple)
        result = await started[1]
        assert result.code == "DEVICE_UNAVAILABLE"
        assert result.completed_steps == 0
        assert result.neutral == "unconfirmed"
        assert harness.device.calls == ["open", InputState(buttons=("A",)), InputState(), "close"]
        rejected = harness.service.start((press(),))
        assert isinstance(rejected, OperationResult)
        assert rejected.code == "INPUT_DISABLED"
        await harness.close()

    asyncio.run(scenario())


def test_repeated_stop_during_release_keeps_busy_until_neutral_finishes() -> None:
    async def scenario() -> None:
        releasing, finish_release = asyncio.Event(), asyncio.Event()

        class ReleasingDevice(Device):
            async def send(self, state: InputState) -> None:
                await super().send(state)
                if state == InputState():
                    releasing.set()
                    await finish_release.wait()

        service, device = InputService(), ReleasingDevice()
        await service.connect(device)
        started = service.start((press(duration=100000), press("B")))
        assert isinstance(started, tuple)
        await device.pressed.wait()
        service.stop()
        await releasing.wait()
        service.stop()
        busy = service.start((press(),))
        assert isinstance(busy, OperationResult)
        assert busy.code == "BUSY"
        assert not started[1].done()
        finish_release.set()
        result = await service.drain_input()
        assert result is not None
        assert result.neutral == "sent"
        assert device.calls == ["open", InputState(buttons=("A",)), InputState()]
        await service.close()

    asyncio.run(scenario())


def test_wait_only_sequence_is_interruptible_without_input(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        waiting = asyncio.Event()
        original = input_module._wait

        async def observed_wait(duration_ms: int) -> None:
            waiting.set()
            await original(duration_ms)

        monkeypatch.setattr(input_module, "_wait", observed_wait)
        await harness.service.connect(harness.device)
        started = harness.service.start((InputStep("wait", 10**100), press()))
        assert isinstance(started, tuple)
        await waiting.wait()
        harness.service.stop()
        result = await started[1]
        assert result.code == "INTERRUPTED"
        assert result.input_sent == "none"
        assert result.neutral == "not_needed"
        assert harness.device.calls == ["open"]
        await harness.close()

    asyncio.run(scenario())


def test_failed_close_retains_old_owner_and_prevents_new_open() -> None:
    async def scenario() -> None:
        service = InputService()
        device, new_device = Device(), Device()
        await service.connect(device)
        device.fail_close = True
        with pytest.raises(InputServiceError):
            await service.connect(new_device)
        assert new_device.calls == []
        assert not service.connected
        device.fail_close = False
        await service.connect(new_device)
        assert device.calls.count("close") == 2
        assert new_device.calls == ["open"]
        await service.close()

    asyncio.run(scenario())


def test_close_waits_for_open_before_closing_candidate() -> None:
    async def scenario() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        class OpeningDevice(Device):
            async def open(self) -> None:
                self.calls.append("opening")
                entered.set()
                await release.wait()
                self.calls.append("opened")

        service, device = InputService(), OpeningDevice()
        connecting = asyncio.create_task(service.connect(device))
        await entered.wait()
        closing = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await connecting
        await closing
        assert device.calls == ["opening", "opened", "close"]
        assert not service.connected

    asyncio.run(scenario())


def test_close_rejects_input_while_draining_operation() -> None:
    async def scenario() -> None:
        closing, finish_close = asyncio.Event(), asyncio.Event()

        class ClosingDevice(Device):
            async def close(self) -> None:
                closing.set()
                await finish_close.wait()
                await super().close()

        service, device = InputService(), ClosingDevice()
        await service.connect(device)
        close = asyncio.create_task(service.close())
        await closing.wait()
        rejected = service.start((press(),))
        assert isinstance(rejected, OperationResult)
        assert rejected.code == "INPUT_DISABLED"
        finish_close.set()
        await close
        assert device.calls == ["open", "close"]

    asyncio.run(scenario())


@pytest.mark.parametrize("write_number", [2, 3], ids=["press", "neutral"])
def test_stop_during_completed_write_preserves_delivery_and_connection(
    harness: Harness, write_number: int
) -> None:
    entered, release = Event(), Event()

    class Port:
        def __init__(self) -> None:
            self.writes = 0
            self.closed = False

        def write(self, data: bytes) -> int:
            self.writes += 1
            if self.writes == write_number:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError
            return len(data)

        def close(self) -> None:
            self.closed = True

    async def scenario() -> None:
        port = Port()
        config = resolve_serial_config("COM3", "ch552", None)
        assert config is not None
        device = SerialInput(config, open_port=lambda _config: port)
        await harness.service.connect(device)
        started = harness.service.start((press(), press("B")))
        assert isinstance(started, tuple)
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            harness.service.stop()
        finally:
            release.set()
        result = await started[1]
        assert result.code == "INTERRUPTED"
        assert result.input_sent == "sent"
        assert result.neutral == "sent"
        assert result.completed_steps == write_number - 2
        assert harness.service.connected
        assert not port.closed
        assert port.writes == 3
        await harness.close()

    asyncio.run(scenario())
