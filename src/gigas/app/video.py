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

_CAPTURE_DIMENSIONS: dict[str, tuple[int | None, int | None]] = {
    "auto": (None, None),
    "hd": (1280, 720),
    "full_hd": (1920, 1080),
    "wqhd": (2560, 1440),
    "four_k": (3840, 2160),
}
_BACKEND_NAMES = {
    0: "テスト",
    200: "V4L2",
    700: "DirectShow / DSHOW",
    1200: "AVFoundation",
    1400: "Media Foundation / MSMF",
    1800: "GStreamer",
}


def _fps_text(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "未取得"
    return f"{value:.2f}".rstrip("0").rstrip(".")


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
        self.panel.info_action.triggered.connect(self.show_information)
        self.service: CapturePort = CaptureService() if service is None else service
        self.active_settings: Settings | None = None
        self.reader = self.service.reader()
        self._snapshot = self.reader.snapshot()
        self._available = True
        self._attempt = 0
        self._attempt_name = "映像取得"
        self._last_failure: tuple[int, int, str] | None = None
        self._started = False
        self._closing = False
        self._request_error = ""
        self._revision = 0
        self._enumerating = False
        self._discovery_error = ""
        self.apply_state: Literal["idle", "stopping", "connecting", "complete", "failed"] = "idle"
        self._replacement: Settings | None = None
        self._generation = 0
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
    def devices(self) -> "tuple[DeviceInfo, ...]":
        """Return the most recent successful enumeration for an opened dialog."""
        return self._snapshot.devices

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

    def show_information(self) -> None:
        """Display current diagnostics once, without capture commands or failure notices.

        Requests come from the configuration accepted for this connection, rather
        than unsaved edits. The modeless text remains fixed until explicitly reopened.
        """
        snapshot = self.reader.snapshot()
        if self._started and not self.service.is_alive() and not self._closing:
            snapshot = replace(snapshot, phase="error", frame=None, error="映像サービス終了")
        settings = self.active_settings
        requested = "未要求"
        fps = "未要求"
        if settings is not None:
            dimensions = _CAPTURE_DIMENSIONS.get(settings.capture_resolution)
            if dimensions is not None:
                requested = "自動" if dimensions[0] is None else f"{dimensions[0]}x{dimensions[1]}"
            fps = str(settings.capture_fps)
        frame = snapshot.frame
        actual = "未取得" if frame is None else f"{frame.width}x{frame.height}"
        backend = "未選択"
        if snapshot.backend is not None:
            backend = f"{_BACKEND_NAMES.get(snapshot.backend, 'API')} ({snapshot.backend})"
        rows = [
            f"状態: {snapshot.phase}",
            f"要求解像度: {requested}",
            f"要求FPS: {fps}",
            f"到着画像: {actual}",
            f"機器報告FPS: {_fps_text(snapshot.reported_fps)}",
            f"実測FPS: {_fps_text(snapshot.measured_fps)}",
            f"取得方式: {backend}",
            f"世代: {snapshot.generation}",
            f"連番: {frame.sequence if frame is not None else '未取得'}",
            f"終了確認: {snapshot.cleanup}",
        ]
        if snapshot.error:
            rows.append(f"取得エラー: {snapshot.error}")
        if self._request_error:
            rows.append(f"操作エラー: {self._request_error}")
        if snapshot.diagnostics:
            rows.extend(("試行診断:", *snapshot.diagnostics))
        self.panel.show_information("\n".join(rows))

    def set_available(self, available: bool) -> None:
        """Allow new capture commands until application shutdown is requested."""
        self._available = available

    def refresh_devices(self) -> None:
        """Request enumeration when idle without changing pending settings or video."""
        if self.can_refresh and self.service.refresh_devices():
            self._begin_attempt("機器確認")
            self._enumerating = True
            self._discovery_error = ""
            self._request_error = ""
            self._poll()

    def apply_capture(self, settings: "Settings") -> None:
        """Apply the latest saved target after releasing any previous resource.

        Args:
            settings: Saved configuration. An empty device cancels an unstarted
                replacement and releases capture. Matching capture conditions do
                not reopen a connecting or streaming device.
        """
        snapshot = self.reader.snapshot()
        self.set_preview_fps(settings.preview_fps)
        if not self._available or self._closing or not self.service.is_alive():
            self._begin_attempt("映像適用")
            self._fail_application("映像サービスを利用できない")
        elif snapshot.cleanup == "unconfirmed":
            self._begin_attempt("映像適用")
            self._fail_application("旧映像の終了を確認できない")
        elif self.apply_state == "stopping":
            self._replacement = settings
        elif (
            self.active_settings is not None
            and snapshot.phase in {"connecting", "streaming"}
            and self._capture_key(settings) == self._capture_key(self.active_settings)
        ):
            return
        else:
            self._begin_attempt("映像適用")
            self._replacement = settings
            if snapshot.phase in {"idle", "error"}:
                self._start_replacement()
            else:
                self.apply_state = "stopping"
                self._enumerating = False
                self.panel.view.set_frame(None)
                self.service.disconnect()
        self._poll()

    @staticmethod
    def _capture_key(settings: "Settings") -> tuple[str, int, str]:
        return settings.capture_device, settings.capture_fps, settings.capture_resolution

    def cancel_pending(self) -> None:
        """Discard an unstarted replacement without restarting video."""
        self._replacement = None
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
        dimensions = _CAPTURE_DIMENSIONS.get(settings.capture_resolution)
        if dimensions is None:
            self._fail_application("取得解像度が未対応")
            return
        try:
            options = CaptureOptions(settings.capture_device, *dimensions, fps=settings.capture_fps)
        except CaptureError as error:
            self._fail_application(str(error))
            return
        if self.service.connect(options):
            self._replacement = None
            self.active_settings = settings
            self.apply_state = "connecting"
            self._generation = self.reader.snapshot().generation
            self._request_error = ""
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
        if self.apply_state == "stopping" and snapshot.phase not in {"idle", "error", "closed"}:
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
        if (
            self._closing
            or snapshot.phase != "streaming"
            or snapshot.frame is None
            or snapshot.frame.generation != self._generation
        ):
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
                and snapshot.frame.generation == self._generation
            ):
                self.apply_state = "complete"
                self._attempt_name = "映像取得"
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
        )
