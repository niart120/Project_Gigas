import asyncio
import sys
from threading import Event, get_ident
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from gigas.game_io.input import serial as module
from gigas.game_io.input.config import resolve_serial_config
from gigas.game_io.input.models import InputState
from gigas.game_io.input.serial import SerialInput, SerialInputError, SerialPort

if TYPE_CHECKING:
    from gigas.game_io.input.config import SerialConfig


@pytest.mark.parametrize(
    ("platform", "identifier"),
    [("win32", "COM3"), ("linux", "/dev/ttyACM0"), ("darwin", "/dev/cu.usbmodem1")],
)
def test_port_listing_uses_pyserial_native_identifiers(
    monkeypatch: pytest.MonkeyPatch, platform: str, identifier: str
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(
        module.list_ports,
        "comports",
        lambda: [SimpleNamespace(device=identifier, description="controller")],
    )
    assert module.list_serial_ports() == (SerialPort(identifier, "controller"),)


class Port:
    """A controlled serial boundary recording completed calls and their threads."""

    def __init__(self) -> None:
        """Prepare a neutral-capable port without device access."""
        self.writes: list[bytes] = []
        self.calls: list[tuple[str, int]] = []
        self.entered = Event()
        self.release = Event()
        self.block = False
        self.partial = False
        self.fail = False

    def write(self, data: bytes) -> int:
        """Optionally hold or fail one write."""
        self.calls.append(("write", get_ident()))
        self.writes.append(data)
        self.entered.set()
        if self.block and not self.release.wait(5):
            raise TimeoutError
        if self.fail:
            message = "write failed"
            raise OSError(message)
        return len(data) - int(self.partial)

    def close(self) -> None:
        """Record closing on the same worker."""
        self.calls.append(("close", get_ident()))


def make_device(port: Port) -> SerialInput:
    config = resolve_serial_config("COM3", "ch552", None)
    assert config is not None

    def open_port(_config: "SerialConfig") -> Port:
        port.calls.append(("open", get_ident()))
        return port

    return SerialInput(config, open_port=open_port)


def test_open_sends_neutral_and_io_stays_on_one_worker() -> None:
    port = Port()

    async def scenario() -> None:
        device = make_device(port)
        await device.open()
        await device.send(InputState(buttons=("A",)))
        await device.send(InputState())
        await device.close()
        await device.close()

    asyncio.run(scenario())
    neutral = bytes.fromhex("AB 00 00 08 80 80 80 80 00 00 00")
    assert port.writes == [neutral, bytes.fromhex("AB 04 00 08 80 80 80 80 00 00 00"), neutral]
    assert [kind for kind, _thread in port.calls] == ["open", "write", "write", "write", "close"]
    assert len({thread for _kind, thread in port.calls}) == 1
    assert port.calls[0][1] != get_ident()


@pytest.mark.parametrize("cancel_count", [1, 2])
def test_cancelled_write_is_recovered_before_neutral_or_close(cancel_count: int) -> None:
    port = Port()

    async def scenario() -> None:
        device = make_device(port)
        await device.open()
        port.entered.clear()
        port.block = True
        task = asyncio.create_task(device.send(InputState(buttons=("A",))))
        try:
            assert await asyncio.to_thread(port.entered.wait, 2)
            for _ in range(cancel_count):
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done()
            with pytest.raises(SerialInputError):
                await device.send(InputState(buttons=("B",)))
            with pytest.raises(SerialInputError):
                await device.close()
            assert len(port.writes) == 2
            assert all(kind != "close" for kind, _thread in port.calls)
        finally:
            port.release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await device.send(InputState())
            await device.close()
        assert len(port.writes) == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["partial", "exception"])
def test_incomplete_write_is_not_retried(failure: str) -> None:
    port = Port()

    async def scenario() -> None:
        device = make_device(port)
        await device.open()
        port.partial = failure == "partial"
        port.fail = failure == "exception"
        with pytest.raises(SerialInputError):
            await device.send(InputState(buttons=("A",)))
        await device.close()

    asyncio.run(scenario())
    assert len(port.writes) == 2


def test_failed_initial_neutral_closes_candidate() -> None:
    port = Port()
    port.partial = True

    async def scenario() -> None:
        device = make_device(port)
        with pytest.raises(SerialInputError):
            await device.open()
        await device.close()

    asyncio.run(scenario())
    assert [kind for kind, _thread in port.calls] == ["open", "write", "close"]


def test_native_factory_uses_exact_port_and_explicit_serial_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[str, object]] = []

    class NativePort(Port):
        """Capture pySerial settings without opening a device."""

        def __init__(self, **options: object) -> None:
            super().__init__()
            opened.extend(options.items())
            self.port: str | None = None
            self.dtr = False
            self.rts = False

        def open(self) -> None:
            """Record configured control lines and selected identity."""
            opened.extend((("selected", self.port), ("dtr", self.dtr), ("rts", self.rts)))

    monkeypatch.setattr(module.serial, "Serial", NativePort)
    monkeypatch.setattr(module, "list_serial_ports", lambda: (SerialPort("COM3", "controller"),))

    async def scenario() -> None:
        config = resolve_serial_config("COM3", "3ds", None)
        assert config is not None
        device = SerialInput(config)
        await device.open()
        await device.close()
        missing = resolve_serial_config("COM4", "3ds", None)
        assert missing is not None
        device = SerialInput(missing)
        with pytest.raises(SerialInputError):
            await device.open()
        await device.close()

    asyncio.run(scenario())
    assert dict(opened) == {
        "port": None,
        "baudrate": 115200,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "timeout": 0,
        "write_timeout": None,
        "xonxoff": False,
        "rtscts": False,
        "dsrdtr": False,
        "selected": "COM3",
        "dtr": True,
        "rts": True,
    }
