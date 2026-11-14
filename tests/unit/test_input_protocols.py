from dataclasses import FrozenInstanceError, replace

import pytest

from gigas.game_io.input.config import get_protocol
from gigas.game_io.input.models import InputState, InputStep, InvalidSequenceError, parse_sequence
from gigas.game_io.input.protocol import encode_state


@pytest.mark.parametrize(
    ("protocol", "expected"),
    [
        ("ch552", bytes.fromhex("AB 00 00 08 80 80 80 80 00 00 00")),
        ("pokecon", b"0X3 8 80 80 80 80\r\n"),
        ("3ds", bytes.fromhex("A1 00 00 A2 80 80 A4 00 00 B2 00 00 00 00")),
    ],
)
def test_neutral_is_a_complete_frame(protocol: str, expected: bytes) -> None:
    assert encode_state(InputState(), get_protocol(protocol)) == expected


@pytest.mark.parametrize(
    ("protocol", "expected"),
    [
        ("ch552", bytes.fromhex("AB 04 22 01 00 FF 40 C0 00 00 00")),
        ("pokecon", b"0X8813 1 0 FF 40 C0\r\n"),
    ],
)
def test_switch_protocols_encode_complete_simultaneous_input(
    protocol: str, expected: bytes
) -> None:
    selected = get_protocol(protocol)
    steps = parse_sequence(
        [
            {
                "kind": "press",
                "buttons": ["A", "PLUS", "CAPTURE", "UP", "RIGHT"],
                "left_stick": {"x": -100, "y": 100},
                "right_stick": {"x": -50, "y": 50},
                "duration_ms": 100,
            }
        ],
    )
    assert encode_state(steps[0].state, selected) == expected
    # Encoding another full state must not retain the previous buttons or sticks.
    assert encode_state(InputState(buttons=("B",)), selected) == (
        bytes.fromhex("AB 02 00 08 80 80 80 80 00 00 00")
        if protocol == "ch552"
        else b"0XB 8 80 80 80 80\r\n"
    )


def test_3ds_encodes_buttons_directions_sticks_and_touch_together() -> None:
    selected = get_protocol("3ds")
    step = parse_sequence(
        [
            {
                "kind": "press",
                "buttons": ["A", "START", "UP", "RIGHT"],
                "duration_ms": 1,
                "left_stick": {"x": -50, "y": 50},
                "right_stick": {"x": -1, "y": 100},
                "touch": {"x": 319, "y": 239},
            }
        ],
    )[0]
    assert encode_state(step.state, selected) == bytes.fromhex(
        "A1 1C 08 A2 C0 3F A4 00 7F B2 01 01 3F EF"
    )


@pytest.mark.parametrize(
    ("button", "switch_mask", "three_ds_mask"),
    [
        ("Y", 1, 0x80),
        ("B", 2, 0x20),
        ("A", 4, 0x10),
        ("X", 8, 0x40),
        ("L", 0x10, 0x100),
        ("R", 0x20, 0x200),
        ("ZL", 0x40, 0x4000),
        ("ZR", 0x80, 0x8000),
        ("HOME", 0x1000, 0x400),
    ],
)
def test_common_buttons_have_protocol_specific_bits(
    button: str,
    switch_mask: int,
    three_ds_mask: int,
) -> None:
    state = InputState(buttons=(button,))
    assert encode_state(state, get_protocol("ch552"))[1:3] == switch_mask.to_bytes(2, "little")
    assert encode_state(state, get_protocol("3ds"))[1:3] == three_ds_mask.to_bytes(2, "little")


@pytest.mark.parametrize(
    ("buttons", "hat", "three_ds_bits"),
    [
        ((), 8, 0),
        (("UP",), 0, 8),
        (("UP", "RIGHT"), 1, 12),
        (("RIGHT",), 2, 4),
        (("RIGHT", "DOWN"), 3, 6),
        (("DOWN",), 4, 2),
        (("DOWN", "LEFT"), 5, 3),
        (("LEFT",), 6, 1),
        (("LEFT", "UP"), 7, 9),
    ],
)
def test_hat_directions(buttons: tuple[str, ...], hat: int, three_ds_bits: int) -> None:
    state = InputState(buttons=buttons)
    assert encode_state(state, get_protocol("ch552"))[3] == hat
    assert encode_state(state, get_protocol("3ds"))[1] == three_ds_bits


def test_sequence_is_detached_immutable_and_serializable() -> None:
    buttons = ["A"]
    axes = {"x": 10, "y": -10}
    payload = [
        {"kind": "press", "buttons": buttons, "left_stick": axes, "duration_ms": 100},
        {"kind": "wait", "duration_ms": 200},
    ]
    steps = parse_sequence(payload)
    buttons.append("B")
    axes["x"] = 99
    assert steps[0].state.buttons == ("A",)
    assert steps[1].state == InputState()
    assert [step.to_json() for step in steps] == [
        {"kind": "press", "buttons": ["A"], "left_stick": {"x": 10, "y": -10}, "duration_ms": 100},
        {"kind": "wait", "duration_ms": 200},
    ]
    for instance, field, value in (
        (steps[0], "duration_ms", 1),
        (steps[0].state, "buttons", ("B",)),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(instance, field, value)


def test_sequence_structure_is_independent_of_selected_capabilities() -> None:
    payload = [{"kind": "press", "buttons": ["START"], "duration_ms": 100}]
    steps = parse_sequence(payload)
    assert steps[0].state.buttons == ("START",)


def test_generated_states_allow_neutral_touch_origin_and_device_specific_buttons() -> None:
    assert InputStep("wait", 1, InputState()).to_json() == {"kind": "wait", "duration_ms": 1}
    step = InputStep(
        "press", 1, InputState(buttons=("FUTURE_BUTTON",), left_stick=(-100, 100), touch=(0, 0))
    )
    assert step.to_json() == {
        "kind": "press",
        "duration_ms": 1,
        "buttons": ["FUTURE_BUTTON"],
        "left_stick": {"x": -100, "y": 100},
        "touch": {"x": 0, "y": 0},
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"buttons": ["A"]},
        {"buttons": (True,)},
        {"buttons": ("A", "A")},
        {"buttons": ("UP", "DOWN")},
        {"buttons": ("LEFT", "RIGHT")},
        {"left_stick": (-101, 0)},
        {"left_stick": (101, 0)},
        {"left_stick": (True, 0)},
        {"left_stick": (1,)},
        {"left_stick": [0, 0]},
        {"right_stick": (0, 0.5)},
        {"touch": (320, 0)},
        {"touch": (0, 240)},
        {"touch": (-1, 0)},
        {"touch": (0, False)},
        {"touch": [0, 0]},
    ],
)
def test_state_construction_rejects_invalid_values(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidSequenceError):
        replace(InputState(), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "hold"},
        {"kind": "press"},
        {"state": None},
        {"state": {}},
        {"state": InputState(buttons=("A",))},
    ],
)
def test_step_construction_rejects_invalid_kind_and_state(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidSequenceError):
        replace(InputStep("wait", 1), **changes)


def test_long_finite_sequence_and_durations_have_no_fixed_limits() -> None:
    raw = [{"kind": "wait", "duration_ms": 10**100}] * 10001
    steps = parse_sequence(raw)
    assert len(steps) == 10001
    assert steps[-1].duration_ms == 10**100


@pytest.mark.parametrize("value", [None, [], {}, (), "wait", iter([1])])
def test_sequence_requires_a_nonempty_finite_array(value: object) -> None:
    with pytest.raises(InvalidSequenceError):
        parse_sequence(value)


@pytest.mark.parametrize("duration", [True, False, 1.0, "1", 0, -1, None])
def test_duration_is_a_positive_integer(duration: object) -> None:
    with pytest.raises(InvalidSequenceError):
        replace(InputStep("wait", 1), duration_ms=duration)


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"kind": "hold", "duration_ms": 1},
        {"kind": "wait", "duration_ms": 1, "buttons": []},
        {"kind": "press", "duration_ms": 1},
        {"kind": "press", "duration_ms": 1, "buttons": "A"},
        {"kind": "press", "duration_ms": 1, "buttons": [True]},
        {"kind": "press", "duration_ms": 1, "buttons": ["A"], "port": "COM3"},
        {"kind": "press", "duration_ms": 1, "buttons": ["A"], "left_stick": {"x": 0}},
        {"kind": "press", "duration_ms": 1, "right_stick": {"x": 1, "y": 0, "z": 0}},
        {"kind": "press", "duration_ms": 1, "touch": [1, 2]},
    ],
)
def test_whole_sequence_rejects_invalid_later_input(invalid: object) -> None:
    with pytest.raises(InvalidSequenceError):
        parse_sequence([{"kind": "press", "buttons": ["A"], "duration_ms": 1}, invalid])
