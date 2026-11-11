"""Importable capture adapters for spawn-process integration tests."""

import os
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from gigas.game_io._capture_process import Channel, SharedSlot, WorkerMessage, send_message
from gigas.game_io.backend import IndexedDevice
from gigas.game_io.models import DeviceInfo, Frame

_original_publish = SharedSlot.publish


def _die_with_lock(self: SharedSlot, frame: Frame) -> None:
    if frame.sequence >= 3:
        self.lock.acquire()
        os._exit(17)
    _original_publish(self, frame)


def _entered(marker: str, operation: str) -> None:
    if marker:
        # Each boundary publishes its own flag; no reader observes overwritten content.
        Path(marker + ".entered." + operation).touch()


class PeerCamera:
    """Produce numbered solid frames and record release without a device."""

    def __init__(self, marker: str, scenario: str = "normal") -> None:
        """Prepare the test marker and requested dimensions."""
        self.marker = marker
        self.width = 16
        self.height = 16
        self.sequence = 0
        self.scenario = scenario

    def isOpened(self) -> bool:  # noqa: N802 -- OpenCV interface
        """Expose a successfully opened test device."""
        return True

    def set(self, prop_id: int, value: float) -> bool:
        """Apply dimensions exactly as the real worker requests them."""
        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            self.width = int(value)
        elif prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            self.height = int(value)
        return True

    def get(self, _prop_id: int) -> float:
        """Expose a nominal rate distinguishable from measured throughput."""
        return 59.94

    def read(self) -> tuple[bool, object]:
        """Pace numbered frames so cancellation can race with a pending read."""
        if self.sequence == 3:
            _entered(self.marker, "read")
            if self.scenario == "hang-read":
                time.sleep(3600)
            elif self.scenario == "crash-read":
                os._exit(19)
            elif self.scenario == "failed-read":
                return False, None
        time.sleep(0.01)
        self.sequence += 1
        return True, np.full((self.height, self.width, 3), self.sequence % 256, dtype=np.uint8)

    def release(self) -> None:
        """Record graceful release for the host test."""
        if self.scenario == "hang-release":
            _entered(self.marker, "release")
            time.sleep(3600)
        if self.marker:
            with Path(self.marker).open("a", encoding="utf-8") as stream:
                stream.write("released\n")


@dataclass(frozen=True)
class PeerBackend:
    """A picklable backend factory and adapter using one stable device path."""

    marker: str = ""
    scenario: str = "normal"

    def __call__(self) -> "PeerBackend":
        """Instantiate the adapter within the spawned interpreter."""
        if self.scenario == "lock-death":
            SharedSlot.publish = _die_with_lock
        return self

    def enumerate(self) -> tuple[IndexedDevice, ...]:
        """Provide two same-name identities to exercise path selection."""
        if self.scenario == "slow-enumerate":
            time.sleep(1.5)
        elif self.scenario == "hang-enumerate":
            _entered(self.marker, "enumerate")
            time.sleep(3600)
        return (
            IndexedDevice(DeviceInfo("Capture", "path-a"), 4),
            IndexedDevice(DeviceInfo("Capture", "path-b"), 7),
        )

    def open(self, index: int) -> PeerCamera:
        """Reject an incorrectly mapped transient index."""
        if self.scenario == "hang-open":
            _entered(self.marker, "open")
            time.sleep(3600)
        if index != 7:
            message = "unexpected selected index"
            raise ValueError(message)
        return PeerCamera(self.marker, self.scenario)


def oversized_control_worker(
    _options: object,
    _generation: int,
    _memory: object,
    _lock: object,
    stop: Channel,
    status: Channel,
    _factory: object,
) -> None:
    """Try a control message larger than the monitor permits, then close the peer."""
    with suppress(OSError):
        status.send_bytes(b"x" * 1_000_000)
    stop.close()
    status.close()


def hanging_after_list_worker(
    _options: object,
    _generation: int,
    _memory: object,
    _lock: object,
    _stop: Channel,
    status: Channel,
    _factory: object,
) -> None:
    """Publish enumeration then simulate a native interpreter-exit hang."""
    send_message(status, WorkerMessage("devices"))
    time.sleep(3600)
