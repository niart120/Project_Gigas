"""Prompt editing with an explicit, composition-aware submit gesture."""

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPlainTextEdit

if TYPE_CHECKING:
    from PySide6.QtGui import QInputMethodEvent, QKeyEvent
    from PySide6.QtWidgets import QWidget


class PromptEdit(QPlainTextEdit):
    """Request submission for Ctrl+Enter without changing ordinary text input.

    The submit_requested signal is a gesture, not permission to send. Its owner
    must apply current command availability. IME preedit and repeated key presses
    suppress the gesture; ordinary Enter retains QPlainTextEdit behavior.

    Args:
        parent: Owning widget, if any.
    """

    submit_requested = Signal()

    def __init__(self, parent: "QWidget | None" = None) -> None:
        """Create an empty editor with no active composition."""
        super().__init__(parent)
        self._composing = False

    def inputMethodEvent(self, event: "QInputMethodEvent") -> None:  # noqa: N802 -- Qt override
        """Track Qt preedit state while retaining native commit and editing behavior."""
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event: "QKeyEvent") -> None:  # noqa: N802 -- Qt override
        """Recognize the local submit gesture without consuming ordinary Enter."""
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and event.modifiers() in {
            Qt.KeyboardModifier.ControlModifier,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.KeypadModifier,
        }:
            if not self._composing and not event.isAutoRepeat():
                self.submit_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)
