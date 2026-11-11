"""Immutable capture configuration and device identity."""

from collections.abc import Sequence
from dataclasses import dataclass


class CaptureError(ValueError):
    """Capture configuration or device access failed with a displayable reason."""


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """A display name and the DirectShow device path used for selection.

    The path identifies a current Windows device interface, not a permanent
    physical serial number. Names and enumeration indices are not identifiers.
    """

    name: str
    path: str


@dataclass(frozen=True, slots=True)
class CaptureOptions:
    """Validated settings applied only when opening a new capture connection.

    Args:
        device_path: Nonempty path selected from the current device enumeration.
        width: Requested width in pixels, from 16 through 3840.
        height: Requested height in pixels, from 16 through 2160.
        fps: Requested capture rate, from 1 through 120 frames per second.

    Raises:
        CaptureError: The path is empty or the mode contains an invalid integer.
    """

    device_path: str
    width: int = 1920
    height: int = 1080
    fps: int = 60

    def __post_init__(self) -> None:
        """Reject invalid connection requests before accessing a device."""
        if not isinstance(self.device_path, str) or not self.device_path:
            message = "映像取得元が未選択"
            raise CaptureError(message)
        for value, low, high, label in (
            (self.width, 16, 3840, "幅"),
            (self.height, 16, 2160, "高さ"),
            (self.fps, 1, 120, "取得FPS"),
        ):
            if type(value) is not int or not low <= value <= high:
                message = f"{label}: {low}〜{high}の整数が必要"
                raise CaptureError(message)


def select_device(devices: Sequence[DeviceInfo], device_path: str) -> DeviceInfo:
    """Select exactly one device by its path without interpreting legacy values.

    Args:
        devices: Current enumeration, including devices with duplicate names.
        device_path: Exact saved path; names and numeric indices are not accepted.

    Returns:
        The sole matching device.

    Raises:
        CaptureError: The path is empty, absent, or appears more than once.
    """
    matches = [device for device in devices if device_path and device.path == device_path]
    if len(matches) != 1:
        message = "映像取得元が見つからない" if not matches else "映像取得元の識別子が重複"
        raise CaptureError(message)
    return matches[0]
