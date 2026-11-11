"""Immutable capture configuration and device identity."""

from dataclasses import dataclass
from datetime import datetime, timedelta


class CaptureError(ValueError):
    """Capture configuration or device access failed with a displayable reason."""


@dataclass(frozen=True, slots=True)
class Frame:
    """An immutable BGR image with host acquisition timestamps.

    Attributes:
        bgr: Contiguous row-major, three-channel uint8 pixels stored as bytes.
        width: Image width in pixels.
        height: Image height in pixels.
        generation: Connection generation; reconnecting creates a new value.
        sequence: Successful acquisition number within that generation.
        captured_at: UTC time immediately after acquisition, not device exposure time.
        monotonic_ns: Monotonic acquisition time for ordering within this host.

    Raises:
        CaptureError: Pixels, dimensions, identifiers, or the clock are invalid.
    """

    bgr: bytes
    width: int
    height: int
    generation: int
    sequence: int
    captured_at: datetime
    monotonic_ns: int

    def __post_init__(self) -> None:
        """Reject images that cannot be safely shared with multiple readers."""
        if (
            not isinstance(self.bgr, bytes)
            or self.width <= 0
            or self.height <= 0
            or len(self.bgr) != self.width * self.height * 3
            or self.generation <= 0
            or self.sequence <= 0
            or self.monotonic_ns < 0
            or self.captured_at.utcoffset() != timedelta(0)
        ):
            message = "取得フレームの形式が不正"
            raise CaptureError(message)


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """A display name and opaque device identity used for selection.

    The path is a Windows device interface, Linux stable node alias, or macOS
    uniqueID, not a universal physical serial number. Names and enumeration indices
    are not identifiers; identities are not translated between operating systems.
    """

    name: str
    path: str


@dataclass(frozen=True, slots=True)
class CaptureOptions:
    """Validated settings applied only when opening a new capture connection.

    Args:
        device_path: Nonempty path selected from the current device enumeration.
        width: Requested width from 16 through 3840 pixels, or None for automatic mode.
        height: Requested height from 16 through 2160 pixels, or None for automatic mode.
        fps: Requested capture rate, from 1 through 120 frames per second.

    Both dimensions must be None to leave the device's current resolution unchanged.
    Explicit dimensions are requests; frames retain the actual dimensions and pixels.

    Raises:
        CaptureError: The path is empty, only one dimension is None, or a value is invalid.
    """

    device_path: str
    width: int | None = 1920
    height: int | None = 1080
    fps: int = 60

    def __post_init__(self) -> None:
        """Reject invalid connection requests before accessing a device."""
        if not isinstance(self.device_path, str) or not self.device_path:
            message = "映像取得元が未選択"
            raise CaptureError(message)
        if (self.width is None) != (self.height is None):
            message = "自動解像度は幅と高さを両方未指定にする必要がある"
            raise CaptureError(message)
        values = [(self.fps, 1, 120, "取得FPS")]
        if self.width is not None and self.height is not None:
            values.extend(((self.width, 16, 3840, "幅"), (self.height, 16, 2160, "高さ")))
        for value, low, high, label in values:
            if type(value) is not int or not low <= value <= high:
                message = f"{label}: {low}〜{high}の整数が必要"
                raise CaptureError(message)
