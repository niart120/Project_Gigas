import asyncio
import json
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest

from gigas.game_io import observation as observation_module
from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.game_io.observation import Observation, ObservationError
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
        assert first.frame is second.frame is frame
        assert first.observation_id != second.observation_id
        assert (writer.path / first.image_path).read_bytes() == first.png_bytes

    try:
        asyncio.run(scenario())
    finally:
        writer.close()


@pytest.mark.parametrize(
    ("boundary", "save_fails"), [("encode", False), ("publish", False), ("publish", True)]
)
def test_cancellation_retains_worker_slot_and_project_until_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str, *, save_fails: bool
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    snapshot = CaptureSnapshot(phase="streaming", frame=sample_frame())
    entered, release = Event(), Event()
    publish = project._publish_image
    encode = Observation.from_frame

    def delayed_publish(path: Path, data: bytes) -> None:
        entered.set()
        if not release.wait(timeout=10) or save_fails:
            message = "image failure"
            raise OSError(message)
        publish(path, data)

    def delayed_encode(_cls: type[Observation], frame: "Frame") -> Observation:
        entered.set()
        if not release.wait(timeout=10):
            message = "encoding release deadline"
            raise TimeoutError(message)
        return encode(frame)

    if boundary == "encode":
        monkeypatch.setattr(Observation, "from_frame", classmethod(delayed_encode))
    else:
        monkeypatch.setattr(project, "_publish_image", delayed_publish)

    async def scenario() -> None:
        service = ObservationService(CaptureReader(lambda: snapshot), writer)
        request = asyncio.create_task(service.observe())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            with pytest.raises(ObservationError) as busy:
                await service.observe()
            assert busy.value.code == "BUSY"
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            with pytest.raises(ObservationError) as busy:
                await service.observe()
            assert busy.value.code == "BUSY"
            if boundary == "publish":
                with pytest.raises(ProjectBusyError):
                    writer.close()
                with pytest.raises(ProjectBusyError):
                    writer.reload()
            else:
                writer.reload()  # Encoding holds the observation slot, not the storage mutex.
            with pytest.raises(ProjectError):
                ProjectWriter.open(writer.path)
            waiting = asyncio.create_task(service.drain())
            await asyncio.sleep(0)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
        finally:
            release.set()
            if not request.done():
                await request
        if save_fails:
            with pytest.raises(ProjectError, match="image failure"):
                await service.drain()
            assert (writer.path / "events.jsonl").read_bytes() == b""
        else:
            await service.drain()
            first = json.loads((writer.path / "events.jsonl").read_bytes())
            assert (writer.path / first["payload"]["image_path"]).is_file()
            second = await service.observe()
            assert first["payload"]["observation_id"] != second.observation_id

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
