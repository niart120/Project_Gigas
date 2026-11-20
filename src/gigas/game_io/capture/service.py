"""UI-independent capture requests, supervision, and immutable state snapshots."""

import logging
import math
import multiprocessing
import time
from contextlib import ExitStack
from dataclasses import dataclass, replace
from threading import Condition, Thread
from typing import TYPE_CHECKING, Literal

from gigas.game_io.capture._process import SharedSlot, receive_message, worker_main
from gigas.game_io.capture.backend import OpenCVBackend
from gigas.game_io.capture.config import PONKAN_SOURCE
from gigas.game_io.capture.frames import FrameStore
from gigas.game_io.capture.models import CaptureError

logger = logging.getLogger(__name__)

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
        devices: Latest catalog, including the currently streaming source.
        devices_revision: Zero before enumeration; increments for successful explicit
            lists and the enumeration used when connecting.
        frame: Latest valid immutable image, or None during stop and after failure.
        generation: Connection generation local to this service's lifetime.
        error: Displayable failure reason, empty when there is no reported failure.
        cleanup: Whether native release or forced process termination was confirmed.
        backend: Explicit OpenCV API identifier chosen by the current or last worker,
            or None before selection. Zero is reserved for injected test adapters.
        catalog_warning: Device-list failure or partial result from the latest search.
        catalog_searching: An independent search is running alongside capture.
        catalog_cleanup_unconfirmed: Search resources could not be released.
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
    catalog_warning: str = ""
    catalog_searching: bool = False
    catalog_cleanup_unconfirmed: bool = False


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


class _WorkerMonitor(Thread):
    """Supervise one device operation at a time, including bounded native cleanup."""

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
        self._closing = False
        self._cancel = False
        self._retained: list[object] = []

    def snapshot(self) -> CaptureSnapshot:
        """Return current state and the immutable latest image without device I/O."""
        with self._condition:
            return replace(self._state, frame=self._frames.latest())

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
            except Exception:
                logger.exception(
                    "Capture supervision failed",
                    extra={"event": "capture.failed", "operation": "_run_request"},
                )
                with self._condition:
                    self._frames.invalidate()
                    self._state = replace(
                        self._state, phase="error", error="映像取得プロセスの起動失敗"
                    )
                return
            logger.info(
                "Capture worker started",
                extra={
                    "event": "capture.worker_started",
                    "generation": generation,
                    "child_pid": session.process.pid,
                    "operation": "enumerate" if options is None else "connect",
                },
            )
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
                    if options is not None and options.device_path == PONKAN_SOURCE:
                        label = "ponkan"
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
        except Exception:
            logger.exception(
                "Capture supervision failed",
                extra={"event": "capture.failed", "operation": "_rollback"},
            )
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
                logger.exception(
                    "Capture supervision failed",
                    extra={"event": "capture.failed", "operation": "_messages"},
                )
                session.error = "映像取得プロセスの通知が不正"
                session.status_closed = True
                break
            with self._condition:
                if message.kind in {"devices", "ponkan"}:
                    devices = message.devices
                    if message.kind == "ponkan":
                        cameras = tuple(d for d in self._state.devices if d.path != PONKAN_SOURCE)
                        devices = (*cameras, *devices)
                    if session.slot is None:
                        session.listed = True
                    if not self._cancel:
                        self._state = replace(
                            self._state,
                            devices=devices,
                            catalog_warning=message.error,
                            devices_revision=self._state.devices_revision + 1,
                        )
                elif message.kind == "candidates":
                    session.candidates = message.candidates
                    session.backend = message.backend
                    self._state = replace(self._state, backend=message.backend)
                elif message.kind == "started":
                    session.started = True
                    logger.info(
                        "Capture connected",
                        extra={"event": "capture.connected", "child_pid": session.process.pid},
                    )
                elif message.kind == "released":
                    session.released = True
                elif message.kind == "error":
                    logger.error(
                        "Capture worker reported failure",
                        extra={
                            "event": "capture.worker_failed",
                            "child_pid": session.process.pid,
                            "error": message.error,
                            "details": message.details,
                        },
                    )
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
            except Exception:
                logger.exception(
                    "Capture supervision failed",
                    extra={"event": "capture.failed", "operation": "_monitor"},
                )
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
        except Exception:
            logger.exception(
                "Capture supervision failed",
                extra={"event": "capture.failed", "operation": "_finalize"},
            )
            session.error = "映像監視処理が失敗"
        join_failed = False
        try:
            session.process.join(0)
        except Exception:
            logger.exception(
                "Capture supervision failed",
                extra={"event": "capture.failed", "operation": "_finalize"},
            )
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
        child_pid, exit_code = session.process.pid, session.process.exitcode
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
            except Exception:
                logger.exception(
                    "Capture supervision failed",
                    extra={"event": "capture.failed", "operation": "_finalize"},
                )
                cleanup = "unconfirmed"
                session.error = "映像取得資源の解放未確認"
        if join_failed:
            cleanup = "unconfirmed"
        if cleanup == "unconfirmed":
            self._retained.append(session)
        session.cleanup = cleanup
        logger.log(
            logging.ERROR if session.error else logging.INFO,
            "Capture worker stopped",
            extra={
                "event": "capture.worker_stopped",
                "child_pid": child_pid,
                "exit_code": exit_code,
                "cleanup": cleanup,
                "error": session.error,
            },
        )


class _CatalogSearch(_WorkerMonitor):
    """Supervise one enumeration and deliver its result after worker cleanup."""

    def __init__(
        self,
        owner: "CaptureService",
        generation: int,
    ) -> None:
        """Prepare a one-shot search belonging to this connection generation."""
        super().__init__(backend_factory=owner._factory, timeouts=owner._timeouts)
        self.name = "gigas-catalog-monitor"
        self.owner = owner
        self.generation = generation

    def run(self) -> None:
        """Publish the completed enumeration to its owner."""
        try:
            self._run_request(None, self.generation)
        finally:
            self.owner._finish_search(self)


class CaptureService(_WorkerMonitor):
    """Own one capture session and allow one concurrent catalog search.

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
        """Prepare capture and discovery without opening devices."""
        super().__init__(backend_factory=backend_factory, timeouts=timeouts)
        self._pending: tuple[str, CaptureOptions | None] | None = None
        self._search: _CatalogSearch | None = None
        self._device_path = ""
        self._active_device: DeviceInfo | None = None

    def reader(self) -> CaptureReader:
        """Return a new read-only handle sharing this service's latest state.

        Readers do not start acquisition or consume frames from one another. Keep
        connection and shutdown ownership with this service; use separate readers
        for preview and observation processing.
        """
        return CaptureReader(self.snapshot)

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

    def connect(self, options: "CaptureOptions") -> bool:
        """Start capture when the previous capture worker has finished."""
        with self._condition:
            if not self._request(options):
                return False
            self._device_path = options.device_path
            self._active_device = None
            return True

    def refresh_devices(self) -> bool:
        """Search once, preserving a live capture session and its frame store."""
        with self._condition:
            if self._closing or self._search is not None or self._state.catalog_cleanup_unconfirmed:
                return False
            if self._state.phase != "streaming":
                return self._request(None)
            if self._active_device is None:
                self._active_device = next(
                    (d for d in self._state.devices if d.path == self._device_path), None
                )
            search = _CatalogSearch(self, self._state.generation)
            self._search = search
            try:
                search.start()
            except RuntimeError:
                self._search = None
                self._state = replace(
                    self._state, catalog_warning="映像機器の検索を開始できませんでした"
                )
                logger.exception("Failed to start catalog monitor")
            return True

    def snapshot(self) -> CaptureSnapshot:
        """Include the active source in the catalog for the lifetime of its stream."""
        with self._condition:
            state = super().snapshot()
            if self._search is not None:
                state = replace(
                    state,
                    catalog_searching=True,
                    catalog_cleanup_unconfirmed=self._search.snapshot().cleanup == "unconfirmed",
                )
            active = self._active_device
            if (
                state.phase == "streaming"
                and active is not None
                and all(device.path != active.path for device in state.devices)
            ):
                state = replace(state, devices=(*state.devices, active))
            return state

    def _finish_search(self, search: _CatalogSearch) -> None:
        result = search.snapshot()
        with self._condition:
            if result.cleanup == "unconfirmed":
                self._retained.append(search)
                self._state = replace(self._state, catalog_cleanup_unconfirmed=True)
            if self._state.phase == "streaming" and self._state.generation == search.generation:
                if result.devices_revision and not result.error:
                    self._state = replace(
                        self._state,
                        devices=result.devices,
                        devices_revision=self._state.devices_revision + 1,
                    )
                self._state = replace(
                    self._state, catalog_warning=result.error or result.catalog_warning
                )
            self._search = None

    def disconnect(self) -> None:
        """Cancel any outstanding search and release the capture worker."""
        with self._condition:
            if self._search is not None:
                self._search.shutdown()
            super().disconnect()

    def shutdown(self) -> None:
        """Request shutdown of both workers without waiting for native I/O."""
        with self._condition:
            if self._search is not None:
                self._search.shutdown()
            super().shutdown()

    def _serve(self) -> None:
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

    def run(self) -> None:
        """Serve requests and finish both workers before reporting shutdown."""
        try:
            self._serve()
        finally:
            with self._condition:
                search = self._search
            if search is not None:
                search.shutdown()
                search.join()
