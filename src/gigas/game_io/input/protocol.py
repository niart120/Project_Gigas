"""Stateless complete-state encoders for CH552, PokeCon and 3DS S2/T3."""

from typing import TYPE_CHECKING

from gigas.game_io.input.models import InvalidSequenceError, validate_state

if TYPE_CHECKING:
    from gigas.game_io.input.config import InputProtocol
    from gigas.game_io.input.models import InputState

_SWITCH_BITS = {
    "Y": 0x0001,
    "B": 0x0002,
    "A": 0x0004,
    "X": 0x0008,
    "L": 0x0010,
    "R": 0x0020,
    "ZL": 0x0040,
    "ZR": 0x0080,
    "MINUS": 0x0100,
    "PLUS": 0x0200,
    "LS": 0x0400,
    "RS": 0x0800,
    "HOME": 0x1000,
    "CAPTURE": 0x2000,
}
_THREE_DS_BITS = {
    "LEFT": 0x0001,
    "DOWN": 0x0002,
    "RIGHT": 0x0004,
    "UP": 0x0008,
    "A": 0x0010,
    "B": 0x0020,
    "X": 0x0040,
    "Y": 0x0080,
    "L": 0x0100,
    "R": 0x0200,
    "HOME": 0x0400,
    "START": 0x0800,
    "SELECT": 0x1000,
    "ZL": 0x4000,
    "ZR": 0x8000,
}
_DIRECTIONS = frozenset(("UP", "RIGHT", "DOWN", "LEFT"))
_HATS = {
    frozenset(): 8,
    frozenset(("UP",)): 0,
    frozenset(("UP", "RIGHT")): 1,
    frozenset(("RIGHT",)): 2,
    frozenset(("RIGHT", "DOWN")): 3,
    frozenset(("DOWN",)): 4,
    frozenset(("DOWN", "LEFT")): 5,
    frozenset(("LEFT",)): 6,
    frozenset(("LEFT", "UP")): 7,
}


def _axis(value: int) -> int:
    return 128 - ((-value * 128 + 50) // 100) if value < 0 else 128 + ((value * 127 + 50) // 100)


def _slide(value: int) -> int:
    if value <= 128:
        return round(255 - (value / 128) * 127)
    return round(128 - ((value - 128) / 127) * 128)


def _c_stick(value: int) -> int:
    return 0 if value in (127, 128) else (value - 128) & 255


def encode_state(state: "InputState", protocol: "InputProtocol") -> bytes:
    """Return one complete firmware frame, including neutral for InputState().

    Args:
        state: Immutable buttons, sticks and touch in common input units.
        protocol: A supported protocol from the shared configuration definitions.

    Returns:
        CH552's 11 bytes, PokeCon's uppercase ASCII line, or the 3DS new
        firmware's 14 bytes. The encoder retains no state and performs no I/O.

    Raises:
        InvalidSequenceError: Inputs or the protocol are unsupported. No button
            aliasing, coordinate clipping or ignored unsupported input occurs.
    """
    validate_state(state, protocol)
    lx, ly = (_axis(value) for value in state.left_stick)
    rx, ry = (_axis(value) for value in state.right_stick)
    if protocol.key == "3ds":
        mask = sum(_THREE_DS_BITS[name] for name in state.buttons)
        touch = (0, 0, 0, 0)
        if state.touch is not None:
            x, y = state.touch
            touch = (1, x >> 8, x & 255, y)
        return bytes(
            (
                0xA1,
                mask & 255,
                mask >> 8,
                0xA2,
                _slide(lx),
                _slide(ly),
                0xA4,
                _c_stick(rx),
                _c_stick(ry),
                0xB2,
                *touch,
            )
        )
    if protocol.key not in ("ch552", "pokecon"):
        message = f"非対応の入力プロトコルです: {protocol.key}"
        raise InvalidSequenceError(message)
    mask = sum(_SWITCH_BITS[name] for name in state.buttons if name not in _DIRECTIONS)
    hat = _HATS[frozenset(state.buttons) & _DIRECTIONS]
    if protocol.key == "ch552":
        return bytes((0xAB, mask & 255, mask >> 8, hat, lx, ly, rx, ry, 0, 0, 0))
    return f"0X{3 | (mask << 2):X} {hat:X} {lx:X} {ly:X} {rx:X} {ry:X}\r\n".encode("ascii")
