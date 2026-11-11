"""Latest-frame video preview without a frame delivery queue."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QImage, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from gigas.game_io.capture import CaptureSnapshot
    from gigas.game_io.models import Frame
    from gigas.storage.settings import Settings

_PHASE_LABELS = {
    "idle": "未接続",
    "enumerating": "機器確認中",
    "connecting": "接続中",
    "streaming": "取得中",
    "stopping": "切断中",
    "error": "エラー",
    "closed": "終了",
}


class VideoView(QWidget):
    """Draw one copied BGR frame with its original aspect ratio.

    The widget owns its QImage independently of the capture source. Repeated
    updates for the same generation and sequence do not reconvert the image.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty preview without accessing any capture device."""
        super().__init__(parent)
        self._image = QImage()
        self._key: tuple[int, int] | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_frame(self, frame: "Frame | None") -> None:
        """Display the latest frame or clear the image when capture stops.

        Args:
            frame: Immutable source image. None invalidates any displayed image.
        """
        key = None if frame is None else (frame.generation, frame.sequence)
        if key == self._key:
            return
        self._key = key
        self._image = (
            QImage()
            if frame is None
            else QImage(
                frame.bgr, frame.width, frame.height, frame.width * 3, QImage.Format.Format_BGR888
            ).copy()
        )
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 -- Qt override
        """Paint letterboxed video without changing the shared source pixels."""
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if not self._image.isNull():
            size = self._image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRect(
                (self.width() - size.width()) // 2,
                (self.height() - size.height()) // 2,
                size.width(),
                size.height(),
            )
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target, self._image)
        painter.end()


class VideoPanel(QWidget):
    """Present capture controls, diagnostics, and the latest video image.

    Args:
        parent: Owning widget, if any.
    """

    connect_requested = Signal()
    disconnect_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create controls without discovering or opening devices."""
        super().__init__(parent)
        layout = QVBoxLayout(self)
        actions = QHBoxLayout()
        actions.addWidget(QLabel("映像", self))
        self.connect_button = QPushButton("接続", self)
        self.disconnect_button = QPushButton("切断", self)
        self.status = QLabel("未接続", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        for widget in (self.connect_button, self.disconnect_button, self.status):
            actions.addWidget(widget)
        actions.addStretch()
        layout.addLayout(actions)
        self.source = QLineEdit(self)
        self.source.setReadOnly(True)
        layout.addWidget(self.source)
        self.requested_mode = QLabel(self)
        self.actual_mode = QLabel(self)
        for label in (self.requested_mode, self.actual_mode):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            layout.addWidget(label)
        self.view = VideoView(self)
        layout.addWidget(self.view, 1)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(70)
        self.details.hide()
        layout.addWidget(self.details)
        self.connect_button.clicked.connect(self.connect_requested)
        self.disconnect_button.clicked.connect(self.disconnect_requested)
        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(False)

    def render_state(
        self,
        snapshot: "CaptureSnapshot",
        settings: "Settings | None",
        *,
        available: bool,
        settings_ready: bool,
        error: str = "",
    ) -> None:
        """Update controls independently of the slower image refresh rate.

        Args:
            snapshot: Current capture state, including cleanup confirmation.
            settings: Settings fixed when the displayed connection was accepted.
            available: Whether the service and application accept new commands.
            settings_ready: Whether saved settings permit connecting.
            error: Local validation error, shown ahead of a service error.
        """
        phase = snapshot.phase
        self.status.setText(
            "終了未確認" if snapshot.cleanup == "unconfirmed" else _PHASE_LABELS[phase]
        )
        self.connect_button.setEnabled(
            available
            and settings_ready
            and phase in {"idle", "error"}
            and snapshot.cleanup != "unconfirmed"
        )
        self.disconnect_button.setEnabled(
            available and phase in {"connecting", "streaming", "enumerating"}
        )
        self.source.setText("" if settings is None else settings.capture_device)
        self.requested_mode.setText(
            ""
            if settings is None
            else f"要求 {settings.capture_width} x {settings.capture_height} · "
            f"{settings.capture_fps} FPS"
        )
        frame = snapshot.frame
        self.actual_mode.setText(
            ""
            if frame is None
            else f"取得 {frame.width} x {frame.height} · "
            f"報告 {_rate(snapshot.reported_fps)} · 実測 {_rate(snapshot.measured_fps)} FPS"
        )
        detail = error or snapshot.error
        if self.details.toPlainText() != detail:
            self.details.setPlainText(detail)
        self.details.setVisible(bool(detail))


def _rate(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"
