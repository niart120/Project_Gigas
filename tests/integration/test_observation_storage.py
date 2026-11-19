import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from gigas.game_io import observation as observation_module
from gigas.game_io.capture.models import Frame
from gigas.game_io.observation import Observation
from gigas.storage.records import GameIoRecordWriter, RecordError


def sample_frame(width: int = 16, height: int = 16) -> Frame:
    pixels = np.random.default_rng(41).integers(0, 256, (height, width, 3), dtype=np.uint8)
    return Frame(pixels.tobytes(), width, height, 2, 7, datetime(2000, 1, 1, tzinfo=UTC), 123)


@pytest.mark.parametrize(
    ("width", "height", "resolution", "output_width", "output_height"),
    [
        (16, 16, None, 16, 16),
        (3840, 2160, "original", 3840, 2160),
        (3840, 2160, None, 1920, 1080),
        (3840, 2160, "hd", 1280, 720),
        (1920, 1440, "hd", 960, 720),
        (1921, 1081, "hd", 1279, 720),
        (1920, 1080, "960x540", 960, 540),
        (1440, 960, "960x540", 810, 540),
        (640, 480, "960x540", 640, 480),
        (240, 160, "960x540", 240, 160),
        (1920, 1080, "640x360", 640, 360),
        (640, 480, "640x360", 480, 360),
        (240, 160, "640x360", 240, 160),
        (480, 720, "640x360", 240, 360),
        (480, 720, "960x540", 360, 540),
    ],
)
def test_observation_resolution_preserves_the_full_frame(
    width: int,
    height: int,
    resolution: Literal["original", "full_hd", "hd", "960x540", "640x360"] | None,
    output_width: int,
    output_height: int,
) -> None:
    frame = sample_frame(width, height)
    if (width, height) != (output_width, output_height):
        # Use distinct colors inside the image as well as at each edge.
        pixels = np.zeros((height, width, 3), dtype=np.uint8)
        pixels[: height // 2, : width // 2] = [255, 0, 0]
        pixels[: height // 2, width // 2 :] = [0, 255, 0]
        pixels[height // 2 :, : width // 2] = [0, 0, 255]
        pixels[height // 2 :, width // 2 :] = [255, 255, 255]
        # Only the original border carries these pixels.
        pixels[: height // 16, :] = [10, 20, 30]
        pixels[-(height // 16) :, :] = [40, 50, 60]
        pixels[:, : width // 16] = [70, 80, 90]
        pixels[:, -(width // 16) :] = [100, 110, 120]
        frame = replace(frame, bgr=pixels.tobytes())
    first = (
        Observation.from_frame(frame)
        if resolution is None
        else Observation.from_frame(frame, resolution=resolution)
    )
    decoded = cv2.imdecode(np.frombuffer(first.png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None
    assert decoded.shape == (output_height, output_width, 3)
    if (width, height) == (output_width, output_height):
        assert decoded.tobytes() == frame.bgr
    else:
        assert decoded[0, output_width // 2].tolist() == [10, 20, 30]
        assert decoded[-1, output_width // 2].tolist() == [40, 50, 60]
        assert decoded[output_height // 2, 0].tolist() == [70, 80, 90]
        assert decoded[output_height // 2, -1].tolist() == [100, 110, 120]
        assert decoded[output_height // 4, output_width // 4].tolist() == [255, 0, 0]
        assert decoded[output_height // 4, 3 * output_width // 4].tolist() == [0, 255, 0]
        assert decoded[3 * output_height // 4, output_width // 4].tolist() == [0, 0, 255]
        assert decoded[3 * output_height // 4, 3 * output_width // 4].tolist() == [
            255,
            255,
            255,
        ]
    assert len(first.png_bytes) <= 32 * 1024 * 1024


def test_record_preserves_bytes_identity_and_source_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generated_at = datetime(2026, 11, 18, 3, 4, 2, 996_789, tzinfo=UTC)
    clock = Mock(wraps=datetime)
    clock.now.side_effect = [
        generated_at,
        generated_at.replace(microsecond=996_999),
        generated_at.replace(microsecond=997_000),
        generated_at.replace(microsecond=998_000),
    ]
    monkeypatch.setattr(observation_module, "datetime", clock)
    width, height = 640, 480
    output_width, output_height = 480, 360
    frame = sample_frame(width, height)
    writer = GameIoRecordWriter.create(tmp_path / "game")
    try:
        first = Observation.from_frame(frame, resolution="640x360")
        collision = Observation.from_frame(frame)
        second = Observation.from_frame(frame)
        clock.now.assert_called_with(UTC)
        assert first.observation_id == collision.observation_id == "20261118T030402-996Z"
        assert second.observation_id == "20261118T030402-997Z"
        assert collision.png_bytes != first.png_bytes
        writer.record(first)
        with pytest.raises(RecordError):
            writer.record(collision)
        writer.record(second)
        assert first.image_path == "observations/20261118T030402-996Z.png"
        assert first.captured_at == frame.captured_at
        assert (first.source_frame.generation, first.source_frame.sequence) == (2, 7)
        assert (first.source_frame.width, first.source_frame.height) == (width, height)
        assert (first.width, first.height, first.mime_type) == (
            output_width,
            output_height,
            "image/png",
        )
        assert (writer.path / first.image_path).read_bytes() == first.png_bytes
        records = [
            json.loads(line) for line in (writer.path / "events.jsonl").read_bytes().splitlines()
        ]
        assert len(records) == 2
        assert records[0]["type"] == "observation.recorded"
        assert records[0]["payload"] == {
            "observation_id": first.observation_id,
            "captured_at": "2000-01-01T00:00:00Z",
            "image_path": first.image_path,
            "image": {"width": output_width, "height": output_height},
            "source_frame": {"width": width, "height": height},
        }
        assert datetime.fromisoformat(records[0]["recorded_at"]) > frame.captured_at
    finally:
        writer.close()
    log = writer.path / "events.jsonl"
    legacy_id = "019aa000-0000-7000-8000-000000000000"
    legacy_path = f"observations/{legacy_id}.png"
    legacy_record = records[0] | {
        "payload": records[0]["payload"] | {"observation_id": legacy_id, "image_path": legacy_path}
    }
    with log.open("ab") as stream:
        stream.write(json.dumps(legacy_record).encode("utf-8") + b"\n")
    original = log.read_bytes()
    (writer.path / first.image_path).unlink()
    legacy_image = writer.path / legacy_path
    legacy_image.write_bytes(first.png_bytes)
    reopened = GameIoRecordWriter.open(writer.path)
    try:
        assert log.read_bytes() == original
        reopened.record(Observation.from_frame(frame))
        assert log.read_bytes().startswith(original)
        assert legacy_image.read_bytes() == first.png_bytes
        assert not (writer.path / first.image_path).exists()
        records = [
            json.loads(line) for line in (writer.path / "events.jsonl").read_bytes().splitlines()
        ]
        assert len(records) == 4
    finally:
        reopened.close()
