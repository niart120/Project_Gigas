"""Private spawn entry and bounded shared-memory frame transport."""

import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from multiprocessing.shared_memory import SharedMemory
from struct import Struct
from typing import TYPE_CHECKING, Literal, Protocol

from gigas.game_io.capture.backend import acquire_frames, device_catalog
from gigas.game_io.capture.models import CaptureError, DeviceInfo, Frame

if TYPE_CHECKING:
    from collections.abc import Callable
    from multiprocessing.context import BaseContext
    from multiprocessing.synchronize import Lock

    from gigas.game_io.capture.backend import CaptureBackend
    from gigas.game_io.capture.models import CaptureOptions

_HEADER = Struct("<QQqII")
_MAX_PIXELS = 3840 * 2160 * 3
MESSAGE_LIMIT = 65536


class Channel(Protocol):
    """Portable public operations of a multiprocessing pipe endpoint."""

    def send_bytes(self, value: bytes, /) -> None:
        """Send one small control message."""
        ...

    def recv_bytes(self, maxlength: int, /) -> bytes:
        """Receive a control message after readiness was observed."""
        ...

    def poll(self, timeout: float = 0.0, /) -> bool:
        """Check readiness without indefinitely waiting."""
        ...

    def close(self) -> None:
        """Close this endpoint without acquiring another process's lock."""
        ...


@dataclass(frozen=True, slots=True)
class WorkerMessage:
    """A small state notification; images never travel through the pipe."""

    kind: Literal["devices", "opened", "released", "error"]
    devices: "tuple[DeviceInfo, ...]" = ()
    fps: float | None = None
    error: str = ""


def send_message(channel: Channel, message: WorkerMessage) -> None:
    """Send bounded JSON control data without pickle or frame-sized pipe writes."""
    data = json.dumps(
        {
            "kind": message.kind,
            "devices": [{"name": d.name, "path": d.path} for d in message.devices],
            "fps": message.fps,
            "error": message.error,
        },
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(data) > MESSAGE_LIMIT or len(message.devices) > 64:
        error = "映像機器の列挙結果が上限超過"
        raise CaptureError(error)
    channel.send_bytes(data)


def receive_message(channel: Channel) -> WorkerMessage:
    """Validate a small control message after a pipe readiness check."""
    value: object = json.loads(channel.recv_bytes(MESSAGE_LIMIT))
    error = "映像取得プロセスの通知が不正"
    if not isinstance(value, dict):
        raise CaptureError(error)
    kind = value.get("kind")
    if kind not in ("devices", "opened", "released", "error"):
        raise CaptureError(error)
    raw_devices = value.get("devices")
    if not isinstance(raw_devices, list) or len(raw_devices) > 64:
        raise CaptureError(error)
    devices = []
    for device in raw_devices:
        if not isinstance(device, dict):
            raise CaptureError(error)
        name, path = device.get("name"), device.get("path")
        if not isinstance(name, str) or not isinstance(path, str) or not path:
            raise CaptureError(error)
        devices.append(DeviceInfo(name, path))
    fps = value.get("fps")
    if fps is not None and (type(fps) not in (int, float) or not 0 < fps < float("inf")):
        raise CaptureError(error)
    message = value.get("error")
    if not isinstance(message, str):
        raise CaptureError(error)
    match kind:
        case "devices":
            return WorkerMessage("devices", devices=tuple(devices))
        case "opened":
            return WorkerMessage("opened", fps=None if fps is None else float(fps))
        case "released":
            return WorkerMessage("released")
        case _:
            return WorkerMessage("error", error=message)


class SharedSlot:
    """Copy one frame atomically, with bounded lock acquisition on both sides."""

    def __init__(self, memory: SharedMemory, lock: "Lock") -> None:
        """Attach already owned shared storage and its generation-specific lock."""
        if memory.size < _HEADER.size + _MAX_PIXELS:
            message = "映像共有メモリの容量不足"
            raise CaptureError(message)
        self.memory = memory
        self.lock = lock

    def _buffer(self) -> memoryview:
        buffer = self.memory.buf
        if buffer is None:
            message = "映像共有メモリが解放済み"
            raise CaptureError(message)
        return buffer

    @classmethod
    def create(cls, context: "BaseContext", _options: "CaptureOptions") -> "SharedSlot":
        """Allocate one 4K BGR capacity regardless of the requested device resolution."""
        memory = SharedMemory(create=True, size=_HEADER.size + _MAX_PIXELS)
        try:
            return cls(memory, context.Lock())
        except BaseException:
            memory.close()
            memory.unlink()
            raise

    def publish(self, frame: Frame) -> None:
        """Replace the slot, dropping this frame if the reader briefly owns it."""
        self._validate_dimensions(frame.width, frame.height)
        if not self.lock.acquire(timeout=0.01):
            return
        try:
            buffer = self._buffer()
            buffer[_HEADER.size : _HEADER.size + len(frame.bgr)] = frame.bgr
            _HEADER.pack_into(
                buffer,
                0,
                frame.sequence,
                frame.monotonic_ns,
                int(frame.captured_at.timestamp() * 1_000_000),
                frame.width,
                frame.height,
            )
        finally:
            self.lock.release()

    def read(self, generation: int, after_sequence: int) -> Frame | None:
        """Return a new immutable copy without waiting indefinitely for a dead writer."""
        if not self.lock.acquire(timeout=0.005):
            return None
        try:
            buffer = self._buffer()
            sequence, tick, timestamp, width, height = _HEADER.unpack_from(buffer)
            if sequence <= after_sequence:
                return None
            self._validate_dimensions(width, height)
            return Frame(
                bytes(buffer[_HEADER.size : _HEADER.size + width * height * 3]),
                width,
                height,
                generation,
                sequence,
                datetime.fromtimestamp(timestamp / 1_000_000, UTC),
                tick,
            )
        finally:
            self.lock.release()

    @staticmethod
    def _validate_dimensions(width: int, height: int) -> None:
        if not 16 <= width <= 3840 or not 16 <= height <= 2160:
            message = "共有映像の寸法が受入範囲外"
            raise CaptureError(message)

    def close(self, *, owner: bool) -> None:
        """Release the local mapping and, for the host, unlink its owned allocation."""
        self.memory.close()
        if owner:
            self.memory.unlink()


def _stop_requested(stop: Channel) -> bool:
    try:
        return stop.poll(0)
    except (EOFError, OSError):
        return True


def worker_main(
    options: "CaptureOptions | None",
    generation: int,
    memory_name: str | None,
    lock: "Lock | None",
    stop: Channel,
    status: Channel,
    factory: "Callable[[], CaptureBackend]",
) -> None:
    """Own all synchronous device calls until cancellation, failure, or forced exit."""
    slot = None
    try:
        backend = factory()
        if options is None:
            devices = device_catalog(backend.enumerate())
            send_message(status, WorkerMessage("devices", devices=devices))
        elif memory_name is not None and lock is not None:
            slot = SharedSlot(SharedMemory(name=memory_name), lock)
            acquire_frames(
                options,
                generation,
                backend,
                lambda: _stop_requested(stop),
                slot.publish,
                lambda fps: send_message(status, WorkerMessage("opened", fps=fps)),
                lambda: send_message(status, WorkerMessage("released")),
                enumerated=lambda devices: send_message(
                    status, WorkerMessage("devices", devices=devices)
                ),
            )
    except Exception as failure:  # noqa: BLE001 -- sanitize the native driver boundary
        error = str(failure) if isinstance(failure, CaptureError) else "映像取得処理が失敗"
        with suppress(BrokenPipeError, EOFError, OSError):
            send_message(status, WorkerMessage("error", error=error))
    finally:
        if slot is not None:
            slot.close(owner=False)
        stop.close()
        status.close()
