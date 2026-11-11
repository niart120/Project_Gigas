import multiprocessing
import time
from multiprocessing.process import BaseProcess
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from gigas.game_io.capture import CaptureService, CaptureTimeouts
from gigas.game_io.capture import _process as process_module
from gigas.game_io.capture import service as capture_module
from gigas.game_io.capture._process import SharedSlot
from gigas.game_io.capture.models import CaptureOptions
from tests.integration.peers.capture_peer import (
    PeerBackend,
    hanging_after_list_worker,
    oversized_control_worker,
)
from tests.integration.test_capture_service import wait_for

if TYPE_CHECKING:
    from multiprocessing.context import BaseContext

    from gigas.game_io.capture.service import _Session


def test_worker_startup_deadline_invalidates_request_and_reaps_process() -> None:
    service = CaptureService(
        backend_factory=PeerBackend(scenario="slow-enumerate"),
        timeouts=CaptureTimeouts(startup=0.15, frame=0.2, stop=0.1, terminate=0.3),
    )
    service.start()
    try:
        assert service.refresh_devices()
        state = wait_for(service, lambda snapshot: snapshot.phase == "error", timeout=1)
        assert state.devices_revision == 0
        assert state.frame is None
        assert state.cleanup == "forced"
        assert state.error
    finally:
        service.shutdown()
        service.join(3)
        assert not service.is_alive()


def test_device_list_during_capture_does_not_disable_first_frame_deadline(tmp_path: Path) -> None:
    marker = tmp_path / "release.txt"
    entered = marker.with_suffix(".txt.entered.open")
    service = CaptureService(
        backend_factory=PeerBackend(str(marker), "hang-open"),
        timeouts=CaptureTimeouts(startup=5, frame=0.2, stop=0.1, terminate=1),
    )
    service.start()
    try:
        assert service.connect(CaptureOptions("path-b", 16, 16))
        listed = wait_for(service, lambda state: state.devices_revision == 1 and entered.exists())
        assert listed.devices
        assert listed.frame is None
        failed = wait_for(service, lambda state: state.phase == "error", timeout=7)
        assert failed.error == "映像取得の開始期限切れ"
        assert failed.cleanup == "forced"
        assert failed.frame is None
        assert not marker.exists()
    finally:
        service.shutdown()
        service.join(3)
        assert not service.is_alive()
    assert not any(
        child.name.startswith("gigas-capture-") for child in multiprocessing.active_children()
    )


@pytest.mark.parametrize("scenario", ["hang-enumerate", "hang-open", "hang-read", "hang-release"])
def test_blocked_native_boundary_is_forced_closed_after_explicit_stop(
    tmp_path: Path,
    scenario: str,
) -> None:
    marker = tmp_path / "release.txt"
    entered = Path(str(marker) + ".entered." + scenario.removeprefix("hang-"))
    service = CaptureService(
        backend_factory=PeerBackend(str(marker), scenario),
        timeouts=CaptureTimeouts(startup=5, frame=5, stop=0.15, terminate=1),
    )
    service.start()
    try:
        if scenario == "hang-enumerate":
            assert service.refresh_devices()
        else:
            assert service.connect(CaptureOptions("path-b", 16, 16))
        if scenario == "hang-release":
            # Exercise release after read has already published its own marker.
            wait_for(service, lambda state: state.frame is not None and state.frame.sequence >= 4)
            service.disconnect()
        wait_for(service, lambda _state: entered.exists())
        before = time.monotonic()
        service.disconnect()
        assert service.snapshot().frame is None
        state = wait_for(service, lambda state: state.phase == "idle", timeout=3)
        assert state.cleanup == "forced"
        assert time.monotonic() - before < 3
        assert not marker.exists()
    finally:
        service.shutdown()
        service.join(3)
        assert not service.is_alive()
    assert not any(
        child.name.startswith("gigas-capture-") for child in multiprocessing.active_children()
    )


@pytest.mark.parametrize(
    "scenario", ["hang-read", "crash-read", "failed-read", "lock-death", "changed-size"]
)
def test_failed_acquisition_invalidates_image_and_fresh_generation_can_connect(
    tmp_path: Path,
    scenario: str,
) -> None:
    marker = tmp_path / "release.txt"
    service = CaptureService(
        backend_factory=PeerBackend(str(marker), scenario),
        timeouts=CaptureTimeouts(startup=5, frame=0.15, stop=0.1, terminate=1),
    )
    service.start()
    options = CaptureOptions("path-b", 16, 16)
    try:
        assert service.connect(options)
        first = wait_for(service, lambda state: state.frame is not None).frame
        assert first is not None
        failed = wait_for(service, lambda state: state.phase == "error", timeout=3)
        assert failed.frame is None
        assert failed.error
        assert failed.cleanup == (
            "forced"
            if scenario == "hang-read"
            else "graceful"
            if scenario in {"failed-read", "changed-size"}
            else "none"
        )
        service._factory = PeerBackend(str(marker))
        assert service.connect(options)
        second = wait_for(service, lambda state: state.frame is not None).frame
        assert second is not None
        assert second.generation > first.generation
    finally:
        service.shutdown()
        service.join(3)
        assert not service.is_alive()


def test_process_start_failure_releases_shared_memory_and_accepts_a_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names: list[str] = []
    original_create = SharedSlot.create

    def track_memory(context: "BaseContext", options: CaptureOptions) -> SharedSlot:
        slot = original_create(context, options)
        names.append(slot.memory.name)
        return slot

    def fail_start(_process: BaseProcess) -> None:
        message = "injected spawn failure"
        raise OSError(message)

    service = CaptureService(backend_factory=PeerBackend())
    service.start()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(SharedSlot, "create", track_memory)
            patch.setattr(BaseProcess, "start", fail_start)
            assert service.connect(CaptureOptions("path-b", 16, 16))
            state = wait_for(service, lambda state: state.phase == "error")
            assert state.frame is None
            assert state.cleanup == "none"
        assert names
        with pytest.raises(FileNotFoundError):
            SharedMemory(name=names[0])
        assert service.connect(CaptureOptions("path-b", 16, 16))
        wait_for(service, lambda state: state.frame is not None)
    finally:
        service.shutdown()
        service.join(3)
        assert not service.is_alive()


@pytest.mark.parametrize("boundary", ["frame", "control"])
def test_monitor_exception_reaps_worker_and_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    def fail_read(_slot: SharedSlot, _generation: int, _sequence: int) -> None:
        message = "injected snapshot failure"
        raise RuntimeError(message)

    def fail_message(_channel: object) -> None:
        message = "injected control failure"
        raise RuntimeError(message)

    service = CaptureService(backend_factory=PeerBackend())
    service.start()
    try:
        with monkeypatch.context() as patch:
            if boundary == "frame":
                patch.setattr(SharedSlot, "read", fail_read)
            else:
                patch.setattr(capture_module, "receive_message", fail_message)
            assert service.connect(CaptureOptions("path-b", 16, 16))
            state = wait_for(service, lambda state: state.phase == "error")
            assert state.frame is None
            assert state.error
    finally:
        service.shutdown()
        service.join(3)
        assert not service.is_alive()


def test_lock_creation_failure_closes_partially_allocated_shared_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names: list[str] = []
    original_memory = process_module.SharedMemory
    context = multiprocessing.get_context("spawn")

    def allocate(*, create: bool, size: int) -> SharedMemory:
        memory = original_memory(create=create, size=size)
        names.append(memory.name)
        return memory

    def fail_lock() -> None:
        message = "injected semaphore failure"
        raise OSError(message)

    service = CaptureService(backend_factory=PeerBackend())
    service.start()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(process_module, "SharedMemory", allocate)
            patch.setattr(context, "Lock", fail_lock)
            assert service.connect(CaptureOptions("path-b", 16, 16))
            state = wait_for(service, lambda state: state.phase == "error")
            assert state.cleanup == "none"
        assert names
        with pytest.raises(FileNotFoundError):
            SharedMemory(name=names[0])
        assert service.refresh_devices()
        wait_for(service, lambda state: state.devices_revision == 1 and state.phase == "idle")
    finally:
        service.shutdown()
        service.join(3)
        assert not service.is_alive()


def test_join_failure_is_reported_without_losing_resource_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_join(_process: BaseProcess, _timeout: float | None = None) -> None:
        message = "injected join failure"
        raise OSError(message)

    service = CaptureService(backend_factory=PeerBackend())
    service.start()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(BaseProcess, "join", fail_join)
            assert service.refresh_devices()
            state = wait_for(service, lambda state: state.phase == "error")
            assert state.cleanup == "unconfirmed"
            assert service._retained
            assert not service.refresh_devices()
    finally:
        service.shutdown()
        service.join(3)
        assert not service.is_alive()


def test_oversized_control_message_does_not_block_worker_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capture_module, "worker_main", oversized_control_worker)
    service = CaptureService(backend_factory=PeerBackend())
    service.start()
    try:
        assert service.refresh_devices()
        state = wait_for(service, lambda state: state.phase == "error", timeout=3)
        assert state.devices_revision == 0
        assert state.frame is None
    finally:
        service.shutdown()
        service.join(3)
        assert not service.is_alive()


def test_successful_list_still_has_a_process_exit_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture_module, "worker_main", hanging_after_list_worker)
    service = CaptureService(timeouts=CaptureTimeouts(startup=3, frame=1, stop=0.1, terminate=1))
    service.start()
    try:
        assert service.refresh_devices()
        state = wait_for(service, lambda state: state.phase == "idle", timeout=3)
        assert state.devices_revision == 1
        assert state.cleanup == "forced"
    finally:
        service.shutdown()
        service.join(3)
        assert not service.is_alive()


def test_unconfirmed_host_cleanup_blocks_reconnection_and_retains_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_close = SharedSlot.close
    retained: list[SharedSlot] = []

    def fail_owner_close(slot: SharedSlot, *, owner: bool) -> None:
        if owner:
            retained.append(slot)
            message = "injected mapping close failure"
            raise OSError(message)
        original_close(slot, owner=owner)

    service = CaptureService(backend_factory=PeerBackend())
    service.start()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(SharedSlot, "close", fail_owner_close)
            assert service.connect(CaptureOptions("path-b", 16, 16))
            wait_for(service, lambda state: state.frame is not None)
            service.disconnect()
            state = wait_for(service, lambda state: state.phase == "error")
            assert state.cleanup == "unconfirmed"
            assert not service.connect(CaptureOptions("path-b", 16, 16))
            assert not service.refresh_devices()
        assert retained
        assert service._retained  # Resource ownership must survive the failed closer.
    finally:
        service.shutdown()
        service.join(3)
        for slot in retained:
            original_close(slot, owner=True)
        assert not service.is_alive()


def test_stop_pipe_close_failure_falls_through_to_forced_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = CaptureService(
        backend_factory=PeerBackend(),
        timeouts=CaptureTimeouts(startup=3, frame=1, stop=0.1, terminate=1),
    )
    spawn = service._spawn

    def wrap_spawn(options: CaptureOptions | None, generation: int) -> "_Session":
        session = spawn(options, generation)
        close = session.stop.close
        attempted = False

        def fail_once() -> None:
            nonlocal attempted
            if not attempted:
                attempted = True
                message = "injected stop pipe close failure"
                raise OSError(message)
            close()

        monkeypatch.setattr(session.stop, "close", fail_once)
        return session

    monkeypatch.setattr(service, "_spawn", wrap_spawn)
    service.start()
    try:
        assert service.connect(CaptureOptions("path-b", 16, 16))
        wait_for(service, lambda state: state.frame is not None)
        service.disconnect()
        state = wait_for(service, lambda state: state.phase == "error", timeout=3)
        assert state.cleanup == "forced"
        assert state.frame is None
        assert state.error
    finally:
        service.shutdown()
        service.join(3)
        assert not service.is_alive()
