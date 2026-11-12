"""Platform-specific device access confined to a capture worker process."""

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic_ns
from typing import TYPE_CHECKING, Protocol

import cv2
import numpy as np
from cv2_enumerate_cameras import enumerate_cameras

from gigas.game_io.capture.config import CaptureOptions
from gigas.game_io.capture.models import CaptureError, DeviceInfo, Frame

if TYPE_CHECKING:
    from collections.abc import Callable

_LINUX_ID_ROOTS = (Path("/dev/v4l/by-id"), Path("/dev/v4l/by-path"))


@dataclass(frozen=True, slots=True)
class IndexedDevice:
    """A stable selection identity paired with a transient API-specific index.

    Attributes:
        info: Display name and opaque selection identity.
        index: Current enumeration index, meaningful only with this backend.
        backend: Explicit OpenCV API identifier; zero is reserved for test adapters.
    """

    info: DeviceInfo
    index: int
    backend: int = 0


def device_catalog(devices: tuple[IndexedDevice, ...]) -> tuple[DeviceInfo, ...]:
    """Return one display entry per nonempty identity, preserving API priority."""
    catalog: dict[str, DeviceInfo] = {}
    for device in devices:
        if device.info.path:
            catalog.setdefault(device.info.path, device.info)
    return tuple(catalog.values())


def select_candidates(
    devices: tuple[IndexedDevice, ...], device_path: str
) -> tuple[IndexedDevice, ...]:
    """Resolve an exact identity to unique candidates in backend priority order.

    Args:
        devices: Current enumeration with backend-specific indices.
        device_path: Nonempty opaque identity, never a display name or numeric index.

    Returns:
        Matching candidates, each belonging to a different backend.

    Raises:
        CaptureError: The identity is absent or ambiguous within one backend.
    """
    matches = tuple(device for device in devices if device_path and device.info.path == device_path)
    if not matches:
        message = "映像取得元が見つからない"
        raise CaptureError(message)
    if len({device.backend for device in matches}) != len(matches):
        message = "映像取得元の識別子が同一方式内で重複"
        raise CaptureError(message)
    return matches


def _linux_identity(path: str) -> str | None:
    try:
        node = Path(path).resolve(strict=True)
        for root in _LINUX_ID_ROOTS:
            for alias in sorted(root.glob("*")):
                try:
                    if alias.resolve(strict=True) == node:
                        return str(alias)
                except OSError:
                    continue
    except OSError:
        return None
    return None


class Camera(Protocol):
    """The synchronous OpenCV operations owned by one worker process."""

    def isOpened(self) -> bool:  # noqa: N802 -- OpenCV interface
        """Return whether the device was opened."""
        ...

    def set(self, prop_id: int, value: float, /) -> bool:
        """Request a device property without guaranteeing that it will be honored."""
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

    def open(self, device: IndexedDevice) -> Camera:
        """Open a current candidate using its own API and index together."""
        ...


class OpenCVBackend:
    """Enumerate usable native APIs without importing GUI libraries or probing indices."""

    @staticmethod
    def _apis() -> tuple[int, ...]:
        preferred = {
            "win32": (cv2.CAP_DSHOW, cv2.CAP_MSMF),
            "linux": (cv2.CAP_V4L2, cv2.CAP_GSTREAMER),
            "darwin": (cv2.CAP_AVFOUNDATION,),
        }.get(sys.platform, ())
        registered = cv2.videoio_registry.getCameraBackends()
        available = tuple(
            api for api in preferred if api in registered and cv2.videoio_registry.hasBackend(api)
        )
        if not available:
            message = "このOSまたはOpenCV構成に対応する映像取得方式がない"
            raise CaptureError(message)
        return available

    def enumerate(self) -> tuple[IndexedDevice, ...]:
        """List identities with explicit APIs, excluding sources without a safe identity.

        Windows preserves device paths, macOS uses uniqueID, and Linux resolves
        current nodes through by-id or by-path aliases. Numbers are never saved.

        Raises:
            CaptureError: No supported native camera API is available.
        """
        devices = []
        for api in self._apis():
            for device in enumerate_cameras(api):
                if not device.path:
                    continue
                path = str(device.path)
                identity = _linux_identity(path) if sys.platform == "linux" else path
                if identity:
                    devices.append(
                        IndexedDevice(
                            DeviceInfo(str(device.name), identity), int(device.index), api
                        )
                    )
        return tuple(devices)

    def open(self, device: IndexedDevice) -> Camera:
        """Open the candidate's explicit API and index without CAP_ANY substitution."""
        if device.backend not in self._apis() or device.index < 0:
            message = "映像取得方式または機器番号が不正"
            raise CaptureError(message)
        return cv2.VideoCapture(device.index, device.backend)


def acquire_frames(
    options: CaptureOptions,
    generation: int,
    backend: CaptureBackend,
    stopped: "Callable[[], bool]",
    publish: "Callable[[Frame], None]",
    released: "Callable[[], None] | None" = None,
    enumerated: "Callable[[tuple[DeviceInfo, ...]], None] | None" = None,
    candidates: "Callable[[tuple[int, ...], int], None] | None" = None,
    api_preference: int | None = None,
) -> None:
    """Resolve a device, acquire validated frames, and always release its handle.

    Args:
        options: Immutable connection settings; FourCC stays at the backend default.
        generation: Host-assigned connection identifier.
        backend: Synchronous device adapter owned by this process.
        stopped: Nonblocking cancellation check, including after a blocking read.
        publish: Receives actual pixels and dimensions without resizing; may skip a
            frame if the reader is busy. The first dimensions remain fixed until stop.
        released: Optional notification after the native release returned normally.
        enumerated: Optional notification of selectable identities from the same
            enumeration used to resolve the device, before selection and opening.
        candidates: Receives at most two matching API identifiers and the chosen API.
        api_preference: Explicit API to re-resolve for a retry, or None for the first
            matching candidate. A missing API never substitutes another candidate.

    Raises:
        CaptureError: Selection, opening, or reading fails, pixels are not uint8 BGR
            within 16..3840 by 16..2160, or dimensions change after the first frame.
    """
    devices = backend.enumerate()
    identities = device_catalog(devices)
    if enumerated is not None:
        enumerated(identities)
    matching = select_candidates(devices, options.device_path)
    if len(matching) > 2:
        message = "映像取得方式の候補数が上限超過"
        raise CaptureError(message)
    selected = next(
        (
            device
            for device in matching
            if api_preference is None or device.backend == api_preference
        ),
        None,
    )
    if selected is None:
        message = "指定した映像取得方式で同じ機器が見つからない"
        raise CaptureError(message)
    if candidates is not None:
        candidates(tuple(device.backend for device in matching), selected.backend)
    if stopped():
        return
    camera = backend.open(selected)
    try:
        if not camera.isOpened():
            message = "映像取得元を開けない"
            raise CaptureError(message)
        if options.width is not None and options.height is not None:
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, options.width)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, options.height)
        camera.set(cv2.CAP_PROP_FPS, options.fps)
        sequence = 0
        dimensions: tuple[int, int] | None = None
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
                or pixels.ndim != 3
                or pixels.shape[2] != 3
                or not 16 <= pixels.shape[1] <= 3840
                or not 16 <= pixels.shape[0] <= 2160
            ):
                message = "取得映像は幅16〜3840・高さ16〜2160のuint8 BGR画像が必要"
                raise CaptureError(message)
            height, width, _channels = pixels.shape
            if dimensions is not None and dimensions != (width, height):
                message = "取得中に映像寸法が変化したため停止。再接続が必要"
                raise CaptureError(message)
            dimensions = (width, height)
            sequence += 1
            publish(
                Frame(
                    pixels.tobytes(),
                    width,
                    height,
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
