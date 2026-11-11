import pytest

from gigas.game_io.capture.models import CaptureError, CaptureOptions, DeviceInfo, select_device


def test_selects_exact_path_despite_duplicate_names_and_changed_indices() -> None:
    devices = (DeviceInfo("Capture", "path-b"), DeviceInfo("Capture", "path-a"))
    assert select_device(devices, "path-a") is devices[1]


@pytest.mark.parametrize("path", ["", "Capture", "0", "missing"])
def test_missing_or_legacy_selection_never_falls_back(path: str) -> None:
    with pytest.raises(CaptureError):
        select_device((DeviceInfo("Capture", "path-a"),), path)


def test_ambiguous_path_is_rejected() -> None:
    with pytest.raises(CaptureError):
        select_device((DeviceInfo("A", "same"), DeviceInfo("B", "same")), "same")


def test_default_mode_and_supported_boundaries() -> None:
    assert CaptureOptions("path-a") == CaptureOptions("path-a", 1920, 1080, 60)
    assert CaptureOptions("path-a", 16, 16, 1).fps == 1
    assert CaptureOptions("path-a", 3840, 2160, 120).width == 3840


@pytest.mark.parametrize(
    ("width", "height", "fps"),
    [
        (15, 16, 1),
        (3841, 16, 1),
        (16, 15, 1),
        (16, 2161, 1),
        (16, 16, 0),
        (16, 16, 121),
        (True, 16, 1),
        (16, 16, True),
    ],
)
def test_invalid_modes_are_rejected(width: int, height: int, fps: int) -> None:
    with pytest.raises(CaptureError):
        CaptureOptions("path-a", width, height, fps)


def test_empty_path_is_not_a_connectable_option() -> None:
    with pytest.raises(CaptureError):
        CaptureOptions("")
