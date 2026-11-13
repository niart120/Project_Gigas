"""One input surface for an ordinary message and its explicit actions."""

from math import ceil
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QFontMetricsF, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gigas.runtime.codex.models import ModelSelection
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
        self.setFixedSize(30, 30)
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

    model_selected = Signal(str, str)

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
        self.goal_mode = QToolButton(self)
        self.goal_mode.setText("目標")
        self.goal_mode.setCheckable(True)
        self.goal_mode.setAccessibleName("目標として送信")
        actions.addWidget(self.goal_mode)
        actions.addStretch()
        self.model_button = _ModelButton(self)
        self.model_button.setFlat(True)
        self.model_button.setAccessibleName("モデル・推論レベルの設定")
        self.model_button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        actions.addWidget(self.model_button)
        self.interrupt_button = _ActionButton("中断", send=False, parent=self)
        self.send_button = _ActionButton("送信", send=True, parent=self)
        actions.addWidget(self.interrupt_button)
        actions.addWidget(self.send_button)
        layout.addLayout(actions)
        self.selection: ModelSelection | None = None
        self.popup = QFrame(self, Qt.WindowType.Popup)
        self.popup.setFrameShape(QFrame.Shape.StyledPanel)
        form = QFormLayout(self.popup)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(12)
        self.model_choice = QComboBox(self.popup)
        self.model_choice.setMinimumContentsLength(20)
        self.model_choice.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.effort_choice = QComboBox(self.popup)
        self.model_choice.setAccessibleName("モデル")
        self.effort_choice.setAccessibleName("推論レベル")
        form.addRow("モデル", self.model_choice)
        form.addRow("推論レベル", self.effort_choice)
        self.model_choice.activated.connect(self._choose_model)
        self.effort_choice.activated.connect(self._apply_model)
        self.model_button.clicked.connect(self._open_models)
        self.set_choices(None)
        self.render_actions(
            send_enabled=False, interrupt_enabled=False, running=False, has_draft=False
        )

    def set_model(self, model: str, effort: str) -> None:
        """Display acknowledged next-turn values, retaining the full accessible text.

        Args:
            model: Model selected for the next turn, or empty when disconnected.
            effort: Reasoning level selected for the next turn, or empty.
        """
        value = " · ".join(part for part in (model, effort) if part)
        self.model_button.setText(value or "未接続")
        self.model_button.setAccessibleName(f"モデル・推論レベルの設定 {value}".rstrip())

    def set_choices(self, selection: ModelSelection | None) -> None:
        """Display the connection's acknowledged next-turn selection."""
        self.selection = selection
        if selection is None:
            self.popup.hide()
        else:
            self._populate_models()
        self.set_model(selection.model if selection else "", selection.effort if selection else "")

    def _populate_models(self) -> None:
        if self.selection is None:
            return
        self.model_choice.blockSignals(True)
        self.model_choice.clear()
        self.model_choice.addItems([choice.model for choice in self.selection.choices])
        self.model_choice.setCurrentText(self.selection.model)
        self.model_choice.blockSignals(False)
        self._model_changed()
        self.effort_choice.setCurrentText(self.selection.effort)

    def _open_models(self) -> None:
        if self.selection is None:
            return
        self._populate_models()
        self.popup.adjustSize()
        anchor = self.model_button.mapToGlobal(QPoint(self.model_button.width(), 0))
        bounds = self.screen().availableGeometry()
        x = max(
            bounds.left(), min(anchor.x() - self.popup.width(), bounds.right() - self.popup.width())
        )
        y = max(bounds.top(), anchor.y() - self.popup.height() - 6)
        self.popup.move(x, y)
        self.popup.show()
        self.model_choice.setFocus()

    def _model_changed(self) -> None:
        self.effort_choice.clear()
        if self.selection is None:
            return
        for choice in self.selection.choices:
            if choice.model == self.model_choice.currentText():
                self.effort_choice.addItems(list(choice.efforts))
                self.effort_choice.setCurrentText(choice.default_effort)
                break

    def _choose_model(self) -> None:
        self._model_changed()
        self._apply_model()

    def _apply_model(self) -> None:
        if self.effort_choice.count():
            self.model_selected.emit(
                self.model_choice.currentText(), self.effort_choice.currentText()
            )

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
        self.interrupt_button.setFixedSize(24 if has_draft else 30, 24 if has_draft else 30)
        self.send_button.setVisible(not running or has_draft)
        self.interrupt_button.setVisible(running)
