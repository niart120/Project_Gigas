"""Initial window size and pane allocation without Qt or device dependencies."""

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class WindowSizePreset:
    """Preferred initial window dimensions and video pane width in logical pixels.

    Attributes:
        key: Stable key stored in gui.window_size_preset.
        label: Short display name for the menu and settings selector.
        window_width: Preferred overall window width.
        window_height: Preferred overall window height.
        preview_width: Initial video pane width used to allocate the horizontal split.
    """

    key: str
    label: str
    window_width: int
    window_height: int
    preview_width: int


WINDOW_SIZE_PRESETS = (
    WindowSizePreset("hd", "HD", 1280, 720, 768),
    WindowSizePreset("full_hd", "FullHD", 1920, 1080, 1280),
    WindowSizePreset("wqhd", "WQHD", 2560, 1440, 1600),
    WindowSizePreset("four_k", "4K", 3840, 2160, 2560),
)


def window_size_preset(key: str) -> WindowSizePreset:
    """Return the standard dimensions for an explicitly selected preset.

    Args:
        key: One of hd, full_hd, wqhd, or four_k.

    Returns:
        The immutable preset with its unscaled window dimensions and video pane width.

    Raises:
        ValueError: The key is not a known preset; no default is substituted.
    """
    for preset in WINDOW_SIZE_PRESETS:
        if preset.key == key:
            return preset
    message = f"Unknown window size preset: {key}"
    raise ValueError(message)


def fit_window_size_preset(
    key: str, available_width: int, available_height: int
) -> WindowSizePreset:
    """Fit an initial window and its pane width using one scale without enlarging them.

    Args:
        key: One of hd, full_hd, wqhd, or four_k.
        available_width: Available width in logical pixels.
        available_height: Available height in logical pixels.

    Returns:
        Preferred dimensions scaled together and rounded down, with a minimum of
        one pixel even when available space is zero. These initial preferences do
        not limit later resizing of the window or its panes.

    Raises:
        ValueError: The key is not a known preset.
    """
    preset = window_size_preset(key)
    scale = min(1.0, available_width / preset.window_width, available_height / preset.window_height)
    return replace(
        preset,
        window_width=max(1, int(preset.window_width * scale)),
        window_height=max(1, int(preset.window_height * scale)),
        preview_width=max(1, int(preset.preview_width * scale)),
    )
