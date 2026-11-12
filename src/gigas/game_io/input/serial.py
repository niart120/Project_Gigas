"""Single-worker native serial input with owned completion after cancellation."""

import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import serial
from serial.tools import list_ports

from gigas.game_io.input.config import resolve_serial_config
from gigas.game_io.input.models import InputState
from gigas.game_io.input.protocol import encode_state

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.game_io.input.config import SerialConfig


class SerialInputError(OSError):
    """Serial access failed; a sent frame may have reached the device partially."""


class SerialConnection(Protocol):
    """Synchronous serial operations owned by one worker."""

    def write(self, data: bytes) -> int:
        """Return the submitted byte count, without claiming device acceptance."""
        ...

    def close(self) -> None:
        """Close only after any preceding write has returned."""
        ...


@dataclass(frozen=True, slots=True)
class SerialPort:
    """A native port identifier and display description; listing does not open it."""

    identifier: str
    description: str


def list_serial_ports() -> tuple[SerialPort, ...]:
    """List Windows native ports without opening a serial connection.

    Call outside the GUI/event loop. The saved identifier must match exactly;
    descriptions and list positions are not connection identifiers.

    Raises:
        SerialInputError: The platform is not Windows or enumeration fails.
    """
    if sys.platform != "win32":
        message = "シリアル入力はWindowsのネイティブポートに対応しています"
        raise SerialInputError(message)
    try:
        return tuple(SerialPort(port.device, port.description) for port in list_ports.comports())
    except OSError as error:
        message = f"シリアルポートの列挙に失敗しました: {error}"
        raise SerialInputError(message) from error


def _open_native(config: "SerialConfig") -> SerialConnection:
    resolve_serial_config(config.port, config.protocol.key, config.baudrate)
    if sum(port.identifier == config.port for port in list_serial_ports()) != 1:
        message = f"選択したシリアルポートが一意に見つかりません: {config.port}"
        raise SerialInputError(message)
    connection = serial.Serial(
        port=None,
        baudrate=config.baudrate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0,
        write_timeout=None,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )
    connection.port = config.port
    connection.dtr = True
    connection.rts = True
    try:
        connection.open()
    except BaseException:
        connection.close()
        raise
    return connection


class SerialInput:
    """Own one serial connection and one executor on the caller's asyncio loop.

    Args:
        config: Valid resolved serial settings. Construction does not open a port.
        open_port: Synchronous native factory, replaceable at the test boundary.

    Attributes:
        protocol: Input capabilities for this connection, fixed at construction.

    Only one call may be in flight. Cancellation waits for an already submitted
    synchronous call to return; it never abandons a write or closes its handle
    early. There is no fixed write deadline or device acknowledgment. The owner
    sends neutral after cancelling input, then closes when disconnecting. Always
    await close() before closing the event loop, including after open failure.
    """

    def __init__(
        self,
        config: "SerialConfig",
        *,
        open_port: "Callable[[SerialConfig], SerialConnection]" = _open_native,
    ) -> None:
        """Prepare a lazy worker without performing I/O."""
        self.protocol = config.protocol
        self._config = config
        self._open_port = open_port
        self._connection: SerialConnection | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gigas-input")
        self._pending: asyncio.Future[None] | None = None
        self._closed = False

    async def open(self) -> None:
        """Open the exact selected port and send a complete neutral frame.

        Raises:
            SerialInputError: Opening or neutral transmission fails, the worker
                is busy, or this owner has already closed.
        """
        await self._call(self._open)

    async def send(self, state: InputState) -> None:
        """Encode and send one full state, with no retry or flush.

        Raises:
            SerialInputError: The port is unavailable, busy, or the write fails
                or returns a partial byte count. Partial delivery is possible.
            InvalidSequenceError: The state is unsupported by this protocol.
        """
        data = encode_state(state, self.protocol)
        await self._call(lambda: self._write(data))

    async def close(self) -> None:
        """Close a settled connection and release its worker; repeated close is harmless.

        Call after the input operation has finished, including its neutral send.
        This method does not add an independent neutral write.

        Raises:
            SerialInputError: Another I/O call is still active or closing fails.
                Ownership is retained so the caller cannot assume disconnection.
        """
        if self._closed:
            return
        await self._call(self._close)
        self._closed = True
        self._executor.shutdown(wait=True)

    async def _call(self, operation: "Callable[[], None]") -> None:
        if self._closed or (self._pending is not None and not self._pending.done()):
            message = "シリアル処理が実行中、または接続所有者が終了済みです"
            raise SerialInputError(message)
        future = asyncio.get_running_loop().run_in_executor(self._executor, operation)
        self._pending = future
        try:
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                await asyncio.shield(future)
                raise
        except OSError as error:
            message = f"シリアル通信に失敗しました: {error}"
            raise SerialInputError(message) from error

    def _open(self) -> None:
        if self._connection is not None:
            message = "シリアル接続は既に開いています"
            raise SerialInputError(message)
        self._connection = self._open_port(self._config)
        try:
            self._write(encode_state(InputState(), self.protocol))
        except BaseException:
            self._close()
            raise

    def _write(self, data: bytes) -> None:
        if self._connection is None:
            message = "シリアル入力が接続されていません"
            raise SerialInputError(message)
        sent = self._connection.write(data)
        if sent != len(data):
            message = f"シリアル送出結果が不明です: {sent}/{len(data)} byte"
            raise SerialInputError(message)

    def _close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
