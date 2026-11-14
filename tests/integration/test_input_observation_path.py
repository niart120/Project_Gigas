import asyncio
import json
from contextlib import closing
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest

from gigas.game_io import observation as observation_module
from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.game_io.input.config import resolve_serial_config
from gigas.game_io.input.models import parse_sequence
from gigas.game_io.input.serial import SerialInput
from gigas.game_io.observation import Observation, ObservationError
from gigas.services._game_io_tools import act_and_observe
from gigas.services.input import InputService, OperationResult
from gigas.services.observation import ObservationService
from gigas.services.recording import RecordingService
from gigas.storage import project
from gigas.storage.input_events import OperationStarted
from gigas.storage.project import ProjectWriter
from tests.integration.test_input_service import Harness, press
from tests.integration.test_observation_storage import sample_frame

if TYPE_CHECKING:
    from gigas.game_io.capture.models import Frame
    from gigas.game_io.input.models import InputStep
    from gigas.game_io.observation import ObservationResolution
    from gigas.storage.input_events import InputEvent


class Port:
    """Capture actual encoded writes without contacting hardware."""

    def __init__(self) -> None:
        """Prepare write history and close evidence."""
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> int:
        """Accept a full local write."""
        self.writes.append(data)
        return len(data)

    def close(self) -> None:
        """Mark the transport as closed."""
        self.closed = True


def test_serial_sequence_and_latest_observation_share_recorded_outcome(tmp_path: Path) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    port = Port()
    original = sample_frame()

    def snapshot() -> CaptureSnapshot:
        assert len(port.writes) == 3  # Initial neutral, press, final neutral.
        return CaptureSnapshot(phase="streaming", generation=original.generation, frame=original)

    async def scenario() -> None:
        reader = CaptureReader(snapshot)
        recording = RecordingService(writer)
        observations = ObservationService(reader, recording)
        service = InputService()
        config = resolve_serial_config("COM3", "ch552", None)
        assert config is not None
        try:
            await service.connect(SerialInput(config, open_port=lambda _config: port))
            sequence = parse_sequence(
                [
                    {"kind": "press", "buttons": ["A"], "duration_ms": 1},
                    {"kind": "wait", "duration_ms": 1},
                ],
            )
            result, observation = await act_and_observe(
                sequence,
                start_input=service.start,
                observations=observations,
                recording=recording,
                thread_id="real-thread-id",
            )
            assert result.code == "OK"
            assert result.completed_steps == 2
            assert result.neutral == "sent"
            assert isinstance(observation, Observation)
            assert observation.source_frame.sequence == original.sequence
            assert observation.source_frame.generation == original.generation
        finally:
            await service.close()
            await observations.drain()
            await recording.drain()
        rows = [json.loads(row) for row in (writer.path / "events.jsonl").read_bytes().splitlines()]
        assert [row["type"] for row in rows] == [
            "input.operation_started",
            "observation.recorded",
            "input.operation_finished",
        ]
        recorded = rows[1]["payload"]
        finished = rows[2]["payload"]
        assert finished["operation_id"] == result.operation_id
        assert (
            finished["observation_id"] == recorded["observation_id"] == observation.observation_id
        )
        assert (writer.path / recorded["image_path"]).read_bytes() == observation.png_bytes
        assert rows[0]["payload"]["sequence"] == [step.to_json() for step in sequence]
        assert rows[0]["payload"]["thread_id"] == "real-thread-id"
        assert all("run_id" not in row["payload"] for row in rows)
        assert all(
            "port" not in row["payload"] and "baudrate" not in row["payload"] for row in rows
        )

    try:
        asyncio.run(scenario())
    finally:
        writer.close()
    neutral = bytes.fromhex("AB 00 00 08 80 80 80 80 00 00 00")
    assert port.writes == [neutral, bytes.fromhex("AB 04 00 08 80 80 80 80 00 00 00"), neutral]
    assert port.closed


def test_missing_image_preserves_input_and_accepts_next_sequence(tmp_path: Path) -> None:
    async def scenario(writer: ProjectWriter) -> None:
        h = Harness(writer)
        h.snapshot = CaptureSnapshot(phase="idle", generation=0)
        try:
            await h.service.connect(h.device)
            for _ in range(2):
                result, image = await act_and_observe(
                    (press(),),
                    start_input=h.service.start,
                    observations=h.observations,
                    recording=h.recording,
                    thread_id="thread",
                )
                assert result.code == "OK"
                assert result.neutral == "sent"
                assert isinstance(image, ObservationError)
            await h.recording.drain()
            finished = [
                r["payload"] for r in h.records() if r["type"] == "input.operation_finished"
            ]
            assert len(finished) == 2
            for row in finished:
                assert isinstance(row, dict)
                assert row["result"] == "completed"
                assert row["phase"] == "observe"
        finally:
            await h.close()

    with closing(ProjectWriter.create(tmp_path / "game")) as writer:
        asyncio.run(scenario(writer))


def test_cancelled_observation_leaves_next_input_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    original = Observation.from_frame

    def encode(frame: "Frame", resolution: "ObservationResolution") -> Observation:
        entered.set()
        assert release.wait(5)
        return original(frame, resolution=resolution)

    monkeypatch.setattr(Observation, "from_frame", encode)

    async def scenario(writer: ProjectWriter) -> None:
        h = Harness(writer)
        try:
            await h.service.connect(h.device)
            response = asyncio.create_task(
                act_and_observe(
                    (press(),),
                    start_input=h.service.start,
                    observations=h.observations,
                    recording=h.recording,
                    thread_id="thread",
                )
            )
            assert await asyncio.to_thread(entered.wait, 3)
            h.device.pressed.clear()
            accepted = h.service.start((press("B", 60000),))
            assert isinstance(accepted, tuple)
            _, operation = accepted
            await h.device.pressed.wait()
            response.cancel()
            with pytest.raises(asyncio.CancelledError):
                await response
            assert not operation.done()
            h.service.stop()
            await asyncio.wait_for(h.service.drain_input(), 1)
            assert (await operation).neutral == "sent"
            await h.recording.drain()
            finished = [
                r["payload"] for r in h.records() if r["type"] == "input.operation_finished"
            ]
            assert len(finished) == 1
            assert isinstance(finished[0], dict)
            assert finished[0]["result"] == "completed"
            assert finished[0]["phase"] == "observe"
        finally:
            release.set()
            await h.close()

    with closing(ProjectWriter.create(tmp_path / "game")) as writer:
        asyncio.run(scenario(writer))


def test_cancelled_input_is_released_and_recorded(tmp_path: Path) -> None:
    async def scenario(writer: ProjectWriter) -> None:
        h = Harness(writer)
        try:
            await h.service.connect(h.device)
            response = asyncio.create_task(
                act_and_observe(
                    (press("A", 60000),),
                    start_input=h.service.start,
                    observations=h.observations,
                    recording=h.recording,
                    thread_id="thread",
                )
            )
            await h.device.pressed.wait()
            response.cancel()
            with pytest.raises(asyncio.CancelledError):
                await response
            await h.recording.drain()
            final = h.records()[-1]["payload"]
            assert isinstance(final, dict)
            assert final["result"] == "interrupted"
            assert final["neutral"] == "sent"
            assert final["input_sent"] == "sent"
        finally:
            await h.close()

    with closing(ProjectWriter.create(tmp_path / "game")) as writer:
        asyncio.run(scenario(writer))


def test_encoding_failure_preserves_input_and_allows_following_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario(writer: ProjectWriter) -> None:
        h = Harness(writer)
        try:
            await h.service.connect(h.device)
            with monkeypatch.context() as patch:
                patch.setattr(observation_module, "MAX_PNG_BYTES", 1)
                result, image = await act_and_observe(
                    (press(),),
                    start_input=h.service.start,
                    observations=h.observations,
                    recording=h.recording,
                    thread_id="thread",
                )
            assert result.code == "OK"
            assert result.completed_steps == 1
            assert result.input_sent == "sent"
            assert result.neutral == "sent"
            assert isinstance(image, ObservationError)
            assert image.code == "OBSERVATION_ENCODING_ERROR"
            following, image = await act_and_observe(
                (press("B"),),
                start_input=h.service.start,
                observations=h.observations,
                recording=h.recording,
                thread_id="thread",
            )
            assert following.code == "OK"
            assert isinstance(image, Observation)
            await h.recording.drain()
            finished = [
                row["payload"] for row in h.records() if row["type"] == "input.operation_finished"
            ]
            assert len(finished) == 2
            first = finished[0]
            assert isinstance(first, dict)
            assert first["operation_id"] == result.operation_id
            assert first["result"] == "completed"
            assert first["phase"] == "observe"
            assert "observation_id" not in first
        finally:
            await h.close()

    with closing(ProjectWriter.create(tmp_path / "game")) as writer:
        asyncio.run(scenario(writer))


@pytest.mark.parametrize("boundary", ["input_start", "image"])
def test_save_failure_preserves_returned_input_image_and_next_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    record_input = ProjectWriter.record_input

    def fail_start(writer: ProjectWriter, event: "InputEvent") -> None:
        if isinstance(event, OperationStarted):
            message = "injected operation start save failure"
            raise OSError(message)
        record_input(writer, event)

    def fail_image(_path: Path, _image: bytes) -> None:
        message = "injected image save failure"
        raise OSError(message)

    if boundary == "input_start":
        monkeypatch.setattr(ProjectWriter, "record_input", fail_start)
    else:
        monkeypatch.setattr(project, "_publish_image", fail_image)

    async def scenario(writer: ProjectWriter) -> None:
        h = Harness(writer)
        try:
            await h.service.connect(h.device)
            for button in ("A", "B"):
                result, image = await act_and_observe(
                    (press(button),),
                    start_input=h.service.start,
                    observations=h.observations,
                    recording=h.recording,
                    thread_id="thread",
                )
                assert result.code == "OK"
                assert result.completed_steps == 1
                assert result.input_sent == "sent"
                assert result.neutral == "sent"
                assert isinstance(image, Observation)
                assert image.png_bytes
                await h.observations.drain()
                await h.recording.drain()
                # Later persistence failure must not mutate an already returned result.
                assert result.code == "OK"
                assert h.failures
                assert h.service.connected
            assert len(h.failures) == 2
            assert all("save failure" in str(error) for error in h.failures)
        finally:
            await h.close()

    with closing(ProjectWriter.create(tmp_path / "game")) as writer:
        asyncio.run(scenario(writer))


def test_stop_before_input_starts_records_interruption_without_delivery(tmp_path: Path) -> None:
    async def scenario(writer: ProjectWriter) -> None:
        h = Harness(writer)

        def start_then_stop(
            sequence: "tuple[InputStep, ...]",
        ) -> tuple[str, asyncio.Task[OperationResult]] | OperationResult:
            accepted = h.service.start(sequence)
            h.service.stop()
            return accepted

        try:
            await h.service.connect(h.device)
            result, image = await act_and_observe(
                (press(),),
                start_input=start_then_stop,
                observations=h.observations,
                recording=h.recording,
                thread_id="thread",
            )
            assert result.code == "INTERRUPTED"
            assert result.operation_id is not None
            assert image is None
            assert result.input_sent == "none"
            assert result.neutral == "not_needed"
            assert h.device.calls == ["open"]
            await h.recording.drain()
            rows = h.records()
            assert [row["type"] for row in rows] == [
                "input.operation_started",
                "input.operation_finished",
            ]
            for row in rows:
                payload = row["payload"]
                assert isinstance(payload, dict)
                assert payload["operation_id"] == result.operation_id
            final = rows[-1]["payload"]
            assert isinstance(final, dict)
            assert final["result"] == "interrupted"
            assert final["input_sent"] == "none"
            assert final["neutral"] == "not_needed"
            assert "observation_id" not in final
        finally:
            await h.close()

    with closing(ProjectWriter.create(tmp_path / "game")) as writer:
        asyncio.run(scenario(writer))
