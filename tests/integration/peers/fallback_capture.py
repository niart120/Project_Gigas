"""Controllable API candidates for real spawned fallback tests."""

import os
import time
from dataclasses import dataclass
from multiprocessing.util import Finalize
from pathlib import Path
from typing import TYPE_CHECKING

import cv2

from gigas.game_io.capture._process import Channel, worker_main
from gigas.game_io.capture.backend import IndexedDevice
from gigas.game_io.capture.config import CaptureOptions
from gigas.game_io.capture.models import CaptureError, DeviceInfo
from tests.integration.peers.capture_peer import PeerCamera

if TYPE_CHECKING:
    from multiprocessing.synchronize import Lock


def _record(marker: str, event: str) -> None:
    with Path(marker).open("a", encoding="utf-8") as stream:
        stream.write(f"{event}\n")


def _exit(marker: str, crash: bool) -> None:
    _record(marker, f"exit:{os.getpid()}")
    if crash:
        os._exit(23)


class CandidateCamera(PeerCamera):
    """Fail the primary candidate before frames, unless testing a streaming failure."""

    def __init__(self, marker: str, api: int, scenario: str) -> None:
        """Bind one explicit candidate and its release scenario."""
        super().__init__("", "failed-read" if scenario == "after-frame" else "normal")
        self.marker_path = marker
        self.api = api
        self.case = scenario

    def isOpened(self) -> bool:  # noqa: N802 -- OpenCV interface
        """Open only the fallback, except for the post-frame failure scenario."""
        return self.case == "after-frame" or (self.api == cv2.CAP_MSMF and self.case != "both-fail")

    def release(self) -> None:
        """Expose failure, blocking, and completion boundaries without a camera."""
        if self.api == cv2.CAP_DSHOW:
            Path(self.marker_path + ".releasing").touch()
            if self.case == "wait-release":
                while not Path(self.marker_path + ".allow-release").exists():
                    time.sleep(0.01)
            elif self.case == "hang-release":
                time.sleep(3600)
            elif self.case == "release-fails":
                message = "injected release failure"
                raise CaptureError(message)
        _record(self.marker_path, f"release:{self.api}:{os.getpid()}")


@dataclass(frozen=True)
class FallbackBackend:
    """Enumerate two APIs with different, changing indices for one identity."""

    marker: str
    scenario: str = "normal"

    def __call__(self) -> "FallbackBackend":
        """Record process exit separately from native release."""
        Finalize(None, _exit, args=(self.marker, self.scenario == "crash-exit"), exitpriority=0)
        return self

    def enumerate(self) -> tuple[IndexedDevice, ...]:
        """Change the secondary index after the first process has opened its device."""
        second = Path(self.marker + ".primary-opened").exists()
        devices = (
            IndexedDevice(DeviceInfo("Same model", "other"), 8, cv2.CAP_DSHOW),
            IndexedDevice(DeviceInfo("Same model", "selected"), 9, cv2.CAP_DSHOW),
            IndexedDevice(DeviceInfo("Same model", "selected"), 7 if second else 3, cv2.CAP_MSMF),
        )
        return devices[:2] if second and self.scenario == "missing-secondary" else devices

    def open(self, device: IndexedDevice) -> CandidateCamera:
        """Reject cached indices and make each actual open observable."""
        if device.backend == cv2.CAP_DSHOW:
            assert device.index == 9
            Path(self.marker + ".primary-opened").touch()
            if self.scenario.startswith("budget"):
                while not Path(self.marker + ".allow-primary").exists():
                    time.sleep(0.01)
        else:
            assert device.backend == cv2.CAP_MSMF
            assert device.index == 7
            Path(self.marker + ".secondary-opened").touch()
            if self.scenario == "budget-secondary-hang":
                time.sleep(3600)
        _record(self.marker, f"open:{device.backend}:{os.getpid()}")
        return CandidateCamera(self.marker, device.backend, self.scenario)


def wait_after_release_worker(
    options: CaptureOptions | None,
    generation: int,
    memory: str | None,
    lock: "Lock | None",
    stop: Channel,
    status: Channel,
    factory: FallbackBackend,
    api: int | None = None,
) -> None:
    """Hold the first process alive after its worker has already released everything."""
    if api is None:
        worker_main(options, generation, memory, lock, stop, status, factory)
    else:
        worker_main(options, generation, memory, lock, stop, status, factory, api)
    if api is None:
        Path(factory.marker + ".before-exit").touch()
        while not Path(factory.marker + ".allow-exit").exists():
            time.sleep(0.01)
