"""Translate one manual gesture into complete controller states."""

from math import atan2, hypot, pi
from typing import Literal

from gigas.game_io.input.models import InputState


class ManualInputModel:
    """Keep a gesture's state independently of devices and execution services."""

    def __init__(self) -> None:
        """Start without a held gesture."""
        self.state = InputState()
        self.held = False

    def press(self, button: str) -> None:
        """Begin a single button hold."""
        self.held = True
        self.state = InputState(buttons=(button,))

    def move_stick(self, side: Literal["left", "right"], x: float, y: float) -> None:
        """Map normalized coordinates into a circular -100..100 stick range."""
        radius = max(1.0, hypot(x, y))
        axes = (round(100 * x / radius), round(100 * y / radius))
        self.held = True
        self.state = InputState(left_stick=axes) if side == "left" else InputState(right_stick=axes)

    def move_pad(self, x: float, y: float) -> None:
        """Map a pad position to eight sectors or its central dead zone."""
        directions = (
            ("RIGHT",),
            ("DOWN", "RIGHT"),
            ("DOWN",),
            ("DOWN", "LEFT"),
            ("LEFT",),
            ("UP", "LEFT"),
            ("UP",),
            ("UP", "RIGHT"),
        )
        buttons = () if hypot(x, y) < 0.27 else directions[round(atan2(y, x) / (pi / 4)) % 8]
        self.held = True
        self.state = InputState(buttons=buttons)

    def release(self) -> bool:
        """Clear the gesture and return whether it was held, even at neutral."""
        held = self.held
        self.held = False
        self.state = InputState()
        return held
