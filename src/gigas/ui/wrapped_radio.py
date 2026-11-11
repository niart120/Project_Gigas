"""Native radio selection with plain, wrapped runtime-provided captions."""

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtWidgets import (
    QLabel,
    QRadioButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent
    from PySide6.QtWidgets import QWidget


class WrappedRadioButton(QRadioButton):
    """Keep native radio selection while wrapping its complete plain caption.

    Args:
        text: Original option label; ampersands are displayed literally.
        parent: Owning question input.
    """

    def __init__(self, text: str, parent: "QWidget") -> None:
        """Create an unchecked option with a mouse-transparent caption."""
        super().__init__(parent)
        style = self.style()
        self._left = style.pixelMetric(
            QStyle.PixelMetric.PM_ExclusiveIndicatorWidth
        ) + style.pixelMetric(QStyle.PixelMetric.PM_RadioButtonLabelSpacing)
        self.setAccessibleName(text)
        self._label = QLabel(text, self)
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.setWordWrap(True)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        caption_policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        caption_policy.setHeightForWidth(True)
        self._label.setSizePolicy(caption_policy)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(self._left, 0, 0, 0)
        layout.addWidget(self._label)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    @override
    def heightForWidth(self, width: int) -> int:
        """Return enough height for the caption and the native indicator."""
        return max(
            super().sizeHint().height(), self._label.heightForWidth(max(1, width - self._left))
        )

    @override
    def sizeHint(self) -> QSize:
        """Prefer a bounded caption width without truncating its text."""
        width = min(320, self._label.sizeHint().width() + self._left)
        return QSize(width, self.heightForWidth(width))

    @override
    def minimumSizeHint(self) -> QSize:
        """Allow narrow layouts to wrap instead of expanding the window."""
        return QSize(self._left + 20, super().sizeHint().height())

    @override
    def hitButton(self, position: QPoint) -> bool:
        """Make the entire wrapped caption clickable like a native radio label."""
        return self.rect().contains(position)

    @override
    def paintEvent(self, _event: "QPaintEvent") -> None:
        """Draw the native indicator at the first caption line."""
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.rect.setHeight(super().sizeHint().height())
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_RadioButton, option)
