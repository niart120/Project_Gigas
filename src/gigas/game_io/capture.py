"""UI-independent capture requests, supervision, and immutable state snapshots."""

import math
import multiprocessing
from dataclasses import dataclass, replace
from threading import Condition, Thread
from typing import TYPE_CHECKING, Literal

from gigas.game_io._capture_process import SharedSlot, WorkerMessage, worker_main
from gigas.game_io.backend import OpenCVBackend
from gigas.game_io.frames import FrameStore
from gigas.game_io.models import CaptureError

if TYPE_CHECKING:
    from collections.abc import Callable
    from multiprocessing.process import BaseProcess

    from gigas.game_io._capture_process import Channel
    from gigas.game_io.backend import CaptureBackend
    from gigas.game_io.models import CaptureOptions, DeviceInfo, Frame

__all__ = ["CaptureService", "CaptureSnapshot", "CaptureTimeouts"]


@dataclass(frozen=True, slots=True)
class CaptureTimeouts:
    """Finite worker deadlines in seconds, configurable for deterministic tests."""

    startup: float = 10.0
    frame: float = 3.0
    stop: float = 2.0
    terminate: float = 2.0

    def __post_init__(self) -> None:
        """Reject disabled or non-finite deadlines."""
        if any(
            not math.isfinite(value) or value <= 0
            for value in (
                self.startup,
                self.frame,
                self.stop,
                self.terminate,
            )
        ):
            message = "映像取得の待機期限は正の有限値が必要"
            raise CaptureError(message)


@dataclass(frozen=True, slots=True)
class CaptureSnapshot:
    """Current host state; holding a snapshot never opens another device.

    Attributes:
        phase: Current connection or device-list operation.
        devices: Last successfully enumerated selectable identities.
        devices_revision: Zero before enumeration, incremented after each successful list.
        frame: Latest valid immutable image, or None during stop and after failure.
        generation: Connection generation local to this service's lifetime.
        error: Displayable failure reason, empty when there is no reported failure.
        cleanup: Whether native release or forced process termination was confirmed.
        reported_fps: Device-reported nominal rate, independent of measured acquisition.
        measured_fps: Acquisition rate from frame sequence and monotonic timestamps.
    """

    phase: Literal[
        "idle", "enumerating", "connecting", "streaming", "stopping", "error", "closed"
    ] = "idle"
    devices: "tuple[DeviceInfo, ...]" = ()
    devices_revision: int = 0
    frame: "Frame | None" = None
    generation: int = 0
    error: str = ""
    cleanup: Literal["none", "graceful", "forced", "unconfirmed"] = "none"
    reported_fps: float | None = None
    measured_fps: float | None = None


@dataclass(slots=True)
class _Session:
    process: "BaseProcess"
    status: "Channel"
    stop: "Channel"
    slot: SharedSlot | None
    released: bool = False
    listed: bool = False
    error: str = ""
    stop_sent: bool = False


class CaptureService(Thread):
    """Own a monitor thread and at most one spawned device worker.

    Construction creates no threads or device handles. Call start once, then submit
    requests from any host thread. All request methods return without waiting for a
    device. After shutdown, use is_alive and join(0) to observe monitor completion.

    Args:
        backend_factory: Picklable factory instantiated in the child; tests can replace
            the device boundary while exercising the actual process transport.
        timeouts: Startup, missing-frame, graceful-stop, and force-exit deadlines.
    """

    def __init__(
        self,
        *,
        backend_factory: "Callable[[], CaptureBackend]" = OpenCVBackend,
        timeouts: CaptureTimeouts | None = None,
    ) -> None:
        """Prepare a disconnected service without spawning its worker."""
        super().__init__(name="gigas-capture-monitor", daemon=True)
        self._factory = backend_factory
        self._timeouts = CaptureTimeouts() if timeouts is None else timeouts
        self._condition = Condition()
        self._frames = FrameStore()
        self._state = CaptureSnapshot()
        self._pending: tuple[str, CaptureOptions | None] | None = None
        self._closing = False
        self._cancel = False

    def snapshot(self) -> CaptureSnapshot:
        """Return current state and the immutable latest image without device I/O."""
        with self._condition:
            return replace(self._state, frame=self._frames.latest())

    def refresh_devices(self) -> bool:
        """Start enumeration if idle; return false while busy or shutting down."""
        return self._request(None)

    def connect(self, options: "CaptureOptions") -> bool:
        """Start one connection with validated settings, returning request acceptance."""
        return self._request(options)

    def _request(self, options: "CaptureOptions | None") -> bool:
        with self._condition:
            if self._closing or self._state.phase not in ("idle", "error"):
                return False
            generation = self._state.generation + (options is not None)
            self._cancel = False
            self._pending = ("enumerate" if options is None else "capture", options)
            self._frames.begin(generation)
            self._state = replace(
                self._state,
                phase="enumerating" if options is None else "connecting",
                generation=generation,
                error="",
                cleanup="none",
                reported_fps=None,
                measured_fps=None,
            )
            self._condition.notify_all()
            return True

    def disconnect(self) -> None:
        """Invalidate the image immediately and asynchronously release the current worker."""
        with self._condition:
            self._cancel = True
            self._frames.invalidate()
            if self._state.phase not in ("idle", "error", "closed"):
                self._state = replace(self._state, phase="stopping")
            self._condition.notify_all()

    def shutdown(self) -> None:
        """Reject new work and request release, without waiting on the GUI thread."""
        with self._condition:
            if self._closing:
                return
            self._closing = True
            self._cancel = True
            self._frames.invalidate()
            self._state = replace(self._state, phase="stopping")
            self._condition.notify_all()

    def run(self) -> None:
        """Serve requests until shutdown, confining all device calls to child processes."""
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closing or self._pending is not None)
                if self._closing:
                    self._state = replace(self._state, phase="closed")
                    return
                pending, self._pending = self._pending, None
                if pending is None:
                    continue
                _operation, options = pending
                generation = self._state.generation
            session = self._spawn(options, generation)
            self._monitor(session, generation)

    def _spawn(self, options: "CaptureOptions | None", generation: int) -> _Session:
        context = multiprocessing.get_context("spawn")
        slot = SharedSlot.create(context, options) if options is not None else None
        stop_reader, stop_writer = context.Pipe(duplex=False)
        status_reader, status_writer = context.Pipe(duplex=False)
        process = context.Process(
            target=worker_main,
            args=(
                options,
                generation,
                slot.memory.name if slot else None,
                slot.lock if slot else None,
                stop_reader,
                status_writer,
                self._factory,
            ),
            name=f"gigas-capture-{generation}",
            daemon=True,
        )
        try:
            process.start()
        except BaseException:
            stop_writer.close()
            status_reader.close()
            if slot is not None:
                slot.close(owner=True)
            raise
        finally:
            stop_reader.close()
            status_writer.close()
        return _Session(process, status_reader, stop_writer, slot)

    def _messages(self, session: _Session) -> None:
        while True:
            try:
                if not session.status.poll(0):
                    break
                message: object = session.status.recv()
            except (EOFError, OSError):
                break
            if not isinstance(message, WorkerMessage):
                session.error = "映像取得プロセスの通知が不正"
                continue
            with self._condition:
                if message.kind == "devices":
                    session.listed = True
                    if not self._cancel:
                        self._state = replace(
                            self._state,
                            devices=message.devices,
                            devices_revision=self._state.devices_revision + 1,
                        )
                elif message.kind == "opened":
                    self._state = replace(self._state, reported_fps=message.fps)
                elif message.kind == "released":
                    session.released = True
                elif message.kind == "error":
                    session.error = message.error
                    self._frames.invalidate()

    def _monitor(self, session: _Session, generation: int) -> None:
        sequence = 0
        first: Frame | None = None
        while session.process.is_alive():
            self._messages(session)
            with self._condition:
                cancel = self._cancel
            if (cancel or session.error) and not session.stop_sent:
                session.stop.close()
                session.stop_sent = True
            if session.slot is not None and not cancel and not session.error:
                frame = session.slot.read(generation, sequence)
                if frame is not None:
                    sequence = frame.sequence
                    if first is None:
                        first = frame
                    elapsed = (frame.monotonic_ns - first.monotonic_ns) / 1_000_000_000
                    rate = (frame.sequence - first.sequence) / elapsed if elapsed > 0 else None
                    with self._condition:
                        if self._frames.publish(frame):
                            self._state = replace(self._state, phase="streaming", measured_fps=rate)
            with self._condition:
                self._condition.wait(timeout=0.01)
        self._messages(session)
        session.process.join(0)
        with self._condition:
            if not self._cancel and not session.error and not session.listed:
                session.error = "映像取得プロセスが終了"
            self._frames.invalidate()
        session.stop.close()
        session.status.close()
        session.process.close()
        if session.slot is not None:
            session.slot.close(owner=True)
        with self._condition:
            self._state = replace(
                self._state,
                phase="error" if session.error else "idle",
                error=session.error,
                cleanup="graceful" if session.released or session.listed else "none",
            )
