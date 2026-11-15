import pytest

from gigas.game_io.input.models import InputState
from gigas.ui.controller_model import ManualInputModel


def test_sticks_clamp_radially_and_neutral_does_not_end_a_gesture() -> None:
    model = ManualInputModel()
    model.move_stick("left", 2, -2)
    assert model.state == InputState(left_stick=(71, -71))
    model.move_stick("left", 0, 0)
    assert model.state == InputState()
    assert model.held
    model.move_stick("left", -0.4, 0.8)
    assert model.state == InputState(left_stick=(-40, 80))
    assert model.release()
    assert model.state == InputState()
    assert not model.release()
    model.move_stick("right", 1, 0)
    assert model.state == InputState(right_stick=(100, 0))


@pytest.mark.parametrize(
    ("x", "y", "buttons"),
    [
        (0, -1, ("UP",)),
        (1, -1, ("UP", "RIGHT")),
        (1, 0, ("RIGHT",)),
        (1, 1, ("DOWN", "RIGHT")),
        (0, 1, ("DOWN",)),
        (-1, 1, ("DOWN", "LEFT")),
        (-1, 0, ("LEFT",)),
        (-1, -1, ("UP", "LEFT")),
        (0.1, 0.1, ()),
    ],
)
def test_pad_has_eight_directions_and_a_neutral_center(
    x: float, y: float, buttons: tuple[str, ...]
) -> None:
    model = ManualInputModel()
    model.move_pad(x, y)
    assert model.state == InputState(buttons=buttons)
    assert model.held
    assert model.release()


def test_a_button_hold_releases_without_restoring_previous_state() -> None:
    model = ManualInputModel()
    model.press("A")
    assert model.state == InputState(buttons=("A",))
    assert model.release()
    model.press("B")
    assert model.state == InputState(buttons=("B",))
