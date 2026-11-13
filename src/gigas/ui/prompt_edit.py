"""Prompt editing with an explicit, composition-aware submit gesture."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import QPlainTextEdit

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class SubmitFilter(QObject):
    """Recognize Ctrl+Enter on standard single-line and choice editors.

    This filter keeps native password rendering and ordinary Enter behavior.
    Its owner decides whether the selected route can currently submit.
    """

    submit_requested = Signal()

    def __init__(self, parent: QObject) -> None:
        """Create a composition-aware input filter owned by the panel."""
        super().__init__(parent)
        self._composing = False

    def reset_composition(self) -> None:
        """Reset preedit tracking when the owner replaces the editor's draft."""
        self._composing = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 -- Qt override
        """Suppress submission during preedit and consume the explicit submit gesture."""
        if isinstance(event, QInputMethodEvent):
            self._composing = bool(event.preeditString())
        elif event.type() == QEvent.Type.FocusOut:
            self._composing = False
        elif (
            isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
            and event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
            and event.modifiers()
            in {
                Qt.KeyboardModifier.ControlModifier,
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.KeypadModifier,
            }
        ):
            if not self._composing and not event.isAutoRepeat():
                self.submit_requested.emit()
            return True
        return super().eventFilter(watched, event)


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

    @property
    def is_composing(self) -> bool:
        """Whether native preedit text is still being composed."""
        return self._composing

    def set_draft(self, text: str) -> None:
        """Replace a route's draft and discard the previous composition.

        Args:
            text: Committed draft to restore after changing or resolving a target.
        """
        self._composing = False
        self.setPlainText(text)

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
