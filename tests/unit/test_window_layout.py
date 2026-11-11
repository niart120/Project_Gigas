import pytest

from gigas.ui.layout import WINDOW_SIZE_PRESETS, fit_window_size_preset, window_size_preset


@pytest.mark.parametrize(
    ("key", "dimensions"),
    [
        ("hd", (1280, 720, 768, 432)),
        ("full_hd", (1920, 1080, 1280, 720)),
        ("wqhd", (2560, 1440, 1600, 900)),
        ("four_k", (3840, 2160, 2560, 1440)),
    ],
)
def test_presets_define_window_and_preview_sizes(
    key: str, dimensions: tuple[int, int, int, int]
) -> None:
    preset = window_size_preset(key)

    assert (
        preset.window_width,
        preset.window_height,
        preset.preview_width,
        preset.preview_height,
    ) == dimensions
    assert preset in WINDOW_SIZE_PRESETS
    assert fit_window_size_preset(key, 7680, 4320) == preset


def test_preset_choices_have_four_distinct_stable_keys() -> None:
    assert tuple(preset.key for preset in WINDOW_SIZE_PRESETS) == (
        "hd",
        "full_hd",
        "wqhd",
        "four_k",
    )


@pytest.mark.parametrize("limit", ["width", "height"])
@pytest.mark.parametrize(
    ("key", "dimensions"),
    [
        ("hd", (640, 360, 384, 216)),
        ("full_hd", (960, 540, 640, 360)),
        ("wqhd", (1280, 720, 800, 450)),
        ("four_k", (1920, 1080, 1280, 720)),
    ],
)
def test_small_screen_scales_window_and_preview_together(
    key: str, dimensions: tuple[int, int, int, int], limit: str
) -> None:
    width, height, preview_width, preview_height = dimensions
    fitted = fit_window_size_preset(
        key,
        width if limit == "width" else 7680,
        height if limit == "height" else 4320,
    )

    assert (fitted.window_width, fitted.window_height) == (width, height)
    assert (fitted.preview_width, fitted.preview_height) == (preview_width, preview_height)
    assert fitted.key == key


def test_fractional_size_rounds_down_to_stay_within_screen() -> None:
    fitted = fit_window_size_preset("full_hd", 1280, 720)

    assert (fitted.window_width, fitted.window_height) == (1280, 720)
    assert (fitted.preview_width, fitted.preview_height) == (853, 480)


@pytest.mark.parametrize("available", [0, 1])
def test_sizes_never_fall_below_one_pixel(available: int) -> None:
    fitted = fit_window_size_preset("full_hd", available, available)

    assert (
        fitted.window_width,
        fitted.window_height,
        fitted.preview_width,
        fitted.preview_height,
    ) == (1, 1, 1, 1)


def test_unknown_preset_is_rejected_without_defaulting() -> None:
    with pytest.raises(ValueError, match="unknown"):
        window_size_preset("unknown")
    with pytest.raises(ValueError, match="unknown"):
        fit_window_size_preset("unknown", 1920, 1080)
