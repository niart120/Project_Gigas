"""Capture connection settings and resolution preset conversion."""

from dataclasses import dataclass

from gigas.game_io.capture.models import CaptureError

_CAPTURE_DIMENSIONS: dict[str, tuple[int | None, int | None]] = {
    "auto": (None, None),
    "hd": (1280, 720),
    "full_hd": (1920, 1080),
    "wqhd": (2560, 1440),
    "four_k": (3840, 2160),
}


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


def resolve_capture_options(device_path: str, resolution: str, fps: int) -> CaptureOptions:
    """Convert saved capture values into validated connection options.

    Args:
        device_path: Selected device identifier.
        resolution: auto, hd, full_hd, wqhd, or four_k preset.
        fps: Requested capture rate in frames per second.

    Returns:
        Connection options with the preset's requested dimensions.

    Raises:
        CaptureError: The preset is unsupported or the connection values are invalid.
    """
    dimensions = _CAPTURE_DIMENSIONS.get(resolution)
    if dimensions is None:
        message = "取得解像度が未対応"
        raise CaptureError(message)
    return CaptureOptions(device_path, *dimensions, fps=fps)
