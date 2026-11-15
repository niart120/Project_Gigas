import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
import pytest

from gigas.game_io.capture.models import Frame
from gigas.game_io.observation import Observation
from gigas.storage.records import GameIoRecordWriter


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
        (1920, 1080, "sd", 720, 405),
        (1440, 960, "sd", 720, 480),
        (640, 480, "sd", 640, 480),
        (240, 160, "sd", 240, 160),
        (480, 720, "sd", 320, 480),
    ],
)
def test_record_preserves_pixels_bytes_id_and_source_time(
    tmp_path: Path,
    width: int,
    height: int,
    resolution: Literal["original", "full_hd", "hd", "sd"] | None,
    output_width: int,
    output_height: int,
) -> None:
    writer = GameIoRecordWriter.create(tmp_path / "game")
    frame = sample_frame(width, height)
    if (width, height) != (output_width, output_height):
        # Four broad color regions reveal cropping, channel swaps and aspect distortion.
        pixels = np.zeros((height, width, 3), dtype=np.uint8)
        pixels[: height // 2, : width // 2] = [255, 0, 0]
        pixels[: height // 2, width // 2 :] = [0, 255, 0]
        pixels[height // 2 :, : width // 2] = [0, 0, 255]
        pixels[height // 2 :, width // 2 :] = [255, 255, 255]
        frame = replace(frame, bgr=pixels.tobytes())
    try:
        first = (
            Observation.from_frame(frame)
            if resolution is None
            else Observation.from_frame(frame, resolution=resolution)
        )
        second = Observation.from_frame(frame)
        writer.record(first)
        writer.record(second)
        assert first.observation_id != second.observation_id
        assert first.captured_at == frame.captured_at
        assert (first.source_frame.generation, first.source_frame.sequence) == (2, 7)
        assert (first.source_frame.width, first.source_frame.height) == (width, height)
        assert (first.width, first.height, first.mime_type) == (
            output_width,
            output_height,
            "image/png",
        )
        assert (writer.path / first.image_path).read_bytes() == first.png_bytes
        decoded = cv2.imdecode(np.frombuffer(first.png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        assert decoded is not None
        assert decoded.shape == (output_height, output_width, 3)
        if (width, height) == (output_width, output_height):
            assert decoded.tobytes() == frame.bgr
        else:
            assert decoded[0, 0].tolist() == [255, 0, 0]
            assert decoded[0, -1].tolist() == [0, 255, 0]
            assert decoded[-1, 0].tolist() == [0, 0, 255]
            assert decoded[-1, -1].tolist() == [255, 255, 255]
            assert decoded[output_height // 4, output_width // 4].tolist() == [255, 0, 0]
            assert decoded[3 * output_height // 4, 3 * output_width // 4].tolist() == [
                255,
                255,
                255,
            ]
        assert len(first.png_bytes) <= 32 * 1024 * 1024
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
    original = log.read_bytes()
    (writer.path / first.image_path).unlink()
    reopened = GameIoRecordWriter.open(writer.path)
    try:
        assert log.read_bytes() == original
        reopened.record(Observation.from_frame(frame))
        assert log.read_bytes().startswith(original)
        records = [
            json.loads(line) for line in (writer.path / "events.jsonl").read_bytes().splitlines()
        ]
        assert len(records) == 3
    finally:
        reopened.close()
