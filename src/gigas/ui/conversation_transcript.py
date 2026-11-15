"""Selectable conversation frames, native Markdown and collapsible activity."""

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote

from PySide6.QtCore import QPoint, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPalette,
    QPen,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocumentFragment,
    QTextFrame,
    QTextFrameFormat,
)

from gigas.ui._markdown import (
    COLORS,
    DARK_COLORS,
    LABELS,
    Blocks,
    MarkdownQueue,
    Parsed,
    ParseJob,
    block_snapshot,
    formatted_document,
)
from gigas.ui.transcript import Transcript, _Item, _utf16_length

if TYPE_CHECKING:
    from PySide6.QtGui import QCloseEvent, QPaintEvent, QResizeEvent, QTextDocument
    from PySide6.QtWidgets import QWidget

    from gigas.runtime.codex.history import HistoryEntry, HistoryKind
    from gigas.runtime.codex.models import ActivityEvent


@dataclass(slots=True)
class _Rendered:
    frame: QTextFrame
    text: str
    blocks: Blocks
    complex: bool
    color: QColor | None


def _activity_label(activity: "ActivityEvent") -> tuple[str, str]:
    names = {
        "commandExecution": "シェル",
        "fileChange": "ファイル変更",
        "mcpToolCall": "ツール",
        "webSearch": "Web検索",
    }
    name = names[activity.kind]
    target = activity.title.splitlines()[0] if activity.title else ""
    if activity.kind == "mcpToolCall" and "/" in target:
        server, target = target.rsplit("/", 1)
        if server.startswith("gigas_") and target in {"observe", "act_and_observe"}:
            return ("画面取得" if target == "observe" else "入力・画面取得"), ""
    return name, target


def _mapped_offset(old: str, new: str, offset: int) -> int:
    if old == new or new.startswith(old):
        return min(offset, _utf16_length(new))
    count, index = 0, 0
    for character in old:
        if count >= offset:
            break
        count += _utf16_length(character)
        index += 1
    for tag, start, end, left, right in SequenceMatcher(
        None, old, new, autojunk=True
    ).get_opcodes():
        if start <= index < end or index == end == len(old):
            target = (
                left + index - start if tag == "equal" else left + min(index - start, right - left)
            )
            return _utf16_length(new[:target])
    return _utf16_length(new)


class ConversationTranscript(Transcript):
    """Render one conversation while sharing native request controls with plain views.

    Received bodies remain in the inherited item model. Frames retain only the
    rendered text and comparison cache; the worker never owns conversation state.
    """

    reasoning_visibility_changed = Signal(bool)
    external_link = Signal(QUrl)

    def __init__(self, parent: "QWidget | None" = None) -> None:
        """Create one document and a lazy Markdown parser."""
        self._rendered: dict[int, _Rendered] = {}
        self._revisions: dict[int, int] = {}
        self._expanded: set[str] = set()
        self._expanded_groups: set[str] = set()
        super().__init__(parent)
        self._parser = MarkdownQueue(self)
        self._parser.ready.connect(self._parsed)
        self.anchorClicked.connect(self._link)

    @property
    def rendering_idle(self) -> bool:
        """Whether all received Markdown is reflected in the document."""
        return self._parser.idle

    @property
    def shutdown_complete(self) -> bool:
        """Whether the parser thread has stopped without a GUI-thread join."""
        return self._parser.stopped

    def shutdown(self) -> None:
        """Stop accepting rendering work as part of application shutdown."""
        self._parser.shutdown()

    def closeEvent(self, event: "QCloseEvent") -> None:  # noqa: N802
        """Retire the worker when a standalone view is closed."""
        self.shutdown()
        super().closeEvent(event)

    def clear(self) -> None:
        """Invalidate old parses, controls and expansion state on conversation change."""
        self._parser.clear()
        self._rendered.clear()
        self._revisions.clear()
        self._expanded.clear()
        self._expanded_groups.clear()
        super().clear()

    def update_entry(self, entry: "HistoryEntry", *, append: bool = False) -> None:
        """Accumulate raw text immediately; defer only Markdown parsing and painting."""
        item = self._item(entry.key, entry.kind)
        if not append:
            self._revisions[id(item)] = self._revisions.get(id(item), 0) + 1
        super().update_entry(entry, append=append)

    def update_item(
        self,
        item_id: str,
        text: str,
        *,
        kind: "HistoryKind" = "assistant",
        append: bool = False,
    ) -> None:
        """Invalidate older parses when replacing a local-only response body."""
        item = self._item(item_id, kind)
        if not append:
            self._revisions[id(item)] = self._revisions.get(id(item), 0) + 1
        super().update_item(item_id, text, kind=kind, append=append)

    def set_reasoning_visible(self, visible: bool) -> None:
        """Apply the setting to both transcript summaries and the subtitle pane."""
        super().set_reasoning_visible(visible)
        self.reasoning_visibility_changed.emit(visible)

    def toggle_details(self, key: str) -> None:
        """Toggle a retained activity without discarding its full text."""
        item = self._items.get(key)
        if item is None or item.kind != "activity":
            return
        if key in self._expanded:
            self._expanded.remove(key)
        else:
            self._expanded.add(key)
        self._render_item(item)

    def _link(self, url: QUrl) -> None:
        if url.scheme() == "gigas-group":
            key = unquote(url.path())
            if (item := self._items.get(key)) is not None and item.kind == "activity":
                self._expanded_groups.symmetric_difference_update({key})
                self._render_item(item)
        elif url.scheme() == "gigas-detail":
            self.toggle_details(unquote(url.path()))
        elif url.scheme() in {"http", "https"}:
            self.external_link.emit(url)

    def loadResource(self, resource_type: int, url: QUrl | str) -> None:  # noqa: N802
        """Do not resolve embedded images or resources from generated Markdown."""
        del resource_type, url

    def _key(self, item: _Item) -> str:
        return next(key for key, stored in self._items.items() if stored is item)

    def _phase_color(self, item: _Item) -> QColor | None:
        phase = item.entry.phase if item.entry else None
        colors = (
            DARK_COLORS
            if self.palette().color(QPalette.ColorRole.Base).lightness() < 128
            else COLORS
        )
        return QColor(colors[phase]) if phase in colors else None

    def _presentation(self, item: _Item) -> tuple[str, str]:
        if item.kind == "assistant":
            phase = item.entry.phase if item.entry else None
            return item.text, LABELS.get(phase or "", "Codex")
        if item.kind == "reasoning":
            return item.text, "推論要約"
        if item.kind == "activity":
            key = self._key(item)
            activity = item.entry.activity if item.entry else None
            if activity is None:
                title = item.text.splitlines()[0] if item.text else "活動"
                details = item.text
            else:
                name, target = _activity_label(activity)
                status = {
                    "inProgress": "実行中",
                    "completed": "完了",
                    "failed": "失敗",
                    "declined": "拒否",
                }.get(activity.status or "", "")
                target = self.fontMetrics().elidedText(
                    target, Qt.TextElideMode.ElideRight, max(60, self.viewport().width() - 200)
                )
                title = " · ".join(part for part in (name, target, status) if part)
                details = item.text
                if activity.arguments is not None:
                    details += "\n\n引数\n" + json.dumps(
                        activity.arguments, ensure_ascii=False, indent=2
                    )
            marker = "▾" if key in self._expanded else "▸"
            return (f"{marker} {title}" + (f"\n\n{details}" if key in self._expanded else ""), "")
        return item.text, {"user": "あなた", "plan": "Plan", "answer": "回答"}.get(item.kind, "")

    def _render_item(self, item: _Item) -> None:
        if item.kind != "activity":
            self._render_one(item)
            return
        # Only the adjacent activity segment can change grouping after an update.
        items = list(self._items.values())
        index = next(i for i, value in enumerate(items) if value is item)
        left = right = index
        while left and items[left - 1].kind == "activity":
            left -= 1
        while right + 1 < len(items) and items[right + 1].kind == "activity":
            right += 1
        segment = items[left : right + 1]
        index = 0
        while index < len(segment):
            first = segment[index]
            end = index + 1
            activity = first.entry.activity if first.entry else None
            if activity is not None and activity.status == "completed":
                while end < len(segment):
                    other = segment[end].entry
                    if (
                        other is None
                        or other.activity is None
                        or other.activity.status != "completed"
                        or other.activity.turn_id != activity.turn_id
                    ):
                        break
                    end += 1
            group = segment[index:end]
            for member in group[1:]:
                if id(member) in self._rendered:
                    self._edit(member, None, [])
            self._render_one(first, group=group if len(group) > 1 else None)
            index = end

    def _group_document(self, group: list[_Item]) -> "QTextDocument":
        key = self._key(group[0])
        opened = key in self._expanded_groups
        names = {
            _activity_label(item.entry.activity)[0]
            for item in group
            if item.entry is not None and item.entry.activity is not None
        }
        name = next(iter(names)) if len(names) == 1 else "活動"
        text = f"{'▾' if opened else '▸'} {name}を{len(group)}件実行しました"
        links = [(0, _utf16_length(text), "gigas-group:" + quote(key, safe=""))]
        children: list[tuple[int, int]] = []
        if opened:
            for member in group:
                body, _ = self._presentation(member)
                start = _utf16_length(text) + 1
                text += "\n" + body
                links.append(
                    (
                        start,
                        _utf16_length(body.splitlines()[0]),
                        "gigas-detail:" + quote(self._key(member), safe=""),
                    )
                )
                children.append((start, _utf16_length(body)))
        doc = formatted_document(
            text,
            "",
            self.font(),
            self.palette().color(QPalette.ColorRole.Text),
            self.palette().color(QPalette.ColorRole.AlternateBase),
            markdown=False,
        )
        for start, length in children:
            cursor = QTextCursor(doc)
            cursor.setPosition(start)
            cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextBlockFormat()
            fmt.setLeftMargin(20)
            cursor.mergeBlockFormat(fmt)
        for start, length, url in links:
            cursor = QTextCursor(doc)
            cursor.setPosition(start)
            cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setAnchor(True)
            fmt.setAnchorHref(url)
            cursor.mergeCharFormat(fmt)
        return doc

    def _render_one(self, item: _Item, *, group: list[_Item] | None = None) -> None:
        hidden = item.kind == "reasoning" and (
            not self._reasoning_visible
            or (item.entry is not None and item.entry.completed is False)
        )
        if not item.text or hidden:
            if id(item) in self._rendered:
                self._edit(item, None, [])
            item.displayed = ""
            return
        if group is not None:
            doc = self._group_document(group)
            self._edit(item, doc, block_snapshot(doc))
            doc.deleteLater()
            return
        source, heading = self._presentation(item)
        color = self._phase_color(item) or self.palette().color(QPalette.ColorRole.Text)
        background = self.palette().color(QPalette.ColorRole.AlternateBase)
        if item.kind == "assistant":
            self._parser.enqueue(
                ParseJob(
                    id(item),
                    self._revisions.get(id(item), 0),
                    source,
                    heading,
                    QFont(self.font()),
                    color,
                    background,
                )
            )
            return
        doc = formatted_document(source, heading, self.font(), color, background, markdown=False)
        if item.kind == "activity":
            cursor = QTextCursor(doc)
            cursor.movePosition(
                QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor
            )
            fmt = QTextCharFormat()
            fmt.setAnchor(True)
            fmt.setAnchorHref("gigas-detail:" + quote(self._key(item), safe=""))
            fmt.setForeground(self.palette().color(QPalette.ColorRole.PlaceholderText))
            fmt.setFontPointSize(self.font().pointSizeF() * 0.9)
            cursor.mergeCharFormat(fmt)
        if item.emphasis:
            body_start = _utf16_length(heading) + 1 if heading else 0
            cursor = QTextCursor(doc)
            fmt = QTextCharFormat()
            fmt.setFontWeight(QFont.Weight.Bold)
            for offset, length in item.emphasis:
                cursor.setPosition(body_start + offset)
                cursor.setPosition(body_start + offset + length, QTextCursor.MoveMode.KeepAnchor)
                cursor.mergeCharFormat(fmt)
        self._edit(item, doc, block_snapshot(doc))
        doc.deleteLater()

    def _parsed(self, result: Parsed) -> None:
        item = next((item for item in self._items.values() if id(item) == result.job.key), None)
        if item is None or result.job.revision != self._revisions.get(id(item), 0):
            return
        self._edit(item, result.document, result.blocks)
        if item.entry is not None and item.entry.completed is not False:
            self._rendered[id(item)].blocks = []

    def _control_anchor(self, item: _Item) -> QTextCursor:
        rendered = self._rendered.get(id(item))
        return rendered.frame.lastCursorPosition() if rendered else QTextCursor(self.document())

    def _control_margin(self) -> int:
        return int(self.document().documentMargin()) + 12

    def _reserve_control_space(self, item: _Item, spacing: int) -> None:
        rendered = self._rendered.get(id(item))
        if rendered is not None:
            fmt = rendered.frame.frameFormat()
            if fmt.bottomMargin() != 8 + spacing:
                fmt.setBottomMargin(8 + spacing)
                rendered.frame.setFrameFormat(fmt)

    def _edit(self, item: _Item, parsed: "QTextDocument | None", snapshot: Blocks) -> None:
        doc = self.document()
        selected = self.textCursor()
        anchor, position = selected.anchor(), selected.position()
        bar = self.verticalScrollBar()
        following = bar.value() == bar.maximum() and not selected.hasSelection()
        visible = self.cursorForPosition(QPoint(0, 1))
        visible_position, visible_y = visible.position(), self.cursorRect(visible).top()
        old_length = doc.characterCount()
        previous = self._rendered.get(id(item))
        new_text = parsed.toRawText() if parsed is not None else ""
        complex_document = bool(parsed.rootFrame().childFrames()) if parsed is not None else False
        old_text = previous.text if previous is not None else ""
        self._laying_out_controls = True
        transaction = QTextCursor(doc)
        transaction.beginEditBlock()
        if previous is None:
            after = False
            insertion = doc.characterCount() - 1
            for stored in self._items.values():
                if after and (following_item := self._rendered.get(id(stored))) is not None:
                    insertion = following_item.frame.firstPosition() - 1
                    break
                after |= stored is item
            transaction.setPosition(insertion)
            fmt = QTextFrameFormat()
            fmt.setLeftMargin(12)
            fmt.setRightMargin(8)
            fmt.setTopMargin(4)
            fmt.setBottomMargin(8)
            if item.kind in {"user", "request", "answer"}:
                fmt.setBackground(self.palette().brush(QPalette.ColorRole.AlternateBase))
            frame = transaction.insertFrame(fmt)
            for boundary in (frame.firstPosition() - 1, frame.lastPosition() + 1):
                gap = QTextCursor(doc)
                gap.setPosition(boundary)
                gap_format = QTextBlockFormat()
                gap_format.setLineHeight(1, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
                gap.setBlockFormat(gap_format)
            start, end = frame.firstPosition(), frame.lastPosition()
        else:
            frame = previous.frame
            start, end = frame.firstPosition(), frame.lastPosition()
            insertion = start - 1
        left, right = start, end
        if parsed is None:
            left, right = start - 1, end + 1
            edit = QTextCursor(doc)
            edit.setPosition(left)
            edit.setPosition(right, QTextCursor.MoveMode.KeepAnchor)
            edit.removeSelectedText()
            self._rendered.pop(id(item), None)
            item.displayed = ""
        else:
            fragment = None
            first_format = parsed.begin().blockFormat()
            if previous and previous.blocks and not previous.complex and not complex_document:
                prefix = 0
                limit = min(len(previous.blocks), len(snapshot))
                while prefix < limit and previous.blocks[prefix][1] == snapshot[prefix][1]:
                    prefix += 1
                if prefix == len(previous.blocks) == len(snapshot):
                    left = right = end
                    fragment = QTextDocumentFragment()
                else:
                    prefix = max(0, prefix - 1)
                    while prefix and (
                        previous.blocks[prefix][1][2] is not None
                        or snapshot[prefix][1][2] is not None
                    ):
                        prefix -= 1
                    left = start + previous.blocks[prefix][0]
                    temp = QTextCursor(parsed)
                    temp.setPosition(snapshot[prefix][0])
                    first_format = temp.blockFormat()
                    temp.setPosition(_utf16_length(new_text), QTextCursor.MoveMode.KeepAnchor)
                    fragment = QTextDocumentFragment(temp)
            if fragment is None:
                fragment = QTextDocumentFragment(parsed)
            edit = QTextCursor(doc)
            edit.setPosition(left)
            edit.setPosition(right, QTextCursor.MoveMode.KeepAnchor)
            if left != right or not fragment.isEmpty():
                edit.insertFragment(fragment)
                first = QTextCursor(doc)
                first.setPosition(left)
                first.setBlockFormat(first_format)
            color = self._phase_color(item)
            self._rendered[id(item)] = _Rendered(frame, new_text, snapshot, complex_document, color)
            item.displayed = new_text
        transaction.endEditBlock()
        self._laying_out_controls = False
        self._layout_controls()
        delta = doc.characterCount() - old_length

        def moved(value: int) -> int:
            if previous is None:
                return value + (delta if value >= insertion and insertion < old_length - 1 else 0)
            if parsed is None:
                return value if value < left else value + delta if value > right else left
            if value < start:
                return value
            if value > end:
                return value + delta
            return start + _mapped_offset(old_text, new_text, value - start)

        selected.setPosition(min(moved(anchor), doc.characterCount() - 1))
        selected.setPosition(
            min(moved(position), doc.characterCount() - 1), QTextCursor.MoveMode.KeepAnchor
        )
        if parsed is None and min(anchor, position) < right and max(anchor, position) > left:
            selected.setPosition(left)
        self.setTextCursor(selected)
        visible.setPosition(min(moved(visible_position), doc.characterCount() - 1))
        layout = doc.documentLayout()
        if layout is not None:
            layout.documentSize()
        bar.setValue(
            bar.maximum() if following else bar.value() + self.cursorRect(visible).top() - visible_y
        )
        self._position_controls()
        self.viewport().update()

    def line_rects(self) -> list[tuple[QRectF, QColor]]:
        """Return visible phase rules in viewport coordinates."""
        layout = self.document().documentLayout()
        if layout is None:
            return []
        result = []
        scroll = self.verticalScrollBar().value()
        for rendered in self._rendered.values():
            if rendered.color is None:
                continue
            rect = layout.frameBoundingRect(rendered.frame)
            top, bottom = rect.top() - scroll + 7, rect.bottom() - scroll - 9
            if bottom >= 0 and top <= self.viewport().height():
                result.append(
                    (QRectF(rect.left() + 2, top, 2, max(0, bottom - top)), rendered.color)
                )
        return result

    def paintEvent(self, event: "QPaintEvent") -> None:  # noqa: N802
        """Paint phase rules beside native frames without covering text selection."""
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        painter.setClipRect(event.rect())
        for rect, color in self.line_rects():
            painter.setPen(QPen(color, 2))
            painter.drawLine(rect.topLeft(), rect.bottomLeft())
        painter.end()

    def resizeEvent(self, event: "QResizeEvent") -> None:  # noqa: N802
        """Reflow frames and keep compact activity targets within the current width."""
        super().resizeEvent(event)
        for item in self._items.values():
            if item.kind == "activity":
                self._render_item(item)
