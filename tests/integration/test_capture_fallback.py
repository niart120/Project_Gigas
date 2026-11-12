import multiprocessing
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import cv2
import pytest

from gigas.game_io.capture import CaptureService, CaptureTimeouts
from gigas.game_io.capture import service as module
from gigas.game_io.capture._process import SharedSlot
from gigas.game_io.capture.config import CaptureOptions
from tests.integration.peers.fallback_capture import FallbackBackend, wait_after_release_worker
from tests.integration.test_capture_service import wait_for

if TYPE_CHECKING:
    from collections.abc import Iterator

    from gigas.game_io.capture.models import Frame


@pytest.fixture
def capture(tmp_path: Path) -> "Iterator[tuple[CaptureService, str]]":
    marker = str(tmp_path / "events.txt")
    service = CaptureService(backend_factory=FallbackBackend(marker))
    service.start()
    yield service, marker
    Path(marker + ".allow-release").touch()
    Path(marker + ".allow-exit").touch()
    service.shutdown()
    service.join(6)
    assert not service.is_alive()
    assert not any(
        child.name.startswith("gigas-capture-") for child in multiprocessing.active_children()
    )


def test_fallback_reenumerates_same_identity_after_normal_worker_exit(
    capture: tuple[CaptureService, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, marker = capture
    monkeypatch.setattr(module, "worker_main", wait_after_release_worker)
    assert service.connect(CaptureOptions("selected", 16, 16))
    waiting = wait_for(service, lambda _state: Path(marker + ".before-exit").exists())
    assert waiting.frame is None
    assert waiting.error == ""
    assert not Path(marker + ".secondary-opened").exists()
    Path(marker + ".allow-exit").touch()
    result = wait_for(service, lambda state: state.frame is not None or state.phase == "error")
    assert result.frame is not None, result.error
    assert result.generation == 1
    assert result.backend == cv2.CAP_MSMF
    assert len(result.diagnostics) == 1
    assert result.error == ""
    events = Path(marker).read_text(encoding="utf-8").splitlines()
    first_pid = events[0].rsplit(":", 1)[1]
    assert events[:3] == [f"open:700:{first_pid}", f"release:700:{first_pid}", f"exit:{first_pid}"]
    assert events[3].startswith("open:1400:")
    assert not events[3].endswith(":" + first_pid)


@pytest.mark.parametrize("closing", [False, True])
def test_stop_while_releasing_cancels_all_remaining_candidates(
    tmp_path: Path, closing: bool
) -> None:
    marker = str(tmp_path / "events.txt")
    service = CaptureService(backend_factory=FallbackBackend(marker, "wait-release"))
    service.start()
    try:
        assert service.connect(CaptureOptions("selected", 16, 16))
        wait_for(service, lambda _state: Path(marker + ".releasing").exists())
        (service.shutdown if closing else service.disconnect)()
        Path(marker + ".allow-release").touch()
        stopped = wait_for(service, lambda state: state.phase in {"idle", "error", "closed"})
        assert stopped.frame is None
        assert not Path(marker + ".secondary-opened").exists()
    finally:
        Path(marker + ".allow-release").touch()
        service.shutdown()
        service.join(6)
        assert not service.is_alive()


@pytest.mark.parametrize(
    ("scenario", "cleanup"),
    [
        ("both-fail", "graceful"),
        ("release-fails", "none"),
        ("crash-exit", "graceful"),
        ("hang-release", "forced"),
        ("after-frame", "graceful"),
    ],
)
def test_fallback_requires_clean_release_normal_exit_and_no_started_stream(
    tmp_path: Path, scenario: str, cleanup: str
) -> None:
    marker = str(tmp_path / "events.txt")
    service = CaptureService(
        backend_factory=FallbackBackend(marker, scenario),
        timeouts=CaptureTimeouts(startup=2, frame=1, stop=0.1, terminate=1),
    )
    service.start()
    try:
        assert service.connect(CaptureOptions("selected", 16, 16))
        result = wait_for(service, lambda state: state.phase == "error")
        assert result.error
        assert result.frame is None
        assert result.cleanup == cleanup
        assert len(result.diagnostics) == (2 if scenario == "both-fail" else 1)
        assert Path(marker + ".secondary-opened").exists() is (scenario == "both-fail")
    finally:
        service.shutdown()
        service.join(6)
        assert not service.is_alive()


def test_startup_deadline_is_shared_by_all_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = str(tmp_path / "events.txt")
    now = [0.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    service = CaptureService(backend_factory=FallbackBackend(marker, "wait-release"))
    service.start()
    try:
        assert service.connect(CaptureOptions("selected", 16, 16))
        wait_for(
            service,
            lambda state: state.backend == cv2.CAP_DSHOW and Path(marker + ".releasing").exists(),
        )
        now[0] = 11.0
        wait_for(service, lambda state: state.phase == "stopping")
        Path(marker + ".allow-release").touch()
        failed = wait_for(service, lambda state: state.phase == "error")
        assert failed.error
        assert failed.frame is None
        assert failed.cleanup == "graceful"
        assert not Path(marker + ".secondary-opened").exists()
    finally:
        Path(marker + ".allow-release").touch()
        service.shutdown()
        now[0] += 10
        service.join(3)
        if service.is_alive():
            now[0] += 10
            service.join(3)
        assert not service.is_alive()


def test_unconfirmed_host_cleanup_never_starts_next_candidate(
    capture: tuple[CaptureService, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, marker = capture
    close = SharedSlot.close
    retained: list[SharedSlot] = []

    def fail_owner_close(slot: SharedSlot, *, owner: bool) -> None:
        if owner:
            retained.append(slot)
            message = "injected close failure"
            raise OSError(message)
        close(slot, owner=owner)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(SharedSlot, "close", fail_owner_close)
            assert service.connect(CaptureOptions("selected", 16, 16))
            failed = wait_for(service, lambda state: state.phase == "error")
            assert failed.cleanup == "unconfirmed"
            assert failed.frame is None
            assert not Path(marker + ".secondary-opened").exists()
            assert not service.connect(CaptureOptions("selected", 16, 16))
    finally:
        for slot in retained:
            close(slot, owner=True)


def test_second_candidate_uses_original_deadline_instead_of_starting_a_new_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = str(tmp_path / "events.txt")
    now = [0.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    service = CaptureService(backend_factory=FallbackBackend(marker, "budget-secondary-hang"))
    service.start()
    try:
        assert service.connect(CaptureOptions("selected", 16, 16))
        wait_for(service, lambda _state: Path(marker + ".primary-opened").exists())
        now[0] = 9.0
        Path(marker + ".allow-primary").touch()
        wait_for(service, lambda _state: Path(marker + ".secondary-opened").exists())
        now[0] = 11.0
        wait_for(service, lambda state: state.phase == "stopping", timeout=2)
        now[0] = 14.0
        result = wait_for(service, lambda state: state.phase == "error", timeout=2)
        assert result.cleanup == "forced"
        assert result.frame is None
        assert len(result.diagnostics) == 2
    finally:
        Path(marker + ".allow-primary").touch()
        service.shutdown()
        # Ensure cleanup can progress if a failed assertion leaves a blocked peer.
        now[0] += 10
        service.join(3)
        if service.is_alive():
            now[0] += 10
            service.join(3)
        assert not service.is_alive()


def test_first_fallback_image_arriving_after_shared_deadline_is_never_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = str(tmp_path / "events.txt")
    now = [0.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    service = CaptureService(backend_factory=FallbackBackend(marker, "budget"))
    read = SharedSlot.read

    def late_first_image(slot: SharedSlot, generation: int, after_sequence: int) -> "Frame | None":
        frame = read(slot, generation, after_sequence)
        if frame is not None and after_sequence == 0 and service.snapshot().backend == cv2.CAP_MSMF:
            now[0] = 11.0
        return frame

    monkeypatch.setattr(SharedSlot, "read", late_first_image)
    service.start()
    try:
        assert service.connect(CaptureOptions("selected", 16, 16))
        wait_for(service, lambda _state: Path(marker + ".primary-opened").exists())
        now[0] = 9.0
        Path(marker + ".allow-primary").touch()
        result = wait_for(service, lambda state: state.frame is not None or state.phase == "error")
        assert result.frame is None
        assert result.phase == "error"
        assert result.cleanup == "graceful"
    finally:
        Path(marker + ".allow-primary").touch()
        service.shutdown()
        service.join(6)
        assert not service.is_alive()


def test_missing_fallback_identity_never_reopens_the_previous_api(tmp_path: Path) -> None:
    marker = str(tmp_path / "events.txt")
    service = CaptureService(backend_factory=FallbackBackend(marker, "missing-secondary"))
    service.start()
    try:
        assert service.connect(CaptureOptions("selected", 16, 16))
        result = wait_for(service, lambda state: state.phase == "error")
        assert result.frame is None
        assert len(result.diagnostics) == 2
        events = Path(marker).read_text(encoding="utf-8").splitlines()
        assert len([event for event in events if event.startswith("open:")]) == 1
        assert not Path(marker + ".secondary-opened").exists()
    finally:
        service.shutdown()
        service.join(6)
        assert not service.is_alive()
