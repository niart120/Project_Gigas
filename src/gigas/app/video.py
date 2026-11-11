"""Coordinate capture state and bounded preview updates on the GUI thread."""

import math
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Literal, Protocol

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from gigas.game_io.capture import CaptureService
from gigas.game_io.capture.models import CaptureError, CaptureOptions
from gigas.ui.notifications import Notice
from gigas.ui.video_panel import VideoPanel

if TYPE_CHECKING:
    from gigas.game_io.capture import CaptureReader, CaptureSnapshot
    from gigas.game_io.capture.models import DeviceInfo
    from gigas.storage.settings import Settings


class CapturePort(Protocol):
    """A nonblocking capture service owned through application shutdown."""

    def start(self) -> None:
        """Start the service without opening any device."""
        ...

    def refresh_devices(self) -> bool:
        """Accept one asynchronous enumeration when idle."""
        ...

    def connect(self, options: CaptureOptions) -> bool:
        """Accept one asynchronous connection using the supplied mode."""
        ...

    def disconnect(self) -> None:
        """Invalidate video immediately and request device cleanup."""
        ...

    def shutdown(self) -> None:
        """Request device cleanup and service termination."""
        ...

    def reader(self) -> "CaptureReader":
        """Return a read-only port without opening another device."""
        ...

    def is_alive(self) -> bool:
        """Report whether the owned service thread still runs."""
        ...

    def join(self, timeout: float | None = None) -> None:
        """Check termination with a bounded timeout."""
        ...


class VideoController(QObject):
    """Pull capture state and the latest image without queuing every frame.

    The failed signal carries a Notice once per observed failure in an operation.
    A new explicit attempt may notify the same diagnostic again. Notification
    history does not participate in acquisition availability or cleanup decisions.

    Args:
        panel: View receiving controls, current state, and display images.
        service: Unstarted service to own; None creates the production service.
        parent: Owning application object.
    """

    changed = Signal()
    devices_changed = Signal()
    failed = Signal(object)

    def __init__(
        self,
        panel: VideoPanel,
        service: CapturePort | None = None,
        parent: QObject | None = None,
    ) -> None:
        """Prepare polling without starting the service or accessing devices."""
        super().__init__(parent)
        self.panel = panel
        self.service: CapturePort = CaptureService() if service is None else service
        self.active_settings: Settings | None = None
        self.reader = self.service.reader()
        self._snapshot = self.reader.snapshot()
        self._available = True
        self._settings_ready = False
        self._commands_ready = True
        self._pending_connection = False
        self._attempt = 0
        self._attempt_name = "映像取得"
        self._last_failure: tuple[int, int, str] | None = None
        self._started = False
        self._closing = False
        self._disconnecting = False
        self._request_error = ""
        self._revision = 0
        self._enumerating = False
        self._discovery_error = ""
        self.apply_state: Literal["idle", "stopping", "connecting", "complete", "failed"] = "idle"
        self._replacement: Settings | None = None
        self._replacement_generation = 0
        self._refresh_after_disconnect = False
        self._next_image = 0.0
        self._preview_fps = 30
        self._last_state: tuple[str, str, str, bool, str, str] | None = None
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(34)
        self.timer.timeout.connect(self._poll)

    @property
    def applying(self) -> bool:
        """Whether a replacement awaits old cleanup or the new session's first frame."""
        return self.apply_state in {"stopping", "connecting"}

    @property
    def apply_error(self) -> str:
        """Return a local validation, cleanup, or connection failure."""
        return self._request_error

    @property
    def cleanup_unconfirmed(self) -> bool:
        """Whether the current worker or its resources lack confirmed cleanup."""
        return self._snapshot.cleanup == "unconfirmed"

    @property
    def has_connection(self) -> bool:
        """Whether a saved acquisition configuration still owns an active session."""
        return self.active_settings is not None and self._snapshot.phase in {
            "connecting",
            "streaming",
            "stopping",
        }

    @property
    def can_connect(self) -> bool:
        """Whether an explicit connection can begin without overlapping other work."""
        return self.can_refresh and self._settings_ready and not self._refresh_after_disconnect

    @property
    def can_disconnect(self) -> bool:
        """Keep cancellation available during saving and replacement cleanup."""
        return (
            self._available
            and not self._closing
            and self.service.is_alive()
            and (
                self._snapshot.phase in {"connecting", "streaming", "enumerating"}
                or self.applying
                or self._pending_connection
            )
        )

    @property
    def devices(self) -> "tuple[DeviceInfo, ...]":
        """Return the most recent successful enumeration for an opened dialog."""
        return self._snapshot.devices

    @property
    def devices_checked(self) -> bool:
        """Whether any successful enumeration has supplied the current catalog."""
        return self._snapshot.devices_revision > 0

    @property
    def discovering(self) -> bool:
        """Whether an explicit catalog request is still awaiting worker cleanup."""
        return self._enumerating

    @property
    def discovery_error(self) -> str:
        """Return the last catalog failure without discarding earlier identities."""
        return self._discovery_error

    @property
    def can_refresh(self) -> bool:
        """Whether application lifetime and service state permit enumeration."""
        return (
            self._available
            and self._commands_ready
            and not self.applying
            and not self._closing
            and self.service.is_alive()
            and self._snapshot.phase in {"idle", "error"}
            and self._snapshot.cleanup != "unconfirmed"
        )

    @property
    def shutdown_error(self) -> str:
        """Return the reason shutdown cannot yet be treated as complete."""
        snapshot = self.reader.snapshot()
        if snapshot.cleanup == "unconfirmed":
            return f"映像終了未確認 · {snapshot.error}" if snapshot.error else "映像終了未確認"
        return ""

    @property
    def shutdown_complete(self) -> bool:
        """Require both service termination and confirmed device cleanup."""
        if not self._closing or self.service.is_alive() or self.shutdown_error:
            return False
        if self._started:
            self.service.join(0)
        self.timer.stop()
        return True

    def start(self) -> None:
        """Start idle monitoring without automatic enumeration or connection."""
        self.service.start()
        self._started = True
        self.timer.start()
        self._poll()

    def set_available(
        self,
        available: bool,
        *,
        settings_ready: bool,
        commands_ready: bool = True,
        pending_connection: bool = False,
    ) -> None:
        """Keep active-session controls independent of settings read failures.

        Args:
            available: Whether application lifetime permits capture commands.
            settings_ready: Whether a successfully loaded snapshot permits connecting.
            commands_ready: Whether settings and application transitions permit discovery.
            pending_connection: Whether a save may start video and needs a cancel action.
        """
        self._available = available
        self._settings_ready = settings_ready
        self._commands_ready = commands_ready
        self._pending_connection = pending_connection
        self._render()

    def refresh_devices(self) -> None:
        """Request enumeration when idle without changing pending settings or video."""
        if self.can_refresh and self.service.refresh_devices():
            self._begin_attempt("機器確認")
            self._enumerating = True
            self._discovery_error = ""
            self._request_error = ""
            self._poll()

    def connect_capture(self, settings: "Settings") -> None:
        """Connect using a saved snapshot, retaining its mode until reconnect.

        Args:
            settings: Last successfully loaded or saved application settings.
        """
        if not self.can_connect:
            return
        self._begin_attempt("映像接続")
        if not settings.capture_device:
            self._fail_application("映像機器が未選択")
            self._poll()
            return
        self._replacement = settings
        self._start_replacement()
        self._poll()

    def apply_capture(self, settings: "Settings") -> None:
        """Apply a successfully saved acquisition configuration after confirmed cleanup.

        Args:
            settings: Saved snapshot to connect, or an empty device to remain disconnected.
        """
        self._begin_attempt("映像適用")
        self._refresh_after_disconnect = False
        self._replacement = settings
        self._request_error = ""
        snapshot = self.reader.snapshot()
        if not self._available or self._closing or not self.service.is_alive():
            self._fail_application("映像サービスを利用できない")
        elif snapshot.cleanup == "unconfirmed":
            self._fail_application("旧映像の終了を確認できない")
        elif snapshot.phase in {"idle", "error"}:
            self._start_replacement()
        else:
            self.apply_state = "stopping"
            self._enumerating = False
            self._disconnecting = True
            self.panel.view.set_frame(None)
            self.service.disconnect()
        self._poll()

    def cancel_pending(self) -> None:
        """Discard unstarted replacement and discovery intents without restarting video."""
        self._replacement = None
        self._refresh_after_disconnect = False
        self.apply_state = "idle"

    def _fail_application(self, error: str) -> None:
        self._replacement = None
        self.apply_state = "failed"
        self._request_error = error

    def _start_replacement(self) -> None:
        settings = self._replacement
        if settings is None:
            return
        if not settings.capture_device:
            self._replacement = None
            self.apply_state = "complete"
            self.active_settings = None
            return
        try:
            options = CaptureOptions(settings.capture_device, fps=settings.capture_fps)
        except CaptureError as error:
            self._fail_application(str(error))
            return
        if self.service.connect(options):
            self.active_settings = settings
            self.apply_state = "connecting"
            self._replacement_generation = self.reader.snapshot().generation
            self._request_error = ""
            self._disconnecting = False
            self._next_image = 0.0
            self.panel.view.set_frame(None)
            self.set_preview_fps(settings.preview_fps)
        else:
            self._fail_application("映像接続の開始要求が受け付けられなかった")

    def set_preview_fps(self, fps: int) -> None:
        """Apply a saved display limit without changing the capture connection.

        Args:
            fps: Validated settings value from 1 through 60 frames per second.
        """
        if fps != self._preview_fps:
            self._preview_fps = fps
            self._next_image = 0.0
            self.timer.setInterval(min(34, math.ceil(1000 / fps)))

    def disconnect_capture(self, *, refresh_devices: bool = False) -> None:
        """Clear video and request cleanup, optionally discovering once afterward.

        Args:
            refresh_devices: Refresh the settings catalog after confirmed cleanup.
        """
        if self.reader.snapshot().phase not in {"idle", "error", "closed"}:
            self._begin_attempt("映像切断")
        self.cancel_pending()
        self._refresh_after_disconnect = refresh_devices
        self._enumerating = False
        self._disconnecting = True
        self.panel.view.set_frame(None)
        self.service.disconnect()
        self._poll()

    def shutdown(self) -> None:
        """Clear video and request service shutdown without joining on the GUI."""
        self._begin_attempt("映像終了")
        self.cancel_pending()
        self._closing = True
        self.panel.view.set_frame(None)
        self.service.shutdown()
        self._poll()

    def _poll(self) -> None:
        snapshot = self.reader.snapshot()
        alive = self.service.is_alive()
        if self._disconnecting:
            if snapshot.phase in {"idle", "error", "closed"}:
                self._disconnecting = False
            else:
                snapshot = replace(snapshot, phase="stopping", frame=None)
        if self._started and not alive and not self._closing:
            snapshot = replace(snapshot, phase="error", frame=None, error="映像サービス終了")
        self._snapshot = snapshot
        if self._advance_application(snapshot, alive):
            snapshot = self.reader.snapshot()
            self._snapshot = snapshot
        enumerating = self._enumerating
        if self._enumerating:
            self._discovery_error = snapshot.error
            if snapshot.phase in {"idle", "error", "closed"} or snapshot.cleanup == "unconfirmed":
                self._enumerating = False
        if snapshot.devices_revision != self._revision:
            self._revision = snapshot.devices_revision
            if not enumerating:
                self._discovery_error = ""
            self.devices_changed.emit()
        self._notify_failure(snapshot, alive=alive)
        self._render()
        if self._closing or snapshot.phase != "streaming" or snapshot.frame is None:
            self.panel.view.set_frame(None)
        elif self.active_settings is not None and (now := time.perf_counter()) >= self._next_image:
            self.panel.view.set_frame(snapshot.frame)
            self._next_image = now + 1 / self._preview_fps
        state = (
            snapshot.phase,
            snapshot.error,
            snapshot.cleanup,
            alive,
            self.apply_state,
            self._request_error,
        )
        if state != self._last_state:
            self._last_state = state
            self.changed.emit()

    def _advance_application(self, snapshot: "CaptureSnapshot", alive: bool) -> bool:
        if self.applying:
            if not alive or snapshot.cleanup == "unconfirmed":
                self._fail_application(snapshot.error or "旧映像の終了を確認できない")
            elif snapshot.phase in {"error", "closed"}:
                self._fail_application(snapshot.error or "映像接続が終了した")
            elif self.apply_state == "stopping" and snapshot.phase == "idle":
                if snapshot.cleanup not in {"graceful", "forced"}:
                    self._fail_application("旧映像の終了を確認できない")
                else:
                    self._start_replacement()
                    return True
            elif (
                self.apply_state == "connecting"
                and snapshot.phase == "streaming"
                and snapshot.frame is not None
                and snapshot.frame.generation == self._replacement_generation
            ):
                self._replacement = None
                self.apply_state = "complete"
                self._attempt_name = "映像取得"
        if self._refresh_after_disconnect:
            if (
                snapshot.cleanup == "unconfirmed"
                or snapshot.phase in {"error", "closed"}
                or not alive
            ):
                self._refresh_after_disconnect = False
            elif snapshot.phase == "idle" and self.can_refresh:
                self._refresh_after_disconnect = False
                if snapshot.cleanup in {"graceful", "forced"} and self.service.refresh_devices():
                    self._begin_attempt("機器確認")
                    self._enumerating = True
                    self._discovery_error = ""
                    return True
        return False

    def _begin_attempt(self, name: str) -> None:
        self._attempt += 1
        self._attempt_name = name
        self._request_error = ""

    def _notify_failure(self, snapshot: "CaptureSnapshot", *, alive: bool) -> None:
        detail = self._request_error
        service_exited = self._started and not alive and not self._closing
        if service_exited:
            detail = snapshot.error
        elif snapshot.cleanup == "unconfirmed":
            detail = snapshot.error or detail or "映像機器の終了を確認できない"
        elif not detail and snapshot.phase == "error":
            detail = snapshot.error
        key = (self._attempt, snapshot.generation, detail)
        if not detail or key == self._last_failure:
            return
        self._last_failure = key
        title = f"{self._attempt_name}失敗"
        explanation = "映像の処理に失敗しました。設定で機器と接続状態を確認してください。"
        if service_exited:
            title = "映像サービス終了"
            explanation = "映像サービスが終了しました。詳細を確認してください。"
        elif snapshot.cleanup == "unconfirmed":
            explanation = (
                "映像機器の終了を確認できません。新しい接続は開始できません。"
                "詳細を確認してください。"
            )
        elif self._attempt_name == "映像適用":
            explanation = (
                "設定は保存されましたが、映像への適用に失敗しました。"
                "設定で機器と接続状態を確認してください。"
            )
        self.failed.emit(Notice("video", title, explanation, detail))

    def _render(self) -> None:
        self.panel.render_state(
            self._snapshot,
            self.active_settings,
            available=self._available and not self._closing and self.service.is_alive(),
            settings_ready=self.can_connect,
        )
        self.panel.disconnect_action.setEnabled(self.can_disconnect)
