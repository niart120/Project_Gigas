"""Latest-frame video preview without a frame delivery queue."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter, QPaintEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

if TYPE_CHECKING:
    from gigas.game_io.models import Frame


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
