from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from gigas.game_io.capture.frames import FrameStore
from gigas.game_io.capture.models import CaptureError, Frame


def frame(generation: int = 1, sequence: int = 1, value: int = 2) -> Frame:
    return Frame(bytes([value]) * 6, 2, 1, generation, sequence, datetime.now(UTC), sequence)


@pytest.mark.parametrize("attribute", ["sequence", "bgr", "monotonic_ns"])
def test_latest_frame_is_immutable_and_keeps_its_original_clock(attribute: str) -> None:
    store = FrameStore()
    store.begin(1)
    original = frame()
    assert store.publish(original)
    snapshot = store.latest()
    assert snapshot is original
    edited = bytearray(original.bgr)
    edited[0] = 99
    assert snapshot.bgr == bytes([2]) * 6
    with pytest.raises(FrozenInstanceError):
        setattr(snapshot, attribute, 99)
    assert store.latest() is snapshot
    assert snapshot.captured_at.tzinfo == UTC
    assert snapshot.monotonic_ns == 1


def test_slow_reader_receives_only_latest_frame_and_old_frames_cannot_replace_it() -> None:
    store = FrameStore()
    store.begin(1)
    for sequence in range(1, 100):
        assert store.publish(frame(sequence=sequence))
    latest = store.latest()
    assert latest is not None
    assert latest.sequence == 99
    assert not store.publish(frame(sequence=98))
    assert store.latest() is latest


def test_stopped_and_old_connections_cannot_publish_fresh_images() -> None:
    store = FrameStore()
    store.begin(1)
    store.publish(frame())
    store.invalidate()
    assert store.latest() is None
    assert not store.publish(frame(sequence=2))
    store.begin(2)
    assert not store.publish(frame(sequence=3))
    fresh = frame(generation=2)
    assert store.publish(fresh)
    assert store.latest() is fresh


def test_frame_rejects_malformed_pixels_and_naive_time() -> None:
    with pytest.raises(CaptureError):
        Frame(b"short", 2, 1, 1, 1, datetime.now(UTC), 1)
    with pytest.raises(CaptureError):
        Frame(bytes(6), 2, 1, 1, 1, datetime(2026, 1, 1), 1)  # noqa: DTZ001


def test_frame_rejects_negative_acquisition_clock() -> None:
    with pytest.raises(CaptureError):
        Frame(bytes(6), 2, 1, 1, 1, datetime.now(UTC), -1)
