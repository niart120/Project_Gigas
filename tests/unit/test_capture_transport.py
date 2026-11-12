import multiprocessing
import time
from datetime import UTC, datetime
from multiprocessing.shared_memory import SharedMemory
from threading import Event, Thread

import pytest

from gigas.game_io.capture._process import SharedSlot
from gigas.game_io.capture.models import CaptureError, CaptureOptions, Frame


def image(width: int, height: int, sequence: int) -> Frame:
    return Frame(
        bytes([sequence % 256]) * (width * height * 3),
        width,
        height,
        4,
        sequence,
        datetime(2026, 11, 11, 1, 2, 3, 456789, tzinfo=UTC),
        1_000_000 + sequence,
        900_000 + sequence,
    )


@pytest.mark.parametrize("automatic", [True, False])
def test_slot_accepts_maximum_image_and_copies_only_actual_pixels(automatic: bool) -> None:
    options = CaptureOptions("path", None, None) if automatic else CaptureOptions("path", 16, 16)
    slot = SharedSlot.create(multiprocessing.get_context("spawn"), options)
    try:
        assert slot.read(4, 0) is None
        large = image(3840, 2160, 1)
        small = image(32, 24, 2)
        slot.publish(large)
        retained = slot.read(4, 0)
        assert retained == large
        slot.publish(small)
        assert slot.read(4, 1) == small
        assert slot.read(4, 2) is None
        assert retained == large
    finally:
        slot.close(owner=True)


@pytest.mark.parametrize(("width", "height"), [(15, 16), (3841, 16), (16, 2161), (7680, 1080)])
def test_slot_rejects_out_of_bounds_frame_without_replacing_previous_image(
    width: int, height: int
) -> None:
    slot = SharedSlot.create(multiprocessing.get_context("spawn"), CaptureOptions("path", 16, 16))
    try:
        original = image(16, 16, 1)
        slot.publish(original)
        with pytest.raises(CaptureError):
            slot.publish(image(width, height, 2))
        assert slot.read(4, 0) == original
    finally:
        slot.close(owner=True)


def test_shared_dimensions_and_pixels_remain_consistent_during_concurrent_copy() -> None:
    slot = SharedSlot.create(multiprocessing.get_context("spawn"), CaptureOptions("path", 16, 16))
    start = Event()
    stop = Event()
    failures: list[BaseException] = []
    sources = (image(1920, 1080, 1), image(640, 480, 2))

    def publish() -> None:
        try:
            if not start.wait(3):
                return
            while not stop.is_set():
                for frame in sources:
                    slot.publish(frame)
        except BaseException as failure:  # noqa: BLE001 -- surface writer failures on the test thread
            failures.append(failure)

    writer = Thread(target=publish, daemon=True)
    writer.start()
    copied = 0
    try:
        slot.publish(sources[0])
        start.set()
        deadline = time.monotonic() + 3
        while copied < 40 and time.monotonic() < deadline:
            frame = slot.read(4, 0)
            if frame is not None:
                assert frame == sources[frame.sequence - 1]
                copied += 1
    finally:
        stop.set()
        start.set()
        writer.join(3)
        slot.close(owner=True)
    assert not writer.is_alive()
    assert not failures
    assert copied == 40


def test_transport_rejects_undersized_mapping_without_taking_ownership() -> None:
    memory = SharedMemory(create=True, size=64)
    try:
        with pytest.raises(CaptureError):
            SharedSlot(memory, multiprocessing.get_context("spawn").Lock())
        assert memory.buf is not None
    finally:
        memory.close()
        memory.unlink()
