"""Single-line identifiers whose full values never impose a window width."""

from html import escape

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class ElidedLabel(QLabel):
    """Keep the plain value accessible while eliding only its painted text.

    Args:
        text: Full identifier or short status, also exposed by text() and accessibility.
        parent: Owning widget, if any.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        """Create a label that can shrink to the space allocated by its layout."""
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 -- Qt override
        """Retain the full value, including markup-looking identifier characters."""
        super().setText(text)
        self.setAccessibleName(text)
        self.setToolTip(f"<qt>{escape(text)}</qt>" if text else "")

    def minimumSizeHint(self) -> QSize:  # noqa: N802 -- Qt override
        """Allow horizontal shrinking without clipping the line height."""
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:  # noqa: N802 -- Qt override
        """Prefer a bounded width even before the window has been shown."""
        size = super().sizeHint()
        return QSize(min(320, size.width()), size.height())

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 -- Qt override
        """Paint using the current font and allocation without mutating the value."""
        del event
        painter = QPainter(self)
        rect = self.contentsRect().adjusted(self.margin(), 0, -self.margin(), 0)
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width())
        self.style().drawItemText(
            painter,
            rect,
            self.alignment(),
            self.palette(),
            self.isEnabled(),
            text,
            QPalette.ColorRole.WindowText,
        )
        painter.end()
