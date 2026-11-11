"""Private spawn entry and bounded shared-memory frame transport."""

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from multiprocessing.shared_memory import SharedMemory
from struct import Struct
from typing import TYPE_CHECKING, Literal, Protocol

from gigas.game_io.backend import acquire_frames
from gigas.game_io.models import CaptureError, Frame

if TYPE_CHECKING:
    from collections.abc import Callable
    from multiprocessing.context import BaseContext
    from multiprocessing.synchronize import Lock

    from gigas.game_io.backend import CaptureBackend
    from gigas.game_io.models import CaptureOptions, DeviceInfo

_HEADER = Struct("<QQq")


class Channel(Protocol):
    """Portable public operations of a multiprocessing pipe endpoint."""

    def send(self, value: object, /) -> None:
        """Send one small control message."""
        ...

    def recv(self) -> object:
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


class SharedSlot:
    """Copy one frame atomically, with bounded lock acquisition on both sides."""

    def __init__(self, memory: SharedMemory, lock: "Lock", width: int, height: int) -> None:
        """Attach already owned shared storage and its generation-specific lock."""
        self.memory = memory
        self.lock = lock
        self.width = width
        self.height = height
        self._end = _HEADER.size + width * height * 3

    def _buffer(self) -> memoryview:
        buffer = self.memory.buf
        if buffer is None:
            message = "映像共有メモリが解放済み"
            raise CaptureError(message)
        return buffer

    @classmethod
    def create(cls, context: "BaseContext", options: "CaptureOptions") -> "SharedSlot":
        """Allocate exactly one requested BGR image plus its acquisition metadata."""
        memory = SharedMemory(create=True, size=_HEADER.size + options.width * options.height * 3)
        return cls(memory, context.Lock(), options.width, options.height)

    def publish(self, frame: Frame) -> None:
        """Replace the slot, dropping this frame if the reader briefly owns it."""
        if not self.lock.acquire(timeout=0.01):
            return
        try:
            buffer = self._buffer()
            buffer[_HEADER.size : self._end] = frame.bgr
            _HEADER.pack_into(
                buffer,
                0,
                frame.sequence,
                frame.monotonic_ns,
                int(frame.captured_at.timestamp() * 1_000_000),
            )
        finally:
            self.lock.release()

    def read(self, generation: int, after_sequence: int) -> Frame | None:
        """Return a new immutable copy without waiting indefinitely for a dead writer."""
        if not self.lock.acquire(timeout=0.005):
            return None
        try:
            buffer = self._buffer()
            sequence, tick, timestamp = _HEADER.unpack_from(buffer)
            if sequence <= after_sequence:
                return None
            return Frame(
                bytes(buffer[_HEADER.size : self._end]),
                self.width,
                self.height,
                generation,
                sequence,
                datetime.fromtimestamp(timestamp / 1_000_000, UTC),
                tick,
            )
        finally:
            self.lock.release()

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
            devices = tuple(device.info for device in backend.enumerate() if device.info.path)
            status.send(WorkerMessage("devices", devices=devices))
        elif memory_name is not None and lock is not None:
            slot = SharedSlot(SharedMemory(name=memory_name), lock, options.width, options.height)
            acquire_frames(
                options,
                generation,
                backend,
                lambda: _stop_requested(stop),
                slot.publish,
                lambda fps: status.send(WorkerMessage("opened", fps=fps)),
                lambda: status.send(WorkerMessage("released")),
            )
    except Exception as failure:  # noqa: BLE001 -- sanitize the native driver boundary
        error = str(failure) if isinstance(failure, CaptureError) else "映像取得処理が失敗"
        with suppress(BrokenPipeError, EOFError, OSError):
            status.send(WorkerMessage("error", error=error))
    finally:
        if slot is not None:
            slot.close(owner=False)
        stop.close()
        status.close()
