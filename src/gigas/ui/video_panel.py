"""Latest-frame video preview without a frame delivery queue."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QImage, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gigas.ui.capture_choices import capture_device_choices
from gigas.ui.elided_label import ElidedLabel

if TYPE_CHECKING:
    from gigas.game_io.capture import CaptureSnapshot
    from gigas.game_io.capture.models import Frame
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
    """Draw one copied BGR frame, preserving its aspect ratio by default.

    The widget owns its QImage independently of the capture source. Repeated
    updates for the same generation and sequence do not reconvert the image.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty preview without accessing any capture device."""
        super().__init__(parent)
        self._image = QImage()
        self._key: tuple[int, int] | None = None
        self._preview_size = QSize(640, 360)
        self._letterbox = True
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_preview_size(self, width: int, height: int) -> None:
        """Set the display target while allowing the layout to allocate less space."""
        self._preview_size = QSize(width, height)
        self.setMaximumSize(self._preview_size)
        self.updateGeometry()

    def sizeHint(self) -> QSize:  # noqa: N802 -- Qt override
        """Prefer the selected display target without imposing a minimum size."""
        return self._preview_size

    def set_letterbox(self, enabled: bool) -> None:
        """Change preview fitting without changing the source frame or connection.

        Args:
            enabled: True fits the complete image with black margins, preserving
                its aspect ratio. False stretches it to fill the preview. The
                mode also applies to future frames, including after clearing.
        """
        if self._letterbox != enabled:
            self._letterbox = enabled
            self.update()

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
        """Paint fitted or stretched video without changing the shared pixels."""
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if not self._image.isNull():
            size = (
                self._image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
                if self._letterbox
                else self.size()
            )
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
    """Draw video and own external status labels.

    MainWindow places the labels in its status bar.

    Args:
        parent: Owning widget, if any.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create the preview without discovering or opening devices."""
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel("未接続", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.source = ElidedLabel(parent=self)
        self.status.hide()
        self.source.hide()
        self.view = VideoView(self)
        layout.addWidget(self.view, 1, Qt.AlignmentFlag.AlignCenter)

    def render_state(
        self,
        snapshot: "CaptureSnapshot",
        settings: "Settings | None",
    ) -> None:
        """Update controls independently of the slower image refresh rate.

        Args:
            snapshot: Current capture state, including cleanup confirmation.
            settings: Settings fixed when the displayed connection was accepted.
        """
        phase = snapshot.phase
        self.status.setText(
            "終了未確認" if snapshot.cleanup == "unconfirmed" else _PHASE_LABELS[phase]
        )
        owned = (
            phase in {"connecting", "streaming", "stopping"} or snapshot.cleanup == "unconfirmed"
        )
        names = [
            label
            for label, path in capture_device_choices(snapshot.devices)
            if owned and settings is not None and path == settings.capture_device
        ]
        self.source.setText(names[0] if len(names) == 1 else "")
