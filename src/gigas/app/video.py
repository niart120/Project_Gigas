"""Coordinate capture state and bounded preview updates on the GUI thread."""

import math
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from gigas.game_io.capture import CaptureService
from gigas.game_io.capture.models import CaptureError, CaptureOptions
from gigas.ui.video_panel import VideoPanel

if TYPE_CHECKING:
    from gigas.game_io.capture import CaptureReader
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

    Args:
        panel: View receiving controls, diagnostics, and display images.
        service: Unstarted service to own; None creates the production service.
        parent: Owning application object.
    """

    changed = Signal()
    devices_changed = Signal()

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
        self._started = False
        self._closing = False
        self._disconnecting = False
        self._request_error = ""
        self._revision = 0
        self._enumerating = False
        self._discovery_error = ""
        self._next_image = 0.0
        self._preview_fps = 30
        self._last_state: tuple[str, str, str, bool] | None = None
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(34)
        self.timer.timeout.connect(self._poll)
        self.panel.disconnect_requested.connect(self.disconnect_capture)

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

    def set_available(self, available: bool, *, settings_ready: bool) -> None:
        """Keep active-session controls independent of settings read failures.

        Args:
            available: Whether application lifetime permits capture commands.
            settings_ready: Whether a successfully loaded snapshot permits connecting.
        """
        self._available = available
        self._settings_ready = settings_ready
        self._render()

    def refresh_devices(self) -> None:
        """Request enumeration when idle without changing pending settings or video."""
        if self.can_refresh and self.service.refresh_devices():
            self._enumerating = True
            self._discovery_error = ""
            self._request_error = ""
            self._poll()

    def connect_capture(self, settings: "Settings") -> None:
        """Connect using a saved snapshot, retaining its mode until reconnect.

        Args:
            settings: Last successfully loaded or saved application settings.
        """
        if not self.can_refresh or not self._settings_ready:
            return
        try:
            options = CaptureOptions(settings.capture_device, fps=settings.capture_fps)
        except CaptureError as error:
            self._request_error = str(error)
            self._render()
            return
        if self.service.connect(options):
            self.active_settings = settings
            self._request_error = ""
            self._disconnecting = False
            self._next_image = 0.0
            self.panel.view.set_frame(None)
            self.set_preview_fps(settings.preview_fps)
            self._poll()

    def set_preview_fps(self, fps: int) -> None:
        """Apply a saved display limit without changing the capture connection.

        Args:
            fps: Validated settings value from 1 through 60 frames per second.
        """
        if fps != self._preview_fps:
            self._preview_fps = fps
            self._next_image = 0.0
            self.timer.setInterval(min(34, math.ceil(1000 / fps)))

    def disconnect_capture(self) -> None:
        """Clear displayed pixels before requesting asynchronous cleanup."""
        self._enumerating = False
        self._disconnecting = True
        self.panel.view.set_frame(None)
        self.service.disconnect()
        self._poll()

    def shutdown(self) -> None:
        """Clear video and request service shutdown without joining on the GUI."""
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
        self._render()
        if self._closing or snapshot.phase != "streaming" or snapshot.frame is None:
            self.panel.view.set_frame(None)
        elif self.active_settings is not None and (now := time.perf_counter()) >= self._next_image:
            self.panel.view.set_frame(snapshot.frame)
            self._next_image = now + 1 / self._preview_fps
        state = (snapshot.phase, snapshot.error, snapshot.cleanup, alive)
        if state != self._last_state:
            self._last_state = state
            self.changed.emit()

    def _render(self) -> None:
        self.panel.render_state(
            self._snapshot,
            self.active_settings,
            available=self._available and not self._closing and self.service.is_alive(),
            settings_ready=self._settings_ready,
            error=self._request_error,
        )
