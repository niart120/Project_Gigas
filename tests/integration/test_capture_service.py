import time
from collections.abc import Callable, Iterator
from pathlib import Path
from threading import Event, Thread
from typing import TYPE_CHECKING

import pytest

from gigas.game_io._capture_process import SharedSlot
from gigas.game_io.capture import CaptureReader, CaptureService, CaptureSnapshot
from gigas.game_io.models import CaptureOptions
from tests.integration.peers.capture_peer import PeerBackend

if TYPE_CHECKING:
    from gigas.game_io.models import Frame


def wait_for(
    service: CaptureService | CaptureReader,
    predicate: Callable[[CaptureSnapshot], bool],
    timeout: float = 8,
) -> CaptureSnapshot:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = service.snapshot()
        if predicate(snapshot):
            return snapshot
        time.sleep(0.01)
    pytest.fail(f"capture state deadline: {service.snapshot()}")


@pytest.fixture
def capture(tmp_path: Path) -> Iterator[tuple[CaptureService, Path]]:
    marker = tmp_path / "release.txt"
    service = CaptureService(backend_factory=PeerBackend(str(marker)))
    service.start()
    yield service, marker
    service.shutdown()
    service.join(6)
    assert not service.is_alive()
    assert service.snapshot().cleanup != "unconfirmed"


@pytest.fixture
def initially_skipped_frames(
    capture: tuple[CaptureService, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[CaptureService, Path, Event]]:
    service, marker = capture
    resume_reader = Event()
    read = SharedSlot.read

    def delayed_read(slot: SharedSlot, generation: int, after_sequence: int) -> "Frame | None":
        if after_sequence and not resume_reader.wait(timeout=5):
            return None
        frame = read(slot, generation, after_sequence)
        return frame if frame is not None and frame.sequence >= 3 else None

    monkeypatch.setattr(SharedSlot, "read", delayed_read)
    try:
        yield service, marker, resume_reader
    finally:
        resume_reader.set()


def test_spawn_enumeration_returns_identities_without_starting_video(
    capture: tuple[CaptureService, Path],
) -> None:
    service, marker = capture
    assert service.refresh_devices()
    snapshot = wait_for(
        service, lambda state: state.devices_revision == 1 and state.phase == "idle"
    )
    assert [device.path for device in snapshot.devices] == ["path-a", "path-b"]
    assert snapshot.frame is None
    assert not marker.exists()


def test_spawn_capture_exposes_latest_image_and_reconnects_after_release(
    initially_skipped_frames: tuple[CaptureService, Path, Event],
) -> None:
    service, marker, resume_reader = initially_skipped_frames
    options = CaptureOptions("path-b", 16, 16, 60)
    assert service.connect(options)
    assert not service.connect(options)
    assert not service.refresh_devices()
    snapshot = wait_for(
        service, lambda state: state.frame is not None and state.frame.sequence >= 3
    )
    frame = snapshot.frame
    assert frame is not None
    assert snapshot.phase == "streaming"
    assert frame.bgr == bytes([frame.sequence % 256]) * (16 * 16 * 3)
    assert snapshot.reported_fps == 59.94
    # The sequence counts device acquisitions, not samples already read by the host.
    assert snapshot.measured_fps is None
    resume_reader.set()
    snapshot = wait_for(service, lambda state: state.measured_fps is not None)
    assert snapshot.frame is not None
    assert snapshot.frame.sequence > frame.sequence
    assert snapshot.measured_fps is not None
    assert snapshot.measured_fps > 0
    service.disconnect()
    assert service.snapshot().frame is None
    stopped = wait_for(service, lambda state: state.phase == "idle")
    assert stopped.cleanup == "graceful"
    assert marker.read_text(encoding="utf-8") == "released\n"
    assert service.connect(options)
    again = wait_for(service, lambda state: state.frame is not None)
    assert again.frame is not None
    assert again.frame.generation > frame.generation
    assert frame.bgr == bytes([frame.sequence % 256]) * (16 * 16 * 3)
    service.shutdown()
    service.join(6)
    assert not service.is_alive()
    assert service.snapshot().phase == "closed"
    assert marker.read_text(encoding="utf-8") == "released\nreleased\n"


def test_shutdown_before_start_rejects_new_requests() -> None:
    service = CaptureService(backend_factory=PeerBackend())
    service.shutdown()
    assert not service.connect(CaptureOptions("path-b", 16, 16))
    assert not service.refresh_devices()
    service.start()
    service.join(2)
    assert not service.is_alive()
    assert service.snapshot().phase == "closed"


def test_reader_has_no_ownership_commands_and_observes_state_without_starting() -> None:
    service = CaptureService(backend_factory=PeerBackend())
    reader = service.reader()
    assert isinstance(reader, CaptureReader)
    assert reader.snapshot() == CaptureSnapshot()
    assert not service.is_alive()
    assert all(not hasattr(reader, name) for name in ("connect", "disconnect", "shutdown", "start"))
    service.shutdown()
    assert reader.snapshot().phase == "stopping"


def test_slow_reader_holds_image_without_stalling_shared_capture_or_preview(
    capture: tuple[CaptureService, Path],
) -> None:
    service, marker = capture
    preview = service.reader()
    observation = service.reader()
    assert service.connect(CaptureOptions("path-b", 16, 16, 60))
    wait_for(preview, lambda state: state.frame is not None)
    holding = Event()
    resume = Event()
    observed: list[CaptureSnapshot] = []

    def retain_image() -> None:
        observed.append(observation.snapshot())
        holding.set()
        resume.wait()
        observed.append(observation.snapshot())

    reader_thread = Thread(target=retain_image, daemon=True)
    reader_thread.start()
    try:
        assert holding.wait(timeout=2)
        retained = observed[0].frame
        assert retained is not None
        pixels = bytes([retained.sequence % 256]) * (16 * 16 * 3)
        progressed = wait_for(
            preview,
            lambda state: state.frame is not None and state.frame.sequence >= retained.sequence + 5,
        )
        assert reader_thread.is_alive()
        assert progressed.frame is not None
        assert progressed.frame.generation == retained.generation
        assert progressed.frame.bgr == bytes([progressed.frame.sequence % 256]) * (16 * 16 * 3)
        assert retained.bgr == pixels
        assert marker.with_suffix(".txt.opened").read_text(encoding="utf-8") == "opened\n"
        service.disconnect()
        assert preview.snapshot().frame is None
        assert observation.snapshot().frame is None
        assert wait_for(service, lambda state: state.phase == "idle").cleanup == "graceful"
        assert marker.read_text(encoding="utf-8") == "released\n"
        assert retained.bgr == pixels
    finally:
        resume.set()
        reader_thread.join(2)
        assert not reader_thread.is_alive()
    assert len(observed) == 2
    assert observed[1].frame is None
