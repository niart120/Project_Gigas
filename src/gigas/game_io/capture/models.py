"""Immutable captured frames and device identity."""

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
        monotonic_ns: Monotonic time immediately after acquisition within this host.

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
