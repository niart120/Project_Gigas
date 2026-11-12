import asyncio
import json
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest

from gigas.game_io import observation as observation_module
from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.game_io.observation import Observation, ObservationError, ObservationResolution
from gigas.services.observation import ObservationService
from gigas.storage import project
from gigas.storage.project import ProjectBusyError, ProjectError, ProjectWriter
from tests.integration.test_observation_storage import sample_frame

if TYPE_CHECKING:
    from gigas.game_io.capture.models import Frame


def test_observe_uses_cached_frame_and_retains_its_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = sample_frame()
    reader = CaptureReader(lambda: CaptureSnapshot(phase="streaming", frame=frame))
    writer = ProjectWriter.create(tmp_path / "game")

    async def scenario() -> None:
        service = ObservationService(reader, writer)
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
    snapshot = CaptureSnapshot(phase="streaming", frame=sample_frame())
    entered, release = Event(), Event()
    publish = project._publish_image
    encode = Observation.from_frame
    failures: list[tuple[Observation, Exception]] = []

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
            writer,
            on_save_error=lambda observation, error: failures.append((observation, error)),
            thread_id="thread-1",
        )
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
            waiting = asyncio.create_task(service.drain())
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
        if save_fails:
            assert len(failures) == 2
            assert {observation.observation_id for observation, _ in failures} == {
                first.observation_id,
                second.observation_id,
            }
            assert all("image failure" in str(error) for _, error in failures)
            assert (writer.path / "events.jsonl").read_bytes() == b""
        else:
            assert failures == []
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
    service = ObservationService(CaptureReader(lambda: snapshot), writer)
    try:
        with pytest.raises(ObservationError) as caught:
            asyncio.run(service.observe())
        assert caught.value.code == "DEVICE_UNAVAILABLE"
        assert (writer.path / "events.jsonl").read_bytes() == b""
    finally:
        writer.close()


def test_drain_waits_for_existing_save_after_later_encoding_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
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
        service = ObservationService(CaptureReader(lambda: snapshot), writer)
        observation = await asyncio.wait_for(service.observe(), 3)
        assert await asyncio.to_thread(entered.wait, 3)
        with monkeypatch.context() as patch:
            patch.setattr(observation_module, "MAX_PNG_BYTES", 1)
            with pytest.raises(ObservationError):
                await service.observe()
        waiting = asyncio.create_task(service.drain())
        try:
            await asyncio.sleep(0)
            assert not waiting.done()
            assert not (writer.path / observation.image_path).exists()
        finally:
            release.set()
            with pytest.raises(ObservationError):
                await waiting
        assert (writer.path / observation.image_path).read_bytes() == observation.png_bytes

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        writer.close()
