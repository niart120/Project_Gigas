"""Item-based conversation text that retains selection and the visible passage."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import (
    QFocusEvent,
    QFont,
    QPalette,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import QTextBrowser, QWidget

if TYPE_CHECKING:
    from PySide6.QtCore import QObject
    from PySide6.QtGui import QResizeEvent

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
    "request": "",
    "answer": "回答",
}


@dataclass(slots=True)
class _Item:
    kind: HistoryKind
    start: int
    text: str = ""
    displayed: str = ""
    emphasis: tuple[tuple[int, int], ...] = ()
    control: QWidget | None = None


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
        show_headings: Include applicable role headings; false for a separately labelled Plan.
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
        self._controls: set[QWidget] = set()
        self._laying_out_controls = False
        self.verticalScrollBar().valueChanged.connect(self._position_controls)
        document_layout = self.document().documentLayout()
        if document_layout is not None:
            document_layout.documentSizeChanged.connect(self._position_controls)

    def set_item_control(self, item_id: str, control: QWidget | None) -> None:
        """Place a native question or approval control immediately below its text.

        The item must already exist. Its text remains part of the single selectable
        document. Passing None hides the previous control without deleting it or
        discarding its draft. This view owns every attached control until clear.
        Adding, replacing or removing controls preserves selection and the visible
        passage, following the tail only when it was unselected.

        Args:
            item_id: Identifier previously supplied to update_item.
            control: Input widget to attach, or None to remove the active controls.

        Raises:
            KeyError: No text item has the given identifier.
        """
        item = self._items[item_id]
        previous = item.control
        if previous is not None and previous is not control:
            previous.hide()
        item.control = control
        if control is not None and control not in self._controls:
            self._controls.add(control)
            control.setParent(self.viewport())
            control.setAutoFillBackground(True)
            control.setBackgroundRole(QPalette.ColorRole.AlternateBase)
            control.installEventFilter(self)
            for child in control.findChildren(QWidget):
                child.installEventFilter(self)
        self._refresh_controls()

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
        self,
        item_id: str,
        steps: "tuple[PlanStep, ...]",
        explanation: str = "",
        *,
        inactive: bool = False,
    ) -> None:
        """Replace a complete public plan without inferring stable step IDs.

        Steps use numbered rows and their public status labels. Active steps use
        bold type unless inactive. Selection and viewport follow the same rules
        as other items; presentation does not mutate the supplied step data.

        Args:
            item_id: Stable identifier for this turn's plan snapshot.
            steps: Ordered complete plan supplied by the runtime.
            explanation: Optional literal explanation of this public plan update.
            inactive: Suppress active emphasis and the active label in a retained
                plan whose turn is no longer executing.
        """
        text = f"{_paragraph_text(explanation)}\n\n" if explanation else ""
        emphasis: list[tuple[int, int]] = []
        labels = {"pending": "未着手", "inProgress": "進行中", "completed": "完了"}
        for index, step in enumerate(steps, 1):
            label = "" if inactive and step.status == "inProgress" else labels[step.status]
            line = f"{index}. {_paragraph_text(step.step)}"
            if label:
                line += f"　{label}"
            if step.status == "inProgress" and not inactive:
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
        """Discard all received items and delete attached or hidden controls."""
        for control in self._controls:
            control.hide()
            control.deleteLater()
        self._controls.clear()
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
        self._layout_controls()
        layout = self.document().documentLayout()
        if layout is not None:
            layout.documentSize()
        scrollbar.setValue(
            scrollbar.maximum()
            if following
            else scrollbar.value() + self.cursorRect(visible).top() - visible_y
        )
        self._position_controls()

    def eventFilter(self, watched: "QObject", event: QEvent) -> bool:  # noqa: N802
        """Fit native input layouts and expose their keyboard focus without selection changes."""
        if not self._laying_out_controls:
            if watched in self._controls and event.type() in {
                QEvent.Type.LayoutRequest,
                QEvent.Type.Resize,
            }:
                self._refresh_controls()
            elif (
                event.type() == QEvent.Type.FocusIn
                and isinstance(event, QFocusEvent)
                and event.reason()
                in {
                    Qt.FocusReason.TabFocusReason,
                    Qt.FocusReason.BacktabFocusReason,
                    Qt.FocusReason.MouseFocusReason,
                    Qt.FocusReason.ShortcutFocusReason,
                }
                and isinstance(watched, QWidget)
            ):
                active = any(
                    item.control is watched
                    or (item.control is not None and item.control.isAncestorOf(watched))
                    for item in self._items.values()
                )
                if active:
                    top = watched.mapTo(self.viewport(), QPoint()).y()
                    bottom = top + watched.height()
                    bar = self.verticalScrollBar()
                    if top < 0:
                        bar.setValue(bar.value() + top)
                    elif bottom > self.viewport().height():
                        bar.setValue(bar.value() + bottom - self.viewport().height())
        return super().eventFilter(watched, event)

    def _control_anchor(self, item: _Item) -> QTextCursor:
        cursor = QTextCursor(self.document())
        separator = 2 if self._show_headings else 0
        position = item.start + _utf16_length(item.displayed) - separator
        cursor.setPosition(max(item.start, position - 1))
        return cursor

    def _refresh_controls(self) -> None:
        bar = self.verticalScrollBar()
        following = bar.value() == bar.maximum() and not self.textCursor().hasSelection()
        visible = self.cursorForPosition(QPoint(0, 1))
        visible_y = self.cursorRect(visible).top()
        self._layout_controls()
        bar.setValue(
            bar.maximum() if following else bar.value() + self.cursorRect(visible).top() - visible_y
        )
        self._position_controls()

    def _layout_controls(self) -> None:
        if not self._controls or self._laying_out_controls:
            return
        self._laying_out_controls = True
        try:
            margin = int(self.document().documentMargin())
            width = max(1, self.viewport().width() - 2 * margin)
            for item in self._items.values():
                control = item.control
                if not item.displayed:
                    if control is not None:
                        control.hide()
                    continue
                height = 0
                if control is not None:
                    hint = (
                        control.heightForWidth(width)
                        if control.hasHeightForWidth()
                        else control.sizeHint().height()
                    )
                    height = min(
                        control.maximumHeight(),
                        max(control.minimumHeight(), control.minimumSizeHint().height(), hint),
                    )
                    control.resize(width, max(1, height))
                    control.show()
                cursor = self._control_anchor(item)
                block = cursor.blockFormat()
                spacing = height + 6 if control is not None else 0
                if block.bottomMargin() != spacing:
                    block.setBottomMargin(spacing)
                    cursor.setBlockFormat(block)
            layout = self.document().documentLayout()
            if layout is not None:
                layout.documentSize()
        finally:
            self._laying_out_controls = False
        if width != max(1, self.viewport().width() - 2 * margin):
            # Adding the reserved space can introduce the vertical scrollbar.
            self._layout_controls()
        self._position_controls()

    def _position_controls(self) -> None:
        if not self._controls or self._laying_out_controls:
            return
        margin = int(self.document().documentMargin())
        for item in self._items.values():
            if item.control is not None and item.displayed:
                anchor = self._control_anchor(item)
                item.control.move(margin, self.cursorRect(anchor).bottom() + 3)

    def _item(self, item_id: str, kind: HistoryKind) -> _Item:
        item = self._items.get(item_id)
        if item is None:
            item = _Item(kind, self.document().characterCount() - 1)
            self._items[item_id] = item
        item.kind = kind
        return item

    def _render_item(self, item: _Item) -> None:
        hidden = item.kind == "reasoning" and not self._reasoning_visible
        heading = (
            f"{_HEADINGS[item.kind]}\n" if self._show_headings and _HEADINGS[item.kind] else ""
        )
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
        # Qt emits layout/scroll signals synchronously while inserting text. Do not
        # position controls until their item offsets match the changed document.
        self._laying_out_controls = True
        try:
            edit = QTextCursor(self.document())
            if item.control is not None and item.displayed:
                control_cursor = self._control_anchor(item)
                control_format = control_cursor.blockFormat()
                control_format.setBottomMargin(0)
                control_cursor.setBlockFormat(control_format)
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
        finally:
            self._laying_out_controls = False
        self._layout_controls()
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
        self._position_controls()

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
        if item.kind in {"user", "request", "answer"}:
            block.setBackground(self.palette().brush(QPalette.ColorRole.AlternateBase))
        elif item.kind == "reasoning":
            block.setLeftMargin(12)
            body.setForeground(self.palette().brush(QPalette.ColorRole.PlaceholderText))
        cursor.setBlockFormat(block)
        cursor.setCharFormat(body)
        body_start = item.start
        if self._show_headings and _HEADINGS[item.kind]:
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
