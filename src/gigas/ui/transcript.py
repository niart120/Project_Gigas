"""Item-based conversation text that retains selection and the visible passage."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QPalette, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QTextBrowser

if TYPE_CHECKING:
    from PySide6.QtGui import QResizeEvent
    from PySide6.QtWidgets import QWidget

    from gigas.runtime.codex.models import PlanStep

type HistoryKind = Literal[
    "user", "assistant", "reasoning", "activity", "plan", "request", "answer"
]

_HEADINGS = {
    "user": "あなた",
    "assistant": "Codex",
    "reasoning": "reasoning",
    "activity": "activity",
    "plan": "Plan",
    "request": "要求",
    "answer": "回答",
}


@dataclass(slots=True)
class _Item:
    kind: HistoryKind
    start: int
    text: str = ""
    displayed: str = ""
    emphasis: tuple[tuple[int, int], ...] = ()


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


class Transcript(QTextBrowser):
    """Show styled items while preserving selection and the visible passage.

    Received text is inserted literally, without interpreting HTML or loading
    external resources. Hidden reasoning remains in the item data, outside the
    displayed document and its standard selection/copy operations.

    Args:
        parent: Owning conversation panel.
        show_headings: Include role headings; false for a separately labelled Plan.
    """

    def __init__(self, parent: "QWidget | None" = None, *, show_headings: bool = True) -> None:
        """Create an empty, read-only conversation view."""
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setUndoRedoEnabled(False)
        self._items: dict[str, _Item] = {}
        self._reasoning_visible = True
        self._show_headings = show_headings

    def update_item(
        self,
        item_id: str,
        text: str,
        *,
        kind: HistoryKind = "assistant",
        append: bool = False,
    ) -> None:
        """Insert or update one item in its original reception order.

        Follow the new tail only when previously at the tail with no selection.
        Selection endpoints inside the changed range are clamped to its replacement
        and advanced to a whole Unicode character boundary; selection outside that
        range keeps its text and direction. The viewport stays anchored to visible
        text outside the changed range. Empty items have no displayed heading.
        Hard paragraph breaks use Qt's single-character length. Soft line breaks
        and nonbreaking spaces remain unchanged in the document.

        Args:
            item_id: Stable identifier, distinct across kinds and turns in this view.
            text: Literal item body, either a replacement or a delta.
            kind: Presentation role; reasoning is subject to its visibility setting.
            append: Append the body to the received item instead of replacing it.
        """
        item = self._item(item_id, kind)
        item.text = item.text + text if append else text
        item.emphasis = ()
        self._render_item(item)

    def update_plan(
        self, item_id: str, steps: "tuple[PlanStep, ...]", explanation: str = ""
    ) -> None:
        """Replace a complete public plan without inferring stable step IDs.

        Completed steps use a check, active steps a mark and bold type, and pending
        steps a bullet. Selection and viewport follow the same rules as other items.

        Args:
            item_id: Stable identifier for this turn's plan snapshot.
            steps: Ordered complete plan supplied by the runtime.
            explanation: Optional literal explanation, used in history only.
        """
        text = f"{_paragraph_text(explanation)}\n\n" if explanation else ""
        emphasis: list[tuple[int, int]] = []
        markers = {"pending": "•", "inProgress": "▶", "completed": "✓"}
        for step in steps:
            line = f"{markers[step.status]} {_paragraph_text(step.step)}"
            if step.status == "inProgress":
                emphasis.append((_utf16_length(text), _utf16_length(line)))
            text += f"{line}\n"
        item = self._item(item_id, "plan")
        item.text = text.removesuffix("\n")
        item.emphasis = tuple(emphasis)
        self._render_item(item)

    def set_reasoning_visible(self, visible: bool) -> None:
        """Show or hide received summaries without discarding their bodies.

        A selection overlapping a hidden item is cleared at the next displayed
        item, or the document end when none follows.

        Args:
            visible: Whether reasoning items appear in the displayed document.
        """
        if visible == self._reasoning_visible:
            return
        self._reasoning_visible = visible
        for item in self._items.values():
            if item.kind == "reasoning":
                self._render_item(item)

    def clear(self) -> None:
        """Discard all received items and their displayed text."""
        self._items.clear()
        super().clear()

    def resizeEvent(self, event: "QResizeEvent") -> None:  # noqa: N802 -- Qt override
        """Keep the tail or visible passage when surrounding controls change size."""
        scrollbar = self.verticalScrollBar()
        following = (
            scrollbar.value() == scrollbar.maximum() and not self.textCursor().hasSelection()
        )
        visible = self.cursorForPosition(QPoint(0, 1))
        visible_y = self.cursorRect(visible).top()
        super().resizeEvent(event)
        layout = self.document().documentLayout()
        if layout is not None:
            layout.documentSize()
        scrollbar.setValue(
            scrollbar.maximum()
            if following
            else scrollbar.value() + self.cursorRect(visible).top() - visible_y
        )

    def _item(self, item_id: str, kind: HistoryKind) -> _Item:
        item = self._items.get(item_id)
        if item is None:
            item = _Item(kind, self.document().characterCount() - 1)
            self._items[item_id] = item
        item.kind = kind
        return item

    def _render_item(self, item: _Item) -> None:
        hidden = item.kind == "reasoning" and not self._reasoning_visible
        heading = f"{_HEADINGS[item.kind]}\n" if self._show_headings else ""
        separator = "\n\n" if self._show_headings else ""
        displayed = (
            f"{heading}{_paragraph_text(item.text)}{separator}" if item.text and not hidden else ""
        )
        if displayed == item.displayed:
            return
        start, end, inserted = _replacement(item.displayed, displayed)
        start += item.start
        end += item.start
        inserted_length = _utf16_length(inserted)
        delta = inserted_length - (end - start)
        selected = self.textCursor()
        scrollbar = self.verticalScrollBar()
        following = scrollbar.value() == scrollbar.maximum() and not selected.hasSelection()
        # At y=0 Qt may hit the preceding, fully clipped line at a block boundary.
        visible = self.cursorForPosition(QPoint(0, 1))
        visible_y = self.cursorRect(visible).top()

        def shifted(position: int) -> int:
            if position < start:
                return position
            if position > end:
                return position + delta
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
        if hidden and selected.selectionStart() < end and selected.selectionEnd() > start:
            anchor = position = item.start
        edit = QTextCursor(self.document())
        edit.setPosition(start)
        edit.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        edit.insertText(inserted)
        if delta and item is not next(reversed(self._items.values())):
            after_item = False
            for stored in self._items.values():
                if after_item:
                    stored.start += delta
                if stored is item:
                    after_item = True
        item.displayed = displayed
        self._format_item(item)
        selected.setPosition(anchor)
        selected.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(selected)
        layout = self.document().documentLayout()
        if layout is not None:
            layout.documentSize()
        scrollbar.setValue(
            scrollbar.maximum()
            if following
            else scrollbar.value() + self.cursorRect(visible).top() - visible_y
        )

    def _format_item(self, item: _Item) -> None:
        if not item.displayed:
            return
        cursor = QTextCursor(self.document())
        cursor.setPosition(item.start)
        end = item.start + _utf16_length(item.displayed)
        cursor.setPosition(end - 1 if self._show_headings else end, QTextCursor.MoveMode.KeepAnchor)
        body = QTextCharFormat()
        body.setFont(self.font())
        body.setForeground(self.palette().brush(QPalette.ColorRole.Text))
        block = QTextBlockFormat()
        if item.kind == "user":
            block.setBackground(self.palette().brush(QPalette.ColorRole.AlternateBase))
        elif item.kind == "reasoning":
            block.setLeftMargin(12)
            body.setForeground(self.palette().brush(QPalette.ColorRole.PlaceholderText))
        cursor.setBlockFormat(block)
        cursor.setCharFormat(body)
        body_start = item.start
        if self._show_headings:
            cursor.setPosition(item.start)
            cursor.setPosition(
                item.start + _utf16_length(_HEADINGS[item.kind]), QTextCursor.MoveMode.KeepAnchor
            )
            heading = QTextCharFormat(body)
            heading.setFontWeight(QFont.Weight.DemiBold)
            if item.kind in {"reasoning", "activity"}:
                heading.setFontPointSize(self.font().pointSizeF() * 0.9)
            cursor.setCharFormat(heading)
            body_start += _utf16_length(_HEADINGS[item.kind]) + 1
        bold = QTextCharFormat(body)
        bold.setFontWeight(QFont.Weight.Bold)
        for offset, length in item.emphasis:
            cursor.setPosition(body_start + offset)
            cursor.setPosition(body_start + offset + length, QTextCursor.MoveMode.KeepAnchor)
            cursor.setCharFormat(bold)
