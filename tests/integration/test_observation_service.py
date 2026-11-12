import asyncio
import json
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest

from gigas.game_io import observation as observation_module
from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.game_io.observation import Observation, ObservationError, ObservationResolution
from gigas.services import observation as service_module
from gigas.services.observation import ObservationInterrupted, ObservationService
from gigas.services.recording import RecordingService
from gigas.storage import project
from gigas.storage.input_events import RunFinished, RunStarted
from gigas.storage.project import ProjectBusyError, ProjectError, ProjectWriter
from tests.integration.test_observation_storage import sample_frame

if TYPE_CHECKING:
    from gigas.game_io.capture.models import Frame


@pytest.mark.parametrize("started", [90, 100])
def test_post_input_observation_waits_for_read_started_after_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    started: int,
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    recording = RecordingService(writer)
    crossing = replace(sample_frame(), read_started_ns=started, monotonic_ns=110)
    fresh = replace(crossing, sequence=8, read_started_ns=102, monotonic_ns=120)
    reads = 0

    def snapshot() -> CaptureSnapshot:
        nonlocal reads
        reads += 1
        return CaptureSnapshot(
            phase="streaming", generation=2, frame=crossing if reads <= 2 else fresh
        )

    reader = CaptureReader(snapshot)
    monkeypatch.setattr(service_module, "monotonic_ns", lambda: 130)

    async def scenario() -> None:
        service = ObservationService(reader, recording)
        latest = await service.observe()
        assert latest.source_frame.sequence == 7
        after = await service.observe_after(2, 100)
        assert reads == 3
        assert after.source_frame.sequence == 8
        await service.drain()
        await recording.drain()
        assert (writer.path / after.image_path).read_bytes() == after.png_bytes

    try:
        asyncio.run(scenario())
    finally:
        writer.close()


@pytest.mark.parametrize(
    ("phase", "generation", "now", "code"),
    [
        ("stopping", 2, 101, "DEVICE_UNAVAILABLE"),
        ("streaming", 3, 101, "DEVICE_UNAVAILABLE"),
        ("streaming", 2, 5_000_000_101, "OBSERVATION_TIMEOUT"),
    ],
)
def test_post_input_observation_rejects_loss_change_or_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    generation: int,
    now: int,
    code: str,
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    recording = RecordingService(writer)
    snapshot = CaptureSnapshot(
        phase="stopping" if phase == "stopping" else "streaming",
        generation=generation,
        frame=sample_frame(),
    )
    reader = CaptureReader(lambda: snapshot)
    monkeypatch.setattr(service_module, "monotonic_ns", lambda: now)

    async def scenario() -> None:
        service = ObservationService(reader, recording)
        with pytest.raises(ObservationError) as failure:
            await service.observe_after(2, 100)
        assert failure.value.code == code
        await service.drain()
        await recording.drain()
        assert (writer.path / "events.jsonl").read_bytes() == b""

    try:
        asyncio.run(scenario())
    finally:
        writer.close()


def test_post_input_cancel_recovers_encoding_without_waiting_for_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    recording = RecordingService(writer)
    encoding, release_encoding, saving, release_save = Event(), Event(), Event(), Event()
    encode, publish = Observation.from_frame, project._publish_image
    frame = sample_frame()
    reader = CaptureReader(lambda: CaptureSnapshot(phase="streaming", generation=2, frame=frame))
    monkeypatch.setattr(service_module, "monotonic_ns", lambda: frame.monotonic_ns)

    def delayed_encode(
        _cls: type[Observation],
        source: "Frame",
        *,
        resolution: ObservationResolution = "full_hd",
    ) -> Observation:
        encoding.set()
        if not release_encoding.wait(5):
            raise TimeoutError
        return encode(source, resolution=resolution)

    def delayed_save(path: Path, data: bytes) -> None:
        saving.set()
        if not release_save.wait(5):
            raise TimeoutError
        publish(path, data)

    monkeypatch.setattr(Observation, "from_frame", classmethod(delayed_encode))
    monkeypatch.setattr(project, "_publish_image", delayed_save)

    async def scenario() -> None:
        service = ObservationService(reader, recording)
        task = asyncio.create_task(service.observe_after(2, frame.read_started_ns - 1))
        try:
            assert await asyncio.to_thread(encoding.wait, 2)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            with pytest.raises(ObservationError) as busy:
                await service.observe()
            assert busy.value.code == "BUSY"
            release_encoding.set()
            with pytest.raises(ObservationInterrupted) as interrupted:
                await asyncio.wait_for(task, 2)
            assert await asyncio.to_thread(saving.wait, 2)
            assert not list((writer.path / "observations").glob("*.png"))
        finally:
            release_encoding.set()
            release_save.set()
            await service.drain()
            await recording.drain()
        observation = interrupted.value.observation
        assert (writer.path / observation.image_path).read_bytes() == observation.png_bytes

    try:
        asyncio.run(scenario())
    finally:
        release_encoding.set()
        release_save.set()
        writer.close()


def test_cancelled_start_keeps_storage_order_and_drain_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    recording = RecordingService(writer)
    entered, release = Event(), Event()
    append = project._append_row

    def delayed_append(path: Path, row: bytes) -> None:
        if json.loads(row)["type"] == "input.run_started":
            entered.set()
            if not release.wait(10):
                message = "test recording release"
                raise TimeoutError(message)
        append(path, row)

    monkeypatch.setattr(project, "_append_row", delayed_append)

    async def scenario() -> None:
        request = asyncio.create_task(
            recording.record_input(RunStarted("req", "run", "thread", "A"))
        )
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(request, 3)
            observation = Observation.from_frame(sample_frame())
            recording.submit_observation(observation)
            recording.submit_input(RunFinished("req", "run", "stopped"))
            with pytest.raises(ProjectBusyError):
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
            "input.run_started",
            "observation.recorded",
            "input.run_finished",
        ]
        assert (writer.path / observation.image_path).read_bytes() == observation.png_bytes

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        writer.close()


def test_record_failure_reaches_waiter_and_shared_failure_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    recording = RecordingService(writer)
    failures: list[Exception] = []

    def fail_append(_path: Path, _row: bytes) -> None:
        message = "injected recording failure"
        raise OSError(message)

    monkeypatch.setattr(project, "_append_row", fail_append)

    async def scenario() -> None:
        recording.on_failure = failures.append
        with pytest.raises(ProjectError, match="injected recording failure"):
            await recording.record_input(RunStarted("req", "run", "thread", "A"))
        recording.submit_input(RunFinished("req", "run", "start_failed"))
        await recording.drain()
        assert len(failures) == 2

    try:
        asyncio.run(scenario())
    finally:
        writer.close()


def test_observe_uses_cached_frame_and_retains_its_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = sample_frame()
    reader = CaptureReader(lambda: CaptureSnapshot(phase="streaming", frame=frame))
    writer = ProjectWriter.create(tmp_path / "game")
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
def test_save_is_independent_and_cancellation_retains_work_until_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str, *, save_fails: bool
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    recording = RecordingService(writer)
    snapshot = CaptureSnapshot(phase="streaming", frame=sample_frame())
    entered, release = Event(), Event()
    publish = project._publish_image
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
        monkeypatch.setattr(project, "_publish_image", delayed_publish)

    async def scenario() -> None:
        service = ObservationService(
            CaptureReader(lambda: snapshot),
            recording,
            on_save_error=lambda observation, error: failures.append((observation, error)),
            thread_id="thread-1",
        )
        recording.on_failure = shared_failures.append
        request = asyncio.create_task(service.observe())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            service.thread_id = "thread-2"
            if boundary == "encode":
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
                with pytest.raises(ObservationError) as busy:
                    await service.observe()
                assert busy.value.code == "BUSY"
                writer.reload()  # Encoding does not own the storage mutex.
            else:
                first = await asyncio.wait_for(request, 3)
                second = await asyncio.wait_for(service.observe(), 3)
                assert first.observation_id != second.observation_id
                # Both observations returned while the first save is still blocked.
                assert not (writer.path / first.image_path).exists()
                assert not (writer.path / second.image_path).exists()
                with pytest.raises(ProjectBusyError):
                    writer.close()
                with pytest.raises(ProjectBusyError):
                    writer.reload()
            with pytest.raises(ProjectError):
                ProjectWriter.open(writer.path).close()
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
            assert len(records) == (1 if boundary == "encode" else 2)
            assert [record["payload"]["thread_id"] for record in records] == (
                ["thread-1"] if boundary == "encode" else ["thread-1", "thread-2"]
            )
            assert [record["sequence"] for record in records] == list(range(1, len(records) + 1))
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
    writer = ProjectWriter.create(tmp_path / "game")
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
    writer = ProjectWriter.create(tmp_path / "game")
    recording = RecordingService(writer)
    snapshot = CaptureSnapshot(phase="streaming", frame=sample_frame())
    entered, release = Event(), Event()
    publish = project._publish_image

    def delayed_publish(path: Path, data: bytes) -> None:
        entered.set()
        if not release.wait(timeout=10):
            message = "test release deadline"
            raise OSError(message)
        publish(path, data)

    monkeypatch.setattr(project, "_publish_image", delayed_publish)

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
