"""UI-independent capture requests, supervision, and immutable state snapshots."""

import math
import multiprocessing
import time
from contextlib import ExitStack
from dataclasses import dataclass, replace
from threading import Condition, Thread
from typing import TYPE_CHECKING, Literal

from gigas.game_io.capture._process import SharedSlot, receive_message, worker_main
from gigas.game_io.capture.backend import OpenCVBackend
from gigas.game_io.capture.frames import FrameStore
from gigas.game_io.capture.models import CaptureError

if TYPE_CHECKING:
    from collections.abc import Callable
    from multiprocessing.process import BaseProcess

    from gigas.game_io.capture._process import Channel
    from gigas.game_io.capture.backend import CaptureBackend
    from gigas.game_io.capture.config import CaptureOptions
    from gigas.game_io.capture.models import DeviceInfo, Frame

__all__ = ["CaptureReader", "CaptureService", "CaptureSnapshot", "CaptureTimeouts"]


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
        devices_revision: Zero before enumeration; increments for successful explicit
            lists and the enumeration used when connecting.
        frame: Latest valid immutable image, or None during stop and after failure.
        generation: Connection generation local to this service's lifetime.
        error: Displayable failure reason, empty when there is no reported failure.
        cleanup: Whether native release or forced process termination was confirmed.
        backend: Explicit OpenCV API identifier chosen by the current or last worker,
            or None before selection. Zero is reserved for injected test adapters.
        diagnostics: Bounded attempt failure reasons in order, retained after a
            successful fallback and reset on the next request. Intermediate failures
            do not populate error; each request tries at most two APIs.
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
    backend: int | None = None
    diagnostics: tuple[str, ...] = ()


class CaptureReader:
    """Read shared capture state without owning connection or shutdown commands.

    Obtain a reader from CaptureService.reader. Creating or discarding readers does
    not open or close the device. Processing a returned image holds no capture lock.

    Args:
        snapshot: The owning service's host-side snapshot callback.
    """

    __slots__ = ("_read",)

    def __init__(self, snapshot: "Callable[[], CaptureSnapshot]") -> None:
        """Bind the shared state source without performing device I/O."""
        self._read = snapshot

    def snapshot(self) -> CaptureSnapshot:
        """Return current immutable state and image, releasing locks before return.

        Returns:
            State with the latest valid frame or None after stop or failure. An older
            snapshot retained by this reader stays unchanged and does not hold up
            acquisition or other readers; consumers must recheck state for freshness.
        """
        return self._read()


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
    status_closed: bool = False
    stop_at: float = 0.0
    forced_at: float | None = None
    unconfirmed: bool = False
    candidates: tuple[int, ...] = ()
    backend: int | None = None
    started: bool = False
    normal_exit: bool = False
    cleanup: Literal["none", "graceful", "forced", "unconfirmed"] = "none"


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
        self._retained: list[object] = []

    def snapshot(self) -> CaptureSnapshot:
        """Return current state and the immutable latest image without device I/O."""
        with self._condition:
            return replace(self._state, frame=self._frames.latest())

    def reader(self) -> CaptureReader:
        """Return a new read-only handle sharing this service's latest state.

        Readers do not start acquisition or consume frames from one another. Keep
        connection and shutdown ownership with this service; use separate readers
        for preview and observation processing.
        """
        return CaptureReader(self.snapshot)

    def refresh_devices(self) -> bool:
        """Start enumeration unless busy, shutting down, or cleanup is unconfirmed."""
        return self._request(None)

    def connect(self, options: "CaptureOptions") -> bool:
        """Accept a connection only when idle with no unconfirmed previous cleanup."""
        return self._request(options)

    def _request(self, options: "CaptureOptions | None") -> bool:
        with self._condition:
            if (
                self._closing
                or self._state.phase not in ("idle", "error")
                or self._state.cleanup == "unconfirmed"
            ):
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
                backend=None,
                diagnostics=(),
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
                while not self._closing:
                    if self._pending is not None:
                        _operation, options = self._pending
                        self._pending = None
                        generation = self._state.generation
                        break
                    self._condition.wait()
                else:
                    self._state = replace(self._state, phase="closed")
                    return
            self._run_request(options, generation)

    def _run_request(self, options: "CaptureOptions | None", generation: int) -> None:
        started = time.monotonic()
        preference: int | None = None
        allowed: tuple[int, ...] = ()
        attempted: set[int] = set()
        last_cleanup = self._state.cleanup
        for attempt in range(2):
            with self._condition:
                if self._cancel or self._closing:
                    self._state = replace(self._state, phase="idle", cleanup=last_cleanup)
                    return
            try:
                session = (
                    self._spawn(options, generation)
                    if preference is None
                    else self._spawn(options, generation, preference)
                )
            except Exception:  # noqa: BLE001 -- thread boundary reports startup failures
                with self._condition:
                    self._frames.invalidate()
                    self._state = replace(
                        self._state, phase="error", error="映像取得プロセスの起動失敗"
                    )
                return
            self._monitor(session, generation, started)
            last_cleanup = session.cleanup
            with self._condition:
                if attempt == 0:
                    allowed = session.candidates
                if session.backend is not None:
                    attempted.add(session.backend)
                diagnostics = self._state.diagnostics
                if session.error:
                    label = str(session.backend) if session.backend is not None else "未選択"
                    diagnostics += (f"取得方式 {label}: {session.error}",)
                remaining = tuple(api for api in allowed if api not in attempted)
                retry = (
                    options is not None
                    and attempt == 0
                    and bool(remaining)
                    and bool(session.error)
                    and not session.started
                    and session.released
                    and session.normal_exit
                    and session.cleanup == "graceful"
                    and not self._cancel
                    and not self._closing
                )
                if retry and time.monotonic() - started >= self._timeouts.startup:
                    retry = False
                    session.error = "映像取得の開始期限切れ"
                    diagnostics += (session.error,)
                if retry:
                    preference = remaining[0]
                    self._frames.begin(generation)
                    self._state = replace(
                        self._state,
                        phase="connecting",
                        error="",
                        cleanup="none",
                        diagnostics=diagnostics,
                    )
                else:
                    self._state = replace(
                        self._state,
                        phase="error" if session.error else "idle",
                        error=session.error,
                        cleanup=session.cleanup,
                        diagnostics=diagnostics,
                    )
                    return

    def _spawn(
        self, options: "CaptureOptions | None", generation: int, api_preference: int | None = None
    ) -> _Session:
        context = multiprocessing.get_context("spawn")
        with ExitStack() as rollback:
            slot = SharedSlot.create(context, options) if options is not None else None
            if slot is not None:
                rollback.callback(self._rollback, slot, lambda: slot.close(owner=True))
            stop_reader, stop_writer = context.Pipe(duplex=False)
            rollback.callback(self._rollback, stop_reader, stop_reader.close)
            rollback.callback(self._rollback, stop_writer, stop_writer.close)
            status_reader, status_writer = context.Pipe(duplex=False)
            rollback.callback(self._rollback, status_reader, status_reader.close)
            rollback.callback(self._rollback, status_writer, status_writer.close)
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
                )
                + (() if api_preference is None else (api_preference,)),
                name=f"gigas-capture-{generation}",
                daemon=True,
            )
            rollback.callback(self._rollback, process, lambda: self._rollback_process(process))
            process.start()
            stop_reader.close()
            status_writer.close()
            rollback.pop_all()
        return _Session(process, status_reader, stop_writer, slot)

    def _rollback(self, resource: object, close: "Callable[[], None]") -> None:
        try:
            close()
        except Exception:  # noqa: BLE001 -- keep ownership when partial startup cleanup fails
            self._retained.append(resource)
            with self._condition:
                self._state = replace(self._state, cleanup="unconfirmed")

    def _rollback_process(self, process: "BaseProcess") -> None:
        if process.is_alive():
            process.terminate()
            process.join(self._timeouts.terminate)
        process.close()

    def _messages(self, session: _Session) -> None:
        if session.status_closed:
            return
        for _ in range(8):
            try:
                if not session.status.poll(0):
                    break
                message = receive_message(session.status)
            except (EOFError, OSError):
                session.status_closed = True
                with self._condition:
                    if (
                        not self._cancel
                        and not session.stop_sent
                        and not session.released
                        and not session.listed
                        and not session.error
                    ):
                        session.error = "映像取得プロセスの通信終了"
                break
            except (CaptureError, ValueError):
                session.error = "映像取得プロセスの通知が不正"
                session.status_closed = True
                break
            with self._condition:
                if message.kind == "devices":
                    if session.slot is None:
                        session.listed = True
                    if not self._cancel:
                        self._state = replace(
                            self._state,
                            devices=message.devices,
                            devices_revision=self._state.devices_revision + 1,
                        )
                elif message.kind == "candidates":
                    session.candidates = message.candidates
                    session.backend = message.backend
                    self._state = replace(self._state, backend=message.backend)
                elif message.kind == "started":
                    session.started = True
                elif message.kind == "released":
                    session.released = True
                elif message.kind == "error":
                    session.error = session.error or message.error
                    self._frames.invalidate()

    def _monitor(self, session: _Session, generation: int, started: float) -> None:
        sequence = 0
        progress = time.monotonic()
        while session.process.is_alive():
            try:
                self._messages(session)
                with self._condition:
                    cancel = self._cancel
                if session.slot is not None and not cancel and not session.error:
                    frame = session.slot.read(generation, sequence)
                    if frame is not None:
                        now = time.monotonic()
                        if sequence == 0 and now - started >= self._timeouts.startup:
                            session.error = "映像取得の開始期限切れ"
                        else:
                            sequence = frame.sequence
                            session.started = True
                            progress = now
                            self._publish(frame)
            except Exception:  # noqa: BLE001 -- monitor failures must still stop/reap the worker
                session.error = "映像監視処理が失敗"
            self._deadlines(session, started, progress, sequence)
            with self._condition:
                self._condition.wait(timeout=0.01)
        self._finalize(session)

    def _publish(self, frame: "Frame") -> None:
        with self._condition:
            if self._frames.publish(frame):
                self._state = replace(self._state, phase="streaming")

    def _deadlines(self, session: _Session, started: float, progress: float, sequence: int) -> None:
        now = time.monotonic()
        with self._condition:
            cancel = self._cancel
        if not session.stop_sent:
            if not cancel and not session.error:
                if sequence == 0 and not session.listed and now - started >= self._timeouts.startup:
                    session.error = "映像取得の開始期限切れ"
                elif sequence > 0 and now - progress >= self._timeouts.frame:
                    session.error = "映像フレーム待機期限切れ"
            if cancel or session.error or session.listed or session.released:
                session.stop_sent = True
                session.stop_at = now
                try:
                    session.stop.close()
                except OSError:
                    session.error = "映像停止通知に失敗"
                with self._condition:
                    self._frames.invalidate()
                    self._state = replace(self._state, phase="stopping")
        elif session.forced_at is None and now - session.stop_at >= self._timeouts.stop:
            session.forced_at = now
            try:
                session.process.terminate()
            except OSError:
                session.unconfirmed = True
        elif session.forced_at is not None and now - session.forced_at >= self._timeouts.terminate:
            session.unconfirmed = True
        if session.unconfirmed:
            with self._condition:
                self._state = replace(
                    self._state,
                    phase="stopping",
                    cleanup="unconfirmed",
                    error="映像取得プロセスの終了未確認",
                )

    def _finalize(self, session: _Session) -> None:
        try:
            self._messages(session)
        except Exception:  # noqa: BLE001 -- final notification cannot skip resource cleanup
            session.error = "映像監視処理が失敗"
        join_failed = False
        try:
            session.process.join(0)
        except Exception:  # noqa: BLE001 -- continue releasing handles after a join failure
            join_failed = True
            session.error = "映像取得プロセスの終了確認失敗"
        with self._condition:
            if not self._cancel and not session.error and not session.listed:
                session.error = "映像取得プロセスが終了"
            self._frames.invalidate()
        cleanup: Literal["none", "graceful", "forced", "unconfirmed"] = "none"
        session.normal_exit = session.process.exitcode == 0
        if not session.normal_exit and session.forced_at is None and not self._cancel:
            session.error = (
                f"{session.error} / 取得子プロセスの異常終了 ({session.process.exitcode})"
            )
        if session.forced_at is not None:
            cleanup = "forced"
        elif session.released or session.listed:
            cleanup = "graceful"
        closers: list[Callable[[], None]] = [
            session.stop.close,
            session.status.close,
            session.process.close,
        ]
        slot = session.slot
        if slot is not None:
            closers.append(lambda: slot.close(owner=True))
        for close in closers:
            try:
                close()
            except Exception:  # noqa: BLE001 -- report unreleased host resources
                cleanup = "unconfirmed"
                session.error = "映像取得資源の解放未確認"
        if join_failed:
            cleanup = "unconfirmed"
        if cleanup == "unconfirmed":
            self._retained.append(session)
        session.cleanup = cleanup
