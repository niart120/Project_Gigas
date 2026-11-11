"""DirectShow device access confined to a capture worker process."""

import math
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic_ns
from typing import TYPE_CHECKING, Protocol

import cv2
import numpy as np
from cv2_enumerate_cameras import enumerate_cameras

from gigas.game_io.models import CaptureError, CaptureOptions, DeviceInfo, Frame, select_device

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class IndexedDevice:
    """A transient DirectShow index paired with its current device identity."""

    info: DeviceInfo
    index: int


class Camera(Protocol):
    """The synchronous OpenCV operations owned by one worker process."""

    def isOpened(self) -> bool:  # noqa: N802 -- OpenCV interface
        """Return whether the device was opened."""
        ...

    def set(self, prop_id: int, value: float, /) -> bool:
        """Request a device property; actual image dimensions are checked separately."""
        ...

    def get(self, prop_id: int, /) -> float:
        """Read a device-reported property, which need not match actual throughput."""
        ...

    def read(self) -> tuple[bool, object]:
        """Return the next image for validation at the external-library boundary."""
        ...

    def release(self) -> None:
        """Release the device, potentially waiting in its native driver."""
        ...


class CaptureBackend(Protocol):
    """A synchronous device boundary, instantiated only inside the worker."""

    def enumerate(self) -> tuple[IndexedDevice, ...]:
        """Return current device identities and transient indices."""
        ...

    def open(self, index: int) -> Camera:
        """Open one index from the current enumeration."""
        ...


class OpenCVBackend:
    """Acquire Windows DirectShow video without importing GUI libraries."""

    def enumerate(self) -> tuple[IndexedDevice, ...]:
        """List selectable devices, excluding interfaces without a device path.

        Raises:
            CaptureError: The platform is not Windows.
        """
        if sys.platform != "win32":
            message = "映像取得はWindows DirectShowのみ対応"
            raise CaptureError(message)
        return tuple(
            IndexedDevice(DeviceInfo(str(device.name), str(device.path)), int(device.index))
            for device in enumerate_cameras(cv2.CAP_DSHOW)
            if device.path
        )

    def open(self, index: int) -> Camera:
        """Open the explicit DirectShow backend without choosing another device."""
        return cv2.VideoCapture(index, cv2.CAP_DSHOW)


def acquire_frames(
    options: CaptureOptions,
    generation: int,
    backend: CaptureBackend,
    stopped: "Callable[[], bool]",
    publish: "Callable[[Frame], None]",
    opened: "Callable[[float | None], None]",
    released: "Callable[[], None] | None" = None,
) -> None:
    """Resolve a device, acquire validated frames, and always release its handle.

    Args:
        options: Immutable connection settings; FourCC stays at the backend default.
        generation: Host-assigned connection identifier.
        backend: Synchronous device adapter owned by this process.
        stopped: Nonblocking cancellation check, including after a blocking read.
        publish: Receives each valid frame; may skip it if the reader is busy.
        opened: Receives device-reported FPS, or None for an unavailable value.
        released: Optional notification after the native release returned normally.

    Raises:
        CaptureError: Selection, opening, reading, or the actual frame format fails.
    """
    devices = backend.enumerate()
    selected = select_device(tuple(device.info for device in devices), options.device_path)
    index = next(device.index for device in devices if device.info == selected)
    if stopped():
        return
    camera = backend.open(index)
    try:
        if not camera.isOpened():
            message = "映像取得元を開けない"
            raise CaptureError(message)
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, options.width)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, options.height)
        camera.set(cv2.CAP_PROP_FPS, options.fps)
        rate = camera.get(cv2.CAP_PROP_FPS)
        opened(rate if math.isfinite(rate) and rate > 0 else None)
        sequence = 0
        while not stopped():
            success, pixels = camera.read()
            tick = monotonic_ns()
            captured_at = datetime.now(UTC)
            if stopped():
                return
            if not success:
                message = "映像フレームの取得失敗"
                raise CaptureError(message)
            if (
                not isinstance(pixels, np.ndarray)
                or pixels.dtype != np.uint8
                or pixels.shape != (options.height, options.width, 3)
            ):
                message = "取得映像の解像度または形式が要求と不一致"
                raise CaptureError(message)
            sequence += 1
            publish(
                Frame(
                    pixels.tobytes(),
                    options.width,
                    options.height,
                    generation,
                    sequence,
                    captured_at,
                    tick,
                )
            )
    finally:
        camera.release()
        if released is not None:
            released()
