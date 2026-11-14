"""Immutable finite controller input and strict JSON sequence validation."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from gigas.game_io.input.config import InputProtocol


class InvalidSequenceError(ValueError):
    """An input sequence or state cannot be sent without changing its meaning."""


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"{name}: 整数が必要です"
        raise InvalidSequenceError(message)
    return value


def _check_integer(value: object, name: str, minimum: int, maximum: int | None = None) -> None:
    number = _integer(value, name)
    if number < minimum or (maximum is not None and number > maximum):
        message = f"{name}: 値が範囲外です"
        raise InvalidSequenceError(message)


def _check_pair(value: tuple[int, int], name: str, minimum: int, xmax: int, ymax: int) -> None:
    if not isinstance(value, tuple) or len(value) != 2:
        message = f"{name}: x, yの整数タプルが必要です"
        raise InvalidSequenceError(message)
    _check_integer(value[0], f"{name}.x", minimum, xmax)
    _check_integer(value[1], f"{name}.y", minimum, ymax)


@dataclass(frozen=True, slots=True)
class InputState:
    """A complete controller state, with no state inherited from earlier input.

    Attributes:
        buttons: Distinct button names; opposite directions cannot coexist.
        left_stick: Integer x/y axes from -100 to 100; right and down are positive.
        right_stick: The same axes for the right stick.
        touch: Touch x/y from 0..319 and 0..239, or None for released.

    Raises:
        InvalidSequenceError: Values or immutable tuple shapes are invalid.
            Protocol-specific capabilities are checked by validate_state().
    """

    buttons: tuple[str, ...] = ()
    left_stick: tuple[int, int] = (0, 0)
    right_stick: tuple[int, int] = (0, 0)
    touch: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        """Validate immutable state values without selecting a protocol."""
        if not isinstance(self.buttons, tuple) or any(
            not isinstance(button, str) for button in self.buttons
        ):
            message = "buttons: ボタン名のタプルが必要です"
            raise InvalidSequenceError(message)
        names = set(self.buttons)
        if len(names) != len(self.buttons):
            message = "buttons: 同じボタンは重複指定できません"
            raise InvalidSequenceError(message)
        if {"UP", "DOWN"} <= names or {"LEFT", "RIGHT"} <= names:
            message = "buttons: 反対方向は同時指定できません"
            raise InvalidSequenceError(message)
        _check_pair(self.left_stick, "left_stick", -100, 100, 100)
        _check_pair(self.right_stick, "right_stick", -100, 100, 100)
        if self.touch is not None:
            _check_pair(self.touch, "touch", 0, 319, 239)


def validate_state(state: InputState, protocol: "InputProtocol") -> None:
    """Validate a state's inputs against the selected protocol's capabilities.

    Raises:
        InvalidSequenceError: A button or touch input is unsupported.
    """
    unsupported = set(state.buttons) - protocol.buttons
    if unsupported:
        message = f"{protocol.label}: 非対応のボタンです: {', '.join(sorted(unsupported))}"
        raise InvalidSequenceError(message)
    if state.touch is not None and not protocol.touch:
        message = f"{protocol.label}: タッチ入力には対応していません"
        raise InvalidSequenceError(message)


@dataclass(frozen=True, slots=True)
class InputStep:
    """One complete press or neutral wait with a positive integer duration.

    Args:
        kind: press holds state before release; wait holds neutral.
        duration_ms: PC-side waiting time in milliseconds, without a fixed maximum.
        state: Complete state. A press must contain input; a wait must be neutral.

    Raises:
        InvalidSequenceError: The kind, duration, or state is invalid.
    """

    kind: Literal["press", "wait"]
    duration_ms: int
    state: InputState = InputState()

    def __post_init__(self) -> None:
        """Validate the step's duration and input semantics."""
        _check_integer(self.duration_ms, "duration_ms", 1)
        if self.kind not in ("press", "wait") or not isinstance(self.state, InputState):
            message = "kind/state: pressまたはwaitと入力状態が必要です"
            raise InvalidSequenceError(message)
        neutral = self.state == InputState()
        if (self.kind == "press" and neutral) or (self.kind == "wait" and not neutral):
            message = "pressには入力、waitには未押下状態が必要です"
            raise InvalidSequenceError(message)

    def to_json(self) -> dict[str, object]:
        """Return a detached JSON-compatible step with explicit duration and buttons."""
        value: dict[str, object] = {"kind": self.kind, "duration_ms": self.duration_ms}
        if self.kind == "press":
            value["buttons"] = list(self.state.buttons)
            for name, axes in (
                ("left_stick", self.state.left_stick),
                ("right_stick", self.state.right_stick),
            ):
                if axes != (0, 0):
                    value[name] = {"x": axes[0], "y": axes[1]}
            if self.state.touch is not None:
                value["touch"] = {"x": self.state.touch[0], "y": self.state.touch[1]}
        return value


def _coordinates(value: object, name: str) -> tuple[int, int]:
    if not isinstance(value, dict) or value.keys() != {"x", "y"}:
        message = f"{name}: xとyだけを持つオブジェクトが必要です"
        raise InvalidSequenceError(message)
    return (
        _integer(value["x"], f"{name}.x"),
        _integer(value["y"], f"{name}.y"),
    )


def _buttons(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        message = "buttons: ボタン名の配列が必要です"
        raise InvalidSequenceError(message)
    names: list[str] = []
    for button in value:
        if not isinstance(button, str):
            message = "buttons: ボタン名には文字列が必要です"
            raise InvalidSequenceError(message)
        names.append(button)
    return tuple(names)


def _step(value: object) -> InputStep:
    if not isinstance(value, dict):
        message = "sequence: 各要素にはオブジェクトが必要です"
        raise InvalidSequenceError(message)
    kind = value.get("kind")
    allowed = {"kind", "duration_ms"}
    if kind == "press":
        allowed |= {"buttons", "left_stick", "right_stick", "touch"}
    if kind not in ("press", "wait") or value.keys() - allowed:
        message = "sequence: 未知の入力種別または項目があります"
        raise InvalidSequenceError(message)
    duration = _integer(value.get("duration_ms"), "duration_ms")
    if kind == "wait":
        return InputStep("wait", duration)
    state = InputState(
        buttons=_buttons(value.get("buttons", [])),
        left_stick=(
            _coordinates(value["left_stick"], "left_stick") if "left_stick" in value else (0, 0)
        ),
        right_stick=(
            _coordinates(value["right_stick"], "right_stick") if "right_stick" in value else (0, 0)
        ),
        touch=(_coordinates(value["touch"], "touch") if value.get("touch") is not None else None),
    )
    return InputStep("press", duration, state)


def parse_sequence(value: object) -> tuple[InputStep, ...]:
    """Parse an entire JSON sequence into detached immutable steps.

    InputState and InputStep construction establishes common input invariants.
    The execution service checks device capabilities before starting any step.

    Args:
        value: Nonempty JSON array of press/wait objects. Generators, tuples,
            loops, unknown keys and implicit numeric conversions are rejected.

    Returns:
        Nonempty finite steps satisfying common invariants, without choosing a
        device. Durations, count and sum have no fixed limit.

    Raises:
        InvalidSequenceError: JSON structure or a common input invariant is invalid.
            No input or other I/O occurs while parsing.
    """
    if not isinstance(value, list) or not value:
        message = "sequence: 1要素以上の有限配列が必要です"
        raise InvalidSequenceError(message)
    return tuple(_step(item) for item in value)
