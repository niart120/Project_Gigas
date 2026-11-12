import pytest

from gigas.game_io.capture.config import CaptureOptions
from gigas.game_io.capture.models import CaptureError


def test_default_mode_and_supported_boundaries() -> None:
    assert CaptureOptions("path-a") == CaptureOptions("path-a", 1920, 1080, 60)
    assert CaptureOptions("path-a", 16, 16, 1).fps == 1
    assert CaptureOptions("path-a", 3840, 2160, 120).width == 3840


def test_automatic_mode_leaves_both_dimensions_unspecified() -> None:
    options = CaptureOptions("path-a", None, None, 30)
    assert options.width is None
    assert options.height is None
    assert options.fps == 30


@pytest.mark.parametrize(("width", "height"), [(None, 1080), (1920, None)])
def test_automatic_mode_cannot_specify_only_one_dimension(
    width: int | None, height: int | None
) -> None:
    with pytest.raises(CaptureError):
        CaptureOptions("path-a", width, height)


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
