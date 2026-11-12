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
from gigas.game_io.observation import Observation, ObservationError
from gigas.services import input as input_module
from gigas.services.input import InputService, InputServiceError
from gigas.services.observation import ObservationService
from gigas.storage import project
from gigas.storage.input_events import OperationStarted, RunStarted
from gigas.storage.project import ProjectWriter
from tests.integration.test_observation_storage import sample_frame

if TYPE_CHECKING:
    from collections.abc import Iterator

    from gigas.game_io.capture.models import Frame
    from gigas.game_io.observation import ObservationResolution
    from gigas.storage.input_events import InputEvent


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
        self.reader = CaptureReader(lambda: self.snapshot)
        self.observations = ObservationService(self.reader, writer)
        self.service = InputService(self.reader, self.observations)
        self.device = Device()

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


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> "Iterator[Harness]":
    writer = ProjectWriter.create(tmp_path / "game")
    value = Harness(writer)

    async def observed(
        _reader: CaptureReader, service: ObservationService, generation: int, boundary_ns: int
    ) -> Observation:
        # Frame freshness belongs to T07; this test observes the execution boundary.
        assert generation == value.frame.generation
        assert boundary_ns > 0
        return await service.observe()

    monkeypatch.setattr(input_module, "observe_after", observed)
    try:
        yield value
    finally:
        writer.close()


def press(button: str = "A", duration: int = 1) -> InputStep:
    return InputStep("press", duration, InputState(buttons=(button,)))


def test_sequence_records_content_results_and_observation(harness: Harness) -> None:
    async def scenario() -> None:
        await harness.service.connect(harness.device)
        grant = await harness.service.begin_run("A、待機、B", "thread", turn_id="turn")
        result = await harness.service.act(grant, (press(), InputStep("wait", 1), press("B")))
        assert result.code == "OK"
        assert result.completed_steps == 3
        assert result.input_sent == "sent"
        assert result.neutral == "sent"
        assert result.observation is not None
        await harness.service.finish_run("completed")
        await harness.close()
        rows = harness.records()
        assert [row["type"] for row in rows] == [
            "input.run_started",
            "input.operation_started",
            "observation.recorded",
            "input.operation_finished",
            "input.run_finished",
        ]
        payload = rows[-2]["payload"]
        assert isinstance(payload, dict)
        assert payload["operation_id"] == result.operation_id
        assert payload["observation_id"] == result.observation.observation_id
        assert harness.device.calls == [
            "open",
            InputState(buttons=("A",)),
            InputState(),
            InputState(buttons=("B",)),
            InputState(),
            "close",
        ]

    asyncio.run(scenario())


def test_long_press_stops_once_releases_and_rejects_old_grant(harness: Harness) -> None:
    async def scenario() -> None:
        await harness.service.connect(harness.device)
        grant = await harness.service.begin_run("長いA", "thread")
        operation = asyncio.create_task(
            harness.service.act(grant, (press(duration=10**100), press("B")))
        )
        await harness.device.pressed.wait()
        busy = await harness.service.act(grant, (press("B"),))
        assert busy.code == "BUSY"
        harness.service.stop()
        harness.service.stop()
        result = await asyncio.wait_for(operation, 3)
        await harness.service.drain_input()
        assert result.code == "INTERRUPTED"
        assert result.completed_steps == 0
        assert result.neutral == "sent"
        assert harness.device.calls == ["open", InputState(buttons=("A",)), InputState()]
        assert (await harness.service.act(grant, (press(),))).code == "INPUT_DISABLED"
        await harness.service.finish_run("stopped")
        new_grant = await harness.service.begin_run("B", "thread")
        assert (await harness.service.act(grant, (press(),))).code == "INPUT_DISABLED"
        assert (await harness.service.act(new_grant, (press("B"),))).code == "OK"
        await harness.close()

    asyncio.run(scenario())


def test_unconnected_run_and_invalid_sequence_never_send(harness: Harness) -> None:
    async def scenario() -> None:
        disabled = await harness.service.begin_run("未接続", "thread")
        assert (await harness.service.act(disabled, (press(),))).code == "INPUT_DISABLED"
        await harness.service.finish_run("completed")
        await harness.service.connect(harness.device)
        grant = await harness.service.begin_run("入力", "thread")
        invalid = await harness.service.act(grant, (press(), press("START")))
        assert invalid.code == "INVALID_SEQUENCE"
        assert invalid.input_sent == "none"
        assert harness.device.calls == ["open"]
        harness.snapshot = CaptureSnapshot()
        assert (await harness.service.act(grant, (press(),))).code == "DEVICE_UNAVAILABLE"
        assert harness.device.calls == ["open"]
        await harness.close()

    asyncio.run(scenario())


def test_suspend_cancels_current_input_without_reviving_it(harness: Harness) -> None:
    async def scenario() -> None:
        await harness.service.connect(harness.device)
        grant = await harness.service.begin_run("A", "thread")
        operation = asyncio.create_task(
            harness.service.act(grant, (press(duration=100000), press("B")))
        )
        await harness.device.pressed.wait()
        harness.service.suspend()
        assert (await operation).code == "INTERRUPTED"
        assert (await harness.service.act(grant, (press(),))).code == "INPUT_DISABLED"
        harness.service.resume()
        assert (await harness.service.act(grant, (press(),))).code == "OK"
        harness.service.stop()
        harness.service.resume()
        assert (await harness.service.act(grant, (press(),))).code == "INPUT_DISABLED"
        await harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("start_type", [RunStarted, OperationStarted])
def test_stop_during_start_save_never_later_enables_input(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    start_type: type[RunStarted | OperationStarted],
) -> None:
    entered, release = Event(), Event()
    original = ProjectWriter.record_input

    def blocked(writer: ProjectWriter, event: "InputEvent") -> None:
        if isinstance(event, start_type):
            entered.set()
            if not release.wait(5):
                raise TimeoutError
        original(writer, event)

    monkeypatch.setattr(ProjectWriter, "record_input", blocked)

    async def scenario() -> None:
        await harness.service.connect(harness.device)
        starting = asyncio.create_task(harness.service.begin_run("A", "thread"))
        operation = None
        try:
            if start_type is OperationStarted:
                grant = await starting
                operation = asyncio.create_task(harness.service.act(grant, (press(),)))
            assert await asyncio.to_thread(entered.wait, 3)
            harness.service.stop()
            if operation is not None:
                assert (await asyncio.wait_for(operation, 3)).code == "INTERRUPTED"
            assert harness.device.calls == ["open"]
        finally:
            release.set()
            grant = await starting
            await harness.service.drain_input()
        assert (await harness.service.act(grant, (press(),))).code == "INPUT_DISABLED"
        await harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("start_type", [RunStarted, OperationStarted])
def test_start_record_failure_never_becomes_success_or_plain_interruption(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    start_type: type[RunStarted | OperationStarted],
) -> None:
    original = ProjectWriter.record_input

    def failed(writer: ProjectWriter, event: "InputEvent") -> None:
        if isinstance(event, start_type):
            message = "recording failed"
            raise OSError(message)
        original(writer, event)

    monkeypatch.setattr(ProjectWriter, "record_input", failed)

    async def scenario() -> None:
        await harness.service.connect(harness.device)
        if start_type is RunStarted:
            with pytest.raises(InputServiceError) as failure:
                await harness.service.begin_run("A", "thread")
            assert failure.value.code == "STORAGE_ERROR"
        else:
            grant = await harness.service.begin_run("A", "thread")
            result = await harness.service.act(grant, (press(),))
            assert result.code == "STORAGE_ERROR"
        assert harness.device.calls == ["open"]
        await harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["send", "neutral"])
def test_send_or_release_failure_closes_without_retry(harness: Harness, failure: str) -> None:
    async def scenario() -> None:
        await harness.service.connect(harness.device)
        grant = await harness.service.begin_run("A", "thread")
        harness.device.fail_send = failure == "send"
        harness.device.fail_neutral = failure == "neutral"
        result = await harness.service.act(grant, (press(), press("B")))
        assert result.code == "DEVICE_UNAVAILABLE"
        assert result.completed_steps == 0
        assert result.neutral == "unconfirmed"
        assert harness.device.calls == ["open", InputState(buttons=("A",)), InputState(), "close"]
        assert (await harness.service.act(grant, (press(),))).code == "INPUT_DISABLED"
        await harness.close()

    asyncio.run(scenario())


def test_external_act_cancellation_retains_cleanup_for_drain(harness: Harness) -> None:
    async def scenario() -> None:
        await harness.service.connect(harness.device)
        grant = await harness.service.begin_run("A", "thread")
        request = asyncio.create_task(
            harness.service.act(grant, (press(duration=100000), press("B")))
        )
        await harness.device.pressed.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        await harness.service.drain_input()
        assert harness.device.calls[-1] == InputState()
        assert (await harness.service.act(grant, (press(),))).code == "INPUT_DISABLED"
        await harness.close()

    asyncio.run(scenario())


def test_observation_failure_keeps_the_executed_input_result(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failed(*_args: object) -> Observation:
        message = "post-action frame timeout"
        raise ObservationError("OBSERVATION_TIMEOUT", message)

    monkeypatch.setattr(input_module, "observe_after", failed)

    async def scenario() -> None:
        await harness.service.connect(harness.device)
        grant = await harness.service.begin_run("A", "thread")
        result = await harness.service.act(grant, (press(),))
        assert result.code == "OBSERVATION_TIMEOUT"
        assert result.completed_steps == 1
        assert result.input_sent == "sent"
        assert result.neutral == "sent"
        assert result.observation is None
        assert (await harness.service.act(grant, (press(),))).code == "INPUT_DISABLED"
        assert harness.service.connected
        await harness.close()

    asyncio.run(scenario())


def test_failed_close_retains_old_owner_and_prevents_new_open(harness: Harness) -> None:
    async def scenario() -> None:
        await harness.service.connect(harness.device)
        new_device = Device()
        harness.device.fail_close = True
        with pytest.raises(InputServiceError):
            await harness.service.connect(new_device)
        assert new_device.calls == []
        harness.device.fail_close = False
        await harness.service.connect(new_device)
        assert harness.device.calls.count("close") == 2
        assert new_device.calls == ["open"]
        await harness.close()

    asyncio.run(scenario())


def test_wait_only_sequence_is_interruptible_without_input(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    waiting = asyncio.Event()
    original = input_module._wait

    async def observed_wait(duration_ms: int) -> None:
        waiting.set()
        await original(duration_ms)

    monkeypatch.setattr(input_module, "_wait", observed_wait)

    async def scenario() -> None:
        await harness.service.connect(harness.device)
        grant = await harness.service.begin_run("待機", "thread")
        request = asyncio.create_task(
            harness.service.act(grant, (InputStep("wait", 10**100), press()))
        )
        await waiting.wait()
        harness.service.stop()
        result = await request
        assert result.code == "INTERRUPTED"
        assert result.input_sent == "none"
        assert result.neutral == "not_needed"
        assert harness.device.calls == ["open"]
        await harness.close()

    asyncio.run(scenario())


def test_stop_during_encoding_keeps_the_completed_observation_id(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    original = Observation.from_frame

    def blocked_encode(
        _cls: type[Observation], frame: "Frame", *, resolution: "ObservationResolution" = "full_hd"
    ) -> Observation:
        entered.set()
        if not release.wait(5):
            raise TimeoutError
        return original(frame, resolution=resolution)

    async def observed(
        _reader: CaptureReader, service: ObservationService, _generation: int, _boundary_ns: int
    ) -> Observation:
        return await service.observe_frame(harness.frame)

    monkeypatch.setattr(Observation, "from_frame", classmethod(blocked_encode))
    monkeypatch.setattr(input_module, "observe_after", observed)

    async def scenario() -> None:
        await harness.service.connect(harness.device)
        grant = await harness.service.begin_run("A", "thread")
        operation = asyncio.create_task(harness.service.act(grant, (press(),)))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            harness.service.stop()
            await asyncio.sleep(0)
            assert not operation.done()
        finally:
            release.set()
        result = await operation
        assert result.code == "INTERRUPTED"
        assert result.phase == "observe"
        assert result.observation is not None
        await harness.close()
        row = next(row for row in harness.records() if row["type"] == "input.operation_finished")
        payload = row["payload"]
        assert isinstance(payload, dict)
        assert payload["result"] == "interrupted"
        assert payload["observation_id"] == result.observation.observation_id

    asyncio.run(scenario())


def test_background_save_failure_preserves_returned_result_and_disables_input(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()

    def failed_publish(_path: Path, _data: bytes) -> None:
        entered.set()
        if not release.wait(5):
            raise TimeoutError
        message = "injected image save failure"
        raise OSError(message)

    monkeypatch.setattr(project, "_publish_image", failed_publish)

    async def scenario() -> None:
        await harness.service.connect(harness.device)
        grant = await harness.service.begin_run("A", "thread")
        try:
            result = await asyncio.wait_for(harness.service.act(grant, (press(),)), 3)
            assert result.code == "OK"
            assert result.observation is not None
            assert await asyncio.to_thread(entered.wait, 3)
        finally:
            release.set()
        await harness.observations.drain()
        assert result.code == "OK"
        assert (await harness.service.act(grant, (press(),))).code == "INPUT_DISABLED"
        await harness.close()

    asyncio.run(scenario())


def test_close_waits_for_open_before_closing_candidate(harness: Harness) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    class OpeningDevice(Device):
        """A connection that exposes an incomplete native open."""

        async def open(self) -> None:
            self.calls.append("opening")
            entered.set()
            await release.wait()
            self.calls.append("opened")

        async def close(self) -> None:
            if self.calls[-1] == "opening":
                message = "close attempted while open is incomplete"
                raise OSError(message)
            self.calls.append("close")

    async def scenario() -> None:
        device = OpeningDevice()
        connecting = asyncio.create_task(harness.service.connect(device))
        await entered.wait()
        closing = asyncio.create_task(harness.service.close())
        await asyncio.sleep(0)
        try:
            assert not closing.done()
        finally:
            release.set()
            await connecting
            await closing
            await harness.observations.drain()
        assert device.calls == ["opening", "opened", "close"]
        assert not harness.service.connected

    asyncio.run(scenario())


def test_stop_during_completed_neutral_write_preserves_connection(harness: Harness) -> None:
    entered, release = Event(), Event()

    class Port:
        """Hold the final neutral write while keeping actual SerialInput ownership."""

        def __init__(self) -> None:
            """Count writes and observe close without a real port."""
            self.writes = 0
            self.closed = False

        def write(self, data: bytes) -> int:
            """Return full delivery only after the controlled final release."""
            self.writes += 1
            if self.writes == 3:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError
            return len(data)

        def close(self) -> None:
            """Observe port closure."""
            self.closed = True

    async def scenario() -> None:
        port = Port()
        config = resolve_serial_config("COM3", "ch552", None)
        assert config is not None
        device = SerialInput(config, open_port=lambda _config: port)
        await harness.service.connect(device)
        grant = await harness.service.begin_run("A", "thread")
        operation = asyncio.create_task(harness.service.act(grant, (press(), press("B"))))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            harness.service.stop()
        finally:
            release.set()
        result = await operation
        try:
            assert result.code == "INTERRUPTED"
            assert result.neutral == "sent"
            assert result.completed_steps == 1
            assert harness.service.connected
            assert not port.closed
            assert port.writes == 3
        finally:
            await harness.close()

    asyncio.run(scenario())
