"""Shared serial protocol capabilities and connection settings."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InputProtocol:
    """Supported inputs and baud rates for one firmware protocol.

    Attributes:
        key: Stable settings identifier.
        label: Device-family name for selection and tool descriptions.
        default_baudrate: Rate used only when the setting is omitted.
        baudrates: Supported choices, in ascending order.
        buttons: Accepted button and direction names.
        touch: Whether 320 by 240 touch coordinates are supported.
    """

    key: str
    label: str
    default_baudrate: int
    baudrates: tuple[int, ...]
    buttons: frozenset[str]
    touch: bool = False


_COMMON = frozenset(("A", "B", "X", "Y", "L", "R", "ZL", "ZR", "HOME"))
_DIRECTIONS = frozenset(("UP", "RIGHT", "DOWN", "LEFT"))
_SWITCH = _COMMON | _DIRECTIONS | {"PLUS", "MINUS", "LS", "RS", "CAPTURE"}
PROTOCOLS = (
    InputProtocol("ch552", "CH552", 9600, (9600,), _SWITCH),
    InputProtocol("pokecon", "PokeCon", 9600, (9600, 19200, 38400, 57600, 115200), _SWITCH),
    InputProtocol(
        "3ds",
        "3DS S2/T3",
        115200,
        (9600, 19200, 57600, 115200),
        _COMMON | _DIRECTIONS | {"START", "SELECT"},
        touch=True,
    ),
)


def get_protocol(key: object) -> InputProtocol:
    """Return a supported protocol, raising ValueError for an unknown identifier."""
    for protocol in PROTOCOLS:
        if key == protocol.key:
            return protocol
    message = "controller.serial.protocol: ch552, pokecon, 3dsのいずれかが必要です"
    raise ValueError(message)


def resolve_baudrate(protocol: InputProtocol, value: object) -> int:
    """Resolve an omitted rate or validate an explicit integer choice.

    Raises:
        ValueError: The value is not an integer supported by this protocol.
    """
    if value is None:
        return protocol.default_baudrate
    if isinstance(value, bool) or not isinstance(value, int) or value not in protocol.baudrates:
        message = (
            f"controller.serial.baudrate: {protocol.label}の対応値 {protocol.baudrates} が必要です"
        )
        raise ValueError(message)
    return int(value)


@dataclass(frozen=True, slots=True)
class SerialConfig:
    """Resolved connection values; port availability is checked when opening.

    Use resolve_serial_config() to resolve settings without opening a device.
    """

    port: str
    protocol: InputProtocol
    baudrate: int


def resolve_serial_config(port: str, protocol: str, baudrate: int | None) -> SerialConfig | None:
    """Resolve one settings snapshot without enumerating or connecting devices.

    Args:
        port: Exact native port identifier, or empty for disconnected.
        protocol: Key from PROTOCOLS.
        baudrate: Explicit supported rate, or None to use the protocol default.

    Returns:
        Effective settings, or None for an empty port. Even disconnected settings
        must have a valid protocol and baud rate.

    Raises:
        ValueError: The protocol, baud rate, or port value is invalid.
    """
    selected = get_protocol(protocol)
    rate = resolve_baudrate(selected, baudrate)
    if not isinstance(port, str) or "://" in port:
        message = "controller.serial.port: ネイティブポートの識別子が必要です"
        raise ValueError(message)
    return SerialConfig(port, selected, rate) if port else None
