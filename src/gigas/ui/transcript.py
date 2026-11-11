"""Plain conversation text that retains the reader's selection and viewport."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QPlainTextEdit

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


def _utf16_length(text: str) -> int:
    # QTextCursor positions count UTF-16 code units, unlike Python string indices.
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def _paragraph_text(text: str) -> str:
    # Qt inserts hard breaks as one paragraph separator; keep soft breaks and NBSP.
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\u2029", "\n")


def _character_boundary(text: str, offset: int) -> int:
    position = 0
    for character in text:
        if position >= offset:
            break
        position += 2 if ord(character) > 0xFFFF else 1
    return position


def _replacement(previous: str, text: str) -> tuple[int, int, str]:
    prefix = 0
    limit = min(len(previous), len(text))
    while prefix < limit and previous[prefix] == text[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < limit - prefix
        and previous[len(previous) - suffix - 1] == text[len(text) - suffix - 1]
    ):
        suffix += 1
    return (
        _utf16_length(previous[:prefix]),
        _utf16_length(previous[: len(previous) - suffix]),
        text[prefix : len(text) - suffix],
    )


class Transcript(QPlainTextEdit):
    """Show plain text while preserving the reader's position across updates.

    Args:
        parent: Owning conversation panel.
    """

    def __init__(self, parent: "QWidget | None" = None) -> None:
        """Create an empty, read-only conversation view."""
        super().__init__(parent)
        self.setReadOnly(True)

    def set_text(self, text: str) -> None:
        """Update changed text and retain selection and the visible passage.

        Follow the new tail only when previously at the tail with no selection.
        Selection endpoints inside the changed range are clamped to its replacement
        and advanced to a whole Unicode character boundary; selection outside that
        range keeps its text and direction. The viewport
        remains anchored to previously visible text outside the changed range.
        Hard paragraph breaks use Qt's single-character length. Soft line breaks
        and nonbreaking spaces remain unchanged in the document.

        Args:
            text: Complete conversation rendered as plain text, including newlines.
        """
        previous = _paragraph_text(self.document().toRawText())
        text = _paragraph_text(text)
        if text == previous:
            return
        start, end, inserted = _replacement(previous, text)
        inserted_length = _utf16_length(inserted)
        selected = self.textCursor()
        scrollbar = self.verticalScrollBar()
        following = scrollbar.value() == scrollbar.maximum() and not selected.hasSelection()
        visible = self.cursorForPosition(QPoint(0, 0))

        def shifted(position: int) -> int:
            if position < start:
                return position
            if position > end:
                return position + inserted_length - (end - start)
            if start == end:
                # An insertion at either selection edge must not expand the selection.
                return position + (
                    inserted_length
                    if selected.hasSelection() and position == selected.selectionStart()
                    else 0
                )
            if position == end:
                return start + inserted_length
            return start + _character_boundary(inserted, position - start)

        anchor, position = shifted(selected.anchor()), shifted(selected.position())
        edit = QTextCursor(self.document())
        edit.setPosition(start)
        edit.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        edit.insertText(inserted)
        selected.setPosition(anchor)
        selected.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(selected)
        block = visible.block()
        layout = block.layout()
        line = layout.lineForTextPosition(visible.positionInBlock()).lineNumber() if layout else 0
        scrollbar.setValue(scrollbar.maximum() if following else block.firstLineNumber() + line)
