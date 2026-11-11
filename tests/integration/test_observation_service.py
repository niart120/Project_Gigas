import asyncio
from pathlib import Path

import pytest

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.game_io.observation import ObservationError
from gigas.services.observation import ObservationService
from gigas.storage.project import ProjectWriter
from tests.integration.test_observation_storage import sample_frame


def test_observe_uses_cached_frame_and_retains_its_time(tmp_path: Path) -> None:
    frame = sample_frame()
    reader = CaptureReader(lambda: CaptureSnapshot(phase="streaming", frame=frame))
    writer = ProjectWriter.create(tmp_path / "game")

    async def scenario() -> None:
        service = ObservationService(reader, writer)
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
