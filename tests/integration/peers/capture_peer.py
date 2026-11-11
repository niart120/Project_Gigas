"""Importable capture adapters for spawn-process integration tests."""

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from gigas.game_io.backend import IndexedDevice
from gigas.game_io.models import DeviceInfo


class PeerCamera:
    """Produce numbered solid frames and record release without a device."""

    def __init__(self, marker: str) -> None:
        """Prepare the test marker and requested dimensions."""
        self.marker = marker
        self.width = 16
        self.height = 16
        self.sequence = 0

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
        time.sleep(0.01)
        self.sequence += 1
        return True, np.full((self.height, self.width, 3), self.sequence % 256, dtype=np.uint8)

    def release(self) -> None:
        """Record graceful release for the host test."""
        if self.marker:
            with Path(self.marker).open("a", encoding="utf-8") as stream:
                stream.write("released\n")


@dataclass(frozen=True)
class PeerBackend:
    """A picklable backend factory and adapter using one stable device path."""

    marker: str = ""

    def __call__(self) -> "PeerBackend":
        """Instantiate the adapter within the spawned interpreter."""
        return self

    def enumerate(self) -> tuple[IndexedDevice, ...]:
        """Provide two same-name identities to exercise path selection."""
        return (
            IndexedDevice(DeviceInfo("Capture", "path-a"), 4),
            IndexedDevice(DeviceInfo("Capture", "path-b"), 7),
        )

    def open(self, index: int) -> PeerCamera:
        """Reject an incorrectly mapped transient index."""
        if index != 7:
            message = "unexpected selected index"
            raise ValueError(message)
        return PeerCamera(self.marker)
