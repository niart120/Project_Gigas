"""One input surface for an ordinary message and its explicit actions."""

from math import ceil
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFontMetricsF, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
)

from gigas.ui.prompt_edit import PromptEdit

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent


class _ModelButton(QPushButton):
    def minimumSizeHint(self) -> QSize:  # noqa: N802 -- Qt override
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:  # noqa: N802 -- Qt override
        hint = super().sizeHint()
        width = max(
            hint.width(), ceil(QFontMetricsF(self.font()).horizontalAdvance(self.text())) + 20
        )
        return QSize(min(240, width), hint.height())

    def paintEvent(self, event: "QPaintEvent") -> None:  # noqa: N802 -- Qt override
        del event
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, max(0, self.width() - 20)
        )
        painter = QPainter(self)
        self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
        painter.end()


class _ActionButton(QPushButton):
    def __init__(self, name: str, *, send: bool, parent: QWidget) -> None:
        super().__init__(parent)
        self._send = send
        self.setAccessibleName(name)
        self.setFixedSize(36, 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def paintEvent(self, event: "QPaintEvent") -> None:  # noqa: N802 -- Qt override
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        fill = palette.color(
            group, QPalette.ColorRole.Text if self._send else QPalette.ColorRole.Button
        )
        ink = palette.color(
            group, QPalette.ColorRole.Base if self._send else QPalette.ColorRole.ButtonText
        )
        if self.isEnabled() and (self.isDown() or self.underMouse()):
            factor = 135 if self.isDown() else 115
            fill = fill.lighter(factor) if fill.lightness() < 128 else fill.darker(factor)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
        painter.setPen(QPen(ink, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        center = self.rect().center()
        if self._send:
            painter.drawLine(center.x(), center.y() + 7, center.x(), center.y() - 7)
            painter.drawLine(center.x() - 5, center.y() - 2, center.x(), center.y() - 7)
            painter.drawLine(center.x(), center.y() - 7, center.x() + 5, center.y() - 2)
        else:
            painter.fillRect(center.x() - 5, center.y() - 5, 11, 11, ink)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(palette.color(QPalette.ColorRole.Highlight), 2))
            painter.drawEllipse(self.rect().adjusted(2, 2, -2, -2))
        painter.end()


class MessageComposer(QFrame):
    """Group the message draft and actions without owning delivery state.

    The parent decides availability and connects button gestures to existing
    commands. The placeholder and model value are outside the editable document,
    so copying or submitting the draft includes only user text.

    Args:
        parent: Owning conversation panel, if any.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty input surface without starting a connection."""
        super().__init__(parent)
        self.setObjectName("messageComposer")
        self.setStyleSheet(
            "QFrame#messageComposer { background: palette(alternate-base);"
            "border: 1px solid palette(mid); border-radius: 14px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        self.prompt = PromptEdit(self)
        self.prompt.setAccessibleName("Message")
        self.prompt.setPlaceholderText("Message")
        self.prompt.setFrameShape(QFrame.Shape.NoFrame)
        self.prompt.setStyleSheet("QPlainTextEdit { background: transparent; border: 0; }")
        self.prompt.setFixedHeight(
            2 * self.prompt.fontMetrics().lineSpacing()
            + 2 * int(self.prompt.document().documentMargin())
            + 1
        )
        layout.addWidget(self.prompt)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.addStretch()
        self.model_button = _ModelButton(self)
        self.model_button.setFlat(True)
        self.model_button.setAccessibleName("モデル・推論量の設定")
        self.model_button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        actions.addWidget(self.model_button)
        self.interrupt_button = _ActionButton("中断", send=False, parent=self)
        self.send_button = _ActionButton("送信", send=True, parent=self)
        actions.addWidget(self.interrupt_button)
        actions.addWidget(self.send_button)
        layout.addLayout(actions)
        self.set_model("", "")
        self.render_actions(
            send_enabled=False, interrupt_enabled=False, running=False, has_draft=False
        )

    def set_model(self, model: str, effort: str) -> None:
        """Display observed connection values, retaining the full accessible text.

        Args:
            model: Model reported by the active connection, or empty when unknown.
            effort: Reasoning effort reported by the connection, or empty.
        """
        value = " · ".join(part for part in (model, effort) if part)
        self.model_button.setText(value or "モデル・推論量")
        self.model_button.setAccessibleName(f"モデル・推論量の設定 {value}".rstrip())

    def render_actions(
        self, *, send_enabled: bool, interrupt_enabled: bool, running: bool, has_draft: bool
    ) -> None:
        """Show the permitted action positions without touching the editor.

        Args:
            send_enabled: Whether the existing delivery path accepts a submission.
            interrupt_enabled: Whether the existing stop path accepts interruption.
            running: Whether execution or its interruption is still in progress.
            has_draft: Whether the committed draft contains non-whitespace text.
        """
        self.send_button.setEnabled(send_enabled)
        self.interrupt_button.setEnabled(interrupt_enabled)
        self.interrupt_button.setFixedSize(28 if has_draft else 36, 28 if has_draft else 36)
        self.send_button.setVisible(not running or has_draft)
        self.interrupt_button.setVisible(running)
