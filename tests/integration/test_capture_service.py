import asyncio
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest

from gigas.game_io.capture import CaptureReader, CaptureService, CaptureSnapshot
from gigas.game_io.capture._process import SharedSlot
from gigas.game_io.capture.models import CaptureOptions, DeviceInfo
from gigas.services.observation import ObservationService
from gigas.storage import project
from gigas.storage.project import ProjectWriter
from tests.integration.peers.capture_peer import PeerBackend

if TYPE_CHECKING:
    from gigas.game_io.capture.models import Frame


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
    # The sequence counts device acquisitions, not samples already read by the host.
    resume_reader.set()
    snapshot = wait_for(
        service, lambda state: state.frame is not None and state.frame.sequence > frame.sequence
    )
    assert snapshot.frame is not None
    assert snapshot.frame.sequence > frame.sequence
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


def test_direct_connection_reports_device_names_without_an_additional_enumeration(
    capture: tuple[CaptureService, Path],
) -> None:
    service, marker = capture
    assert service.snapshot().devices_revision == 0
    assert service.connect(CaptureOptions("path-b", 16, 16, 60))
    initial = wait_for(service, lambda state: state.frame is not None)

    assert initial.devices == (DeviceInfo("Capture", "path-a"), DeviceInfo("Capture", "path-b"))
    assert initial.devices_revision == 1
    first = initial.frame
    assert first is not None
    progressed = wait_for(
        service,
        lambda state: state.frame is not None and state.frame.sequence >= first.sequence + 5,
    )
    assert progressed.phase == "streaming"
    assert progressed.devices_revision == 1
    assert marker.with_suffix(".txt.enumerated").read_text(encoding="utf-8") == "enumerated\n"
    assert marker.with_suffix(".txt.opened").read_text(encoding="utf-8") == "opened\n"


def test_shutdown_before_start_rejects_new_requests() -> None:
    service = CaptureService(backend_factory=PeerBackend())
    service.shutdown()
    assert not service.connect(CaptureOptions("path-b", 16, 16))
    assert not service.refresh_devices()
    service.start()
    service.join(2)
    assert not service.is_alive()
    assert service.snapshot().phase == "closed"


@pytest.mark.parametrize(
    ("requested", "actual"), [(None, (16, 16)), ((1920, 1080), (32, 24)), (None, (3840, 2160))]
)
def test_spawn_shares_actual_size_with_independent_readers(
    requested: tuple[int, int] | None, actual: tuple[int, int]
) -> None:
    service = CaptureService(backend_factory=PeerBackend(actual_size=actual))
    preview, observation = service.reader(), service.reader()
    service.start()
    try:
        width, height = requested or (None, None)
        assert service.connect(CaptureOptions("path-b", width, height))
        first = wait_for(preview, lambda state: state.frame is not None or state.phase == "error")
        retained = first.frame
        assert retained is not None, first.error
        assert (retained.width, retained.height) == actual
        assert retained.bgr == bytes([retained.sequence % 256]) * (actual[0] * actual[1] * 3)
        later = wait_for(
            observation,
            lambda state: state.frame is not None and state.frame.sequence >= retained.sequence + 3,
        ).frame
        assert later is not None
        assert (later.width, later.height) == actual
        assert later.bgr == bytes([later.sequence % 256]) * (actual[0] * actual[1] * 3)
        assert later.monotonic_ns > retained.monotonic_ns
        assert later.captured_at >= retained.captured_at
        service.disconnect()
        assert wait_for(service, lambda state: state.phase == "idle").cleanup == "graceful"
        assert preview.snapshot().frame is None
        assert observation.snapshot().frame is None
        assert retained.bgr == bytes([retained.sequence % 256]) * (actual[0] * actual[1] * 3)
    finally:
        service.shutdown()
        service.join(6)
        assert not service.is_alive()


def test_reader_has_no_ownership_commands_and_observes_state_without_starting() -> None:
    service = CaptureService(backend_factory=PeerBackend())
    reader = service.reader()
    assert isinstance(reader, CaptureReader)
    assert reader.snapshot() == CaptureSnapshot()
    assert not service.is_alive()
    assert all(not hasattr(reader, name) for name in ("connect", "disconnect", "shutdown", "start"))
    service.shutdown()
    assert reader.snapshot().phase == "stopping"


def test_observation_save_does_not_stall_capture_preview_or_stop(
    capture: tuple[CaptureService, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, marker = capture
    preview = service.reader()
    assert service.connect(CaptureOptions("path-b", 16, 16, 60))
    first = wait_for(preview, lambda state: state.frame is not None).frame
    assert first is not None
    holding, resume = Event(), Event()
    publish = project._publish_image

    def delayed_publish(path: Path, data: bytes) -> None:
        holding.set()
        if not resume.wait(timeout=10):
            message = "test release deadline"
            raise OSError(message)
        publish(path, data)

    monkeypatch.setattr(project, "_publish_image", delayed_publish)
    writer = ProjectWriter.create(tmp_path / "observation")

    async def scenario() -> None:
        observer = ObservationService(service.reader(), writer)
        request = asyncio.create_task(observer.observe())
        try:
            assert await asyncio.to_thread(holding.wait, 3)
            progressed = await asyncio.to_thread(
                wait_for,
                preview,
                lambda state: (
                    state.frame is not None and state.frame.sequence >= first.sequence + 5
                ),
            )
            assert progressed.frame is not None
            assert progressed.frame.generation == first.generation
            assert not request.done()
            assert marker.with_suffix(".txt.opened").read_text(encoding="utf-8") == "opened\n"
            service.disconnect()
            assert preview.snapshot().frame is None
            stopped = await asyncio.to_thread(
                wait_for, service, lambda state: state.phase == "idle"
            )
            assert stopped.cleanup == "graceful"
            assert marker.read_text(encoding="utf-8") == "released\n"
        finally:
            resume.set()
            observed = await request
        assert observed.frame.bgr == bytes([observed.frame.sequence % 256]) * (16 * 16 * 3)
        assert (writer.path / observed.image_path).read_bytes() == observed.png_bytes

    try:
        asyncio.run(scenario())
    finally:
        resume.set()
        writer.close()
