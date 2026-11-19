import asyncio
import json
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest

from gigas.game_io import observation as observation_module
from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.game_io.input.models import InputStep
from gigas.game_io.observation import Observation, ObservationError, ObservationResolution
from gigas.services.observation import ObservationService
from gigas.services.recording import RecordingService
from gigas.storage import records as records_module
from gigas.storage.input_events import OperationFinished, OperationStarted
from gigas.storage.records import GameIoRecordWriter, RecordBusyError, RecordError
from tests.integration.test_observation_storage import sample_frame

if TYPE_CHECKING:
    from gigas.game_io.capture.models import Frame


def test_submitted_records_keep_storage_order_and_drain_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = GameIoRecordWriter.create(tmp_path / "game")
    recording = RecordingService(writer)
    entered, release = Event(), Event()
    append = records_module._append_row

    def delayed_append(path: Path, row: bytes) -> None:
        if json.loads(row)["type"] == "input.operation_started":
            entered.set()
            if not release.wait(10):
                message = "test recording release"
                raise TimeoutError(message)
        append(path, row)

    monkeypatch.setattr(records_module, "_append_row", delayed_append)

    async def scenario() -> None:
        recording.submit_input(OperationStarted("operation-1", (InputStep("wait", 1),)))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            observation = Observation.from_frame(sample_frame())
            recording.submit_observation(observation)
            recording.submit_dialogue("dialogue.message", "公開応答")
            recording.submit_input(
                OperationFinished("operation-1", "interrupted", 0, phase="input", reason="stopped")
            )
            with pytest.raises(RecordBusyError):
                writer.close()
            assert not (writer.path / observation.image_path).exists()
            waiting = asyncio.create_task(recording.drain())
            await asyncio.sleep(0)
            assert not waiting.done()
        finally:
            release.set()
            await recording.drain()
        await waiting
        records = [
            json.loads(row) for row in (writer.path / "events.jsonl").read_bytes().splitlines()
        ]
        assert [row["type"] for row in records] == [
            "input.operation_started",
            "observation.recorded",
            "dialogue.message",
            "input.operation_finished",
        ]
        assert records[2]["payload"] == {"text": "公開応答"}
        assert (writer.path / observation.image_path).read_bytes() == observation.png_bytes
        assert not (writer.path / "memory").exists()

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        writer.close()


def test_record_failure_reaches_shared_failure_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    writer = GameIoRecordWriter.create(tmp_path / "game")
    recording = RecordingService(writer)
    failures: list[Exception] = []

    def fail_append(_path: Path, _row: bytes) -> None:
        message = "injected recording failure"
        raise OSError(message)

    monkeypatch.setattr(records_module, "_append_row", fail_append)

    async def scenario() -> None:
        recording.on_failure = failures.append
        recording.submit_input(OperationStarted("operation-1", (InputStep("wait", 1),)))
        recording.submit_dialogue("dialogue.message", "公開応答")
        await recording.drain()
        assert len(failures) == 2
        entries = [r for r in caplog.records if r.__dict__.get("event") == "recording.failed"]
        assert len(entries) == 2
        assert entries[0].__dict__["reference"] == "operation-1"
        assert entries[0].__dict__["path"] == str(writer.path)
        assert entries[0].exc_info is not None

    try:
        asyncio.run(scenario())
    finally:
        writer.close()


@pytest.mark.usefixtures("distinct_observation_times")
def test_observe_uses_cached_frame_and_retains_its_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = sample_frame()
    reader = CaptureReader(lambda: CaptureSnapshot(phase="streaming", frame=frame))
    writer = GameIoRecordWriter.create(tmp_path / "game")
    recording = RecordingService(writer)

    async def scenario() -> None:
        service = ObservationService(reader, recording)
        with monkeypatch.context() as patch:
            patch.setattr(observation_module, "MAX_PNG_BYTES", 1)
            with pytest.raises(ObservationError) as failed:
                await service.observe()
            assert failed.value.code == "OBSERVATION_ENCODING_ERROR"
        assert (writer.path / "events.jsonl").read_bytes() == b""
        first = await asyncio.wait_for(service.observe(), timeout=3)
        second = await asyncio.wait_for(service.observe(), timeout=3)
        assert first.source_frame == second.source_frame
        assert first.source_frame.sequence == frame.sequence
        assert first.captured_at == second.captured_at == frame.captured_at
        assert first.observation_id != second.observation_id
        await service.drain()
        await recording.drain()
        assert (writer.path / first.image_path).read_bytes() == first.png_bytes

    try:
        asyncio.run(scenario())
    finally:
        writer.close()


@pytest.mark.parametrize(
    ("boundary", "save_fails"), [("encode", False), ("publish", False), ("publish", True)]
)
@pytest.mark.usefixtures("distinct_observation_times")
def test_save_is_independent_and_cancellation_retains_work_until_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str, *, save_fails: bool
) -> None:
    writer = GameIoRecordWriter.create(tmp_path / "game")
    recording = RecordingService(writer)
    snapshot = CaptureSnapshot(phase="streaming", frame=sample_frame())
    entered, release = Event(), Event()
    publish = records_module._publish_image
    encode = Observation.from_frame
    failures: list[tuple[Observation, Exception]] = []
    shared_failures: list[Exception] = []

    def delayed_publish(path: Path, data: bytes) -> None:
        entered.set()
        if not release.wait(timeout=10) or save_fails:
            message = "image failure"
            raise OSError(message)
        publish(path, data)

    def delayed_encode(
        _cls: type[Observation], frame: "Frame", *, resolution: ObservationResolution = "full_hd"
    ) -> Observation:
        entered.set()
        if not release.wait(timeout=10):
            message = "encoding release deadline"
            raise TimeoutError(message)
        return encode(frame, resolution=resolution)

    if boundary == "encode":
        monkeypatch.setattr(Observation, "from_frame", classmethod(delayed_encode))
    else:
        monkeypatch.setattr(records_module, "_publish_image", delayed_publish)

    async def scenario() -> None:
        service = ObservationService(
            CaptureReader(lambda: snapshot),
            recording,
            on_save_error=lambda observation, error: failures.append((observation, error)),
        )
        recording.on_failure = shared_failures.append
        request = asyncio.create_task(service.observe())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            if boundary == "encode":
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
                with pytest.raises(ObservationError) as busy:
                    await service.observe()
                assert busy.value.code == "BUSY"
                # Encoding does not own the storage mutex.
                writer.record_dialogue("dialogue.message", "during encoding")
            else:
                first = await asyncio.wait_for(request, 3)
                second = await asyncio.wait_for(service.observe(), 3)
                assert first.observation_id != second.observation_id
                # Both observations returned while the first save is still blocked.
                assert not (writer.path / first.image_path).exists()
                assert not (writer.path / second.image_path).exists()
                with pytest.raises(RecordBusyError):
                    writer.close()
                with pytest.raises(RecordBusyError):
                    writer.record_dialogue("dialogue.message", "while busy")
            with pytest.raises(RecordError):
                GameIoRecordWriter.open(writer.path).close()
            waiting = asyncio.create_task(
                service.drain() if boundary == "encode" else recording.drain()
            )
            await asyncio.sleep(0)
            assert not waiting.done()
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
        finally:
            release.set()
            if not request.done():
                await request
            await service.drain()
            await recording.drain()
        if save_fails:
            assert len(failures) == 2
            assert shared_failures == [error for _, error in failures]
            assert {observation.observation_id for observation, _ in failures} == {
                first.observation_id,
                second.observation_id,
            }
            assert all("image failure" in str(error) for _, error in failures)
            assert (writer.path / "events.jsonl").read_bytes() == b""
        else:
            assert failures == []
            assert shared_failures == []
            records = [
                json.loads(line)
                for line in (writer.path / "events.jsonl").read_bytes().splitlines()
            ]
            if boundary == "encode":
                assert records.pop(0)["payload"] == {"text": "during encoding"}
            assert len(records) == (1 if boundary == "encode" else 2)
            assert all("thread_id" not in record["payload"] for record in records)
            assert all(
                (writer.path / record["payload"]["image_path"]).is_file() for record in records
            )

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        writer.close()


@pytest.mark.parametrize(
    "snapshot",
    [
        CaptureSnapshot(),
        CaptureSnapshot(phase="streaming"),
        CaptureSnapshot(phase="error", frame=sample_frame()),
        CaptureSnapshot(phase="stopping", frame=sample_frame()),
    ],
)
def test_unavailable_snapshot_does_not_save(tmp_path: Path, snapshot: CaptureSnapshot) -> None:
    writer = GameIoRecordWriter.create(tmp_path / "game")
    recording = RecordingService(writer)
    service = ObservationService(CaptureReader(lambda: snapshot), recording)
    try:
        with pytest.raises(ObservationError) as caught:
            asyncio.run(service.observe())
        assert caught.value.code == "DEVICE_UNAVAILABLE"
        assert (writer.path / "events.jsonl").read_bytes() == b""
    finally:
        writer.close()


def test_encoding_drain_failure_leaves_existing_save_owned_by_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = GameIoRecordWriter.create(tmp_path / "game")
    recording = RecordingService(writer)
    snapshot = CaptureSnapshot(phase="streaming", frame=sample_frame())
    entered, release = Event(), Event()
    publish = records_module._publish_image

    def delayed_publish(path: Path, data: bytes) -> None:
        entered.set()
        if not release.wait(timeout=10):
            message = "test release deadline"
            raise OSError(message)
        publish(path, data)

    monkeypatch.setattr(records_module, "_publish_image", delayed_publish)

    async def scenario() -> None:
        service = ObservationService(CaptureReader(lambda: snapshot), recording)
        observation = await asyncio.wait_for(service.observe(), 3)
        assert await asyncio.to_thread(entered.wait, 3)
        with monkeypatch.context() as patch:
            patch.setattr(observation_module, "MAX_PNG_BYTES", 1)
            with pytest.raises(ObservationError):
                await service.observe()
        with pytest.raises(ObservationError):
            await service.drain()
        waiting = asyncio.create_task(recording.drain())
        try:
            await asyncio.sleep(0)
            assert not waiting.done()
            assert not (writer.path / observation.image_path).exists()
        finally:
            release.set()
            await waiting
        assert (writer.path / observation.image_path).read_bytes() == observation.png_bytes

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        writer.close()
