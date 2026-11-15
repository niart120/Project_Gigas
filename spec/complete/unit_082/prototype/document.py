"""M1 experiment: one selectable document, native Markdown and message frames.

This module is deliberately not imported by the product. The parser is Qt's;
only presentation, replacement boundaries and viewport anchoring are custom.
"""

from dataclasses import dataclass
from difflib import SequenceMatcher
from time import perf_counter

from PySide6.QtCore import QPoint, QRectF, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextDocumentFragment,
    QTextFormat,
    QTextFrameFormat,
)
from PySide6.QtWidgets import QTextBrowser

COLORS = {"commentary": "#426b95", "final_answer": "#735296", "partial_answer": "#735296"}
LABELS = {
    "commentary": "Codex · 進捗",
    "final_answer": "Codex · 回答",
    "partial_answer": "Codex · 回答",
    "unknown": "Codex",
    "user": "あなた",
    "activity": "活動",
    "reasoning": "推論要約",
}


def units(text):
    return len(text.encode("utf-16-le")) // 2


def mapped_offset(old, new, offset):
    """Map an observed text position; only called for a cursor inside an edit.

    Fast append and format-only cases avoid a diff. For corrections the mapping
    preserves equal runs and clamps removed text to a valid Unicode boundary.
    """
    if old == new or new.startswith(old):
        return min(offset, units(new))
    count = 0
    index = 0
    for index, char in enumerate(old):  # noqa: B007 -- index is the offset after the loop
        if count >= offset:
            break
        count += units(char)
    else:
        index = len(old)
    for tag, a, b, c, d in SequenceMatcher(None, old, new, autojunk=True).get_opcodes():
        if a <= index < b or index == b == len(old):
            target = c + index - a if tag == "equal" else c + min(index - a, d - c)
            return units(new[:target])
    return units(new)


class ResourceFreeDocument(QTextDocument):
    def loadResource(self, resource_type, url):  # noqa: N802
        del resource_type, url
        return None  # noqa: RET501 -- no file/network/image resource in worker parsing


def rich_document(source, phase, font, markdown=True):
    """Parse first, then apply application typography without losing headings."""
    doc = ResourceFreeDocument()
    doc.setUndoRedoEnabled(False)
    doc.setDefaultFont(font)
    doc.setDocumentMargin(0)
    if markdown:
        doc.setMarkdown(
            source,
            QTextDocument.MarkdownFeature.MarkdownNoHTML
            | QTextDocument.MarkdownFeature.MarkdownDialectGitHub,
        )
    else:
        doc.setPlainText(source)
    cursor = QTextCursor(doc)
    if phase != "activity":
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.insertBlock()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.insertText(LABELS.get(phase, LABELS["unknown"]))
        if cursor.block().textList():
            cursor.block().textList().remove(cursor.block())
        cursor.setBlockFormat(QTextBlockFormat())
    base = QTextCharFormat()
    base.setFontPointSize(font.pointSizeF())
    base.setProperty(QTextFormat.Property.FontSizeAdjustment, 0)
    cursor.select(QTextCursor.SelectionType.Document)
    cursor.mergeCharFormat(base)
    block = doc.begin()
    while block.isValid():
        level = block.blockFormat().headingLevel()
        edit = QTextCursor(block)
        fmt = block.blockFormat()
        fmt.setTopMargin(8 if level else 3)
        fmt.setBottomMargin(4 if level else 3)
        if fmt.hasProperty(QTextFormat.Property.BlockCodeFence):
            fmt.setBackground(QColor("#f0f1f3"))
            fmt.setLeftMargin(8)
            fmt.setRightMargin(8)
        edit.setBlockFormat(fmt)
        if level or (block == doc.begin() and phase != "activity"):
            edit.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            char = QTextCharFormat()
            char.setFontPointSize(font.pointSizeF() * (1.15 if level in (1, 2) else 1))
            char.setFontWeight(QFont.Weight.Bold)
            if block == doc.begin():
                char.setForeground(QColor(COLORS.get(phase, "#444444")))
            edit.mergeCharFormat(char)
        block = block.next()
    return doc


def blocks(doc):
    """Snapshot native layout inputs, not just text: closing markup changes style."""
    result = []
    block = doc.begin()
    while block.isValid():
        fragments = []
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            fragments.append((fragment.text(), fragment.charFormat()))
            iterator += 1
        listing = block.textList()
        result.append(
            (
                block.position(),
                (
                    block.text(),
                    block.blockFormat(),
                    listing.format() if listing else None,
                    fragments,
                ),
            )
        )
        block = block.next()
    return result


@dataclass
class Message:
    frame: object
    source: str
    rendered: str
    phase: str
    markdown: bool
    blocks: list
    complex: bool


class MessageView(QTextBrowser):
    """Keep native cross-message selection while replacing one message frame."""

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setUndoRedoEnabled(False)
        self.setFont(QFont("Meiryo", 10.5))
        self.document().setDefaultFont(self.font())
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.messages = {}
        self.last_cost = {}
        self.painted_lines = []
        self.pending = {}
        self.completed = set()
        self.generation = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.flush)
        self.interval_ms = 50
        self.incremental = True

    def reset(self):
        self.timer.stop()
        self.pending.clear()
        self.completed.clear()
        self.messages.clear()
        self.generation += 1
        self.clear()

    def enqueue(self, key, delta, *, generation=None, phase="unknown"):
        if generation is not None and generation != self.generation:
            return
        if key in self.completed:
            return
        source, previous_phase = self.pending.get(
            key,
            (self.messages[key].source, self.messages[key].phase)
            if key in self.messages
            else ("", phase),
        )
        self.pending[key] = source + delta, previous_phase
        if not self.timer.isActive():
            self.timer.start(self.interval_ms)

    def flush(self):
        self.timer.stop()
        pending, self.pending = self.pending, {}
        for key, (source, phase) in pending.items():
            self.put(key, source, phase)

    def finish(self, key, source, phase="final_answer"):
        self.pending.pop(key, None)
        self.put(key, source, phase)
        self.freeze(key)
        if not self.pending:
            self.timer.stop()

    def freeze(self, key):
        # Item completion, not phase, makes the parser comparison cache obsolete.
        self.messages[key].blocks = None
        self.completed.add(key)

    def put(
        self, key, source, phase="final_answer", *, markdown=True, prepend=False, prepared=None
    ):
        started = perf_counter()
        parsed, new_blocks = (
            prepared if prepared is not None else self.prepare(source, phase, markdown)
        )
        complex_document = bool(parsed.rootFrame().childFrames())
        parsed_at = perf_counter()
        new_text = parsed.toRawText()
        selected = self.textCursor()
        anchor, position = selected.anchor(), selected.position()
        bar = self.verticalScrollBar()
        follow = bar.value() == bar.maximum() and not selected.hasSelection()
        visible = self.cursorForPosition(QPoint(30, 4))
        visible_position, visible_y = visible.position(), self.cursorRect(visible).top()
        doc = self.document()
        old_length = doc.characterCount()
        creating = key not in self.messages
        insertion_position = None
        transaction = QTextCursor(doc)
        transaction.beginEditBlock()
        if key in self.messages:
            item = self.messages[key]
            frame = item.frame
            start, end = frame.firstPosition(), frame.lastPosition()
            old_text = item.rendered
        else:
            cursor = QTextCursor(doc)
            cursor.movePosition(
                QTextCursor.MoveOperation.Start if prepend else QTextCursor.MoveOperation.End
            )
            insertion_position = cursor.position()
            fmt = QTextFrameFormat()
            fmt.setLeftMargin(12)
            fmt.setRightMargin(8)
            fmt.setTopMargin(4)
            fmt.setBottomMargin(8)
            if phase == "user":
                fmt.setBackground(QColor("#f4f4f4"))
            frame = cursor.insertFrame(fmt)
            for boundary in (frame.firstPosition() - 1, frame.lastPosition() + 1):
                gap = QTextCursor(doc)
                gap.setPosition(boundary)
                gap_format = gap.blockFormat()
                gap_format.setLineHeight(1, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
                gap_format.setTopMargin(0)
                gap_format.setBottomMargin(0)
                gap.setBlockFormat(gap_format)
            start, end = frame.firstPosition(), frame.lastPosition()
            old_text = ""
        left, right = start, end
        fragment = None
        first_format = parsed.begin().blockFormat()
        if (
            key in self.messages
            and item.blocks
            and self.incremental
            and not item.complex
            and not complex_document
        ):
            prefix = 0
            limit = min(len(item.blocks), len(new_blocks))
            while prefix < limit and item.blocks[prefix][1] == new_blocks[prefix][1]:
                prefix += 1
            if prefix == len(item.blocks) == len(new_blocks):
                left = right = end
                fragment = QTextDocumentFragment()
            else:
                # Include the preceding block: a fragment starting at the end
                # cannot create the separator between an old and a new block.
                prefix = max(0, prefix - 1)
                # A list must keep its group identity and numbering. Start at a
                # preceding ordinary block and replace through the message end.
                while prefix and (
                    item.blocks[prefix][1][2] is not None or new_blocks[prefix][1][2] is not None
                ):
                    prefix -= 1
                left = start + (
                    item.blocks[prefix][0] if prefix < len(item.blocks) else units(old_text)
                )
                right = end
                temp = QTextCursor(parsed)
                temp.setPosition(
                    new_blocks[prefix][0] if prefix < len(new_blocks) else units(new_text)
                )
                first_format = temp.blockFormat()
                temp.setPosition(units(new_text), QTextCursor.MoveMode.KeepAnchor)
                fragment = QTextDocumentFragment(temp)
        if fragment is None:
            fragment = QTextDocumentFragment(parsed)
        cursor = frame.firstCursorPosition()
        cursor.setPosition(left)
        cursor.setPosition(right, QTextCursor.MoveMode.KeepAnchor)
        if left != right or not fragment.isEmpty():
            cursor.insertFragment(fragment)
            first = QTextCursor(doc)
            first.setPosition(left)
            first.setBlockFormat(first_format)
        new_end = frame.lastPosition()
        delta = new_end - end

        def moved(value):
            if creating:
                return value + (
                    doc.characterCount() - old_length
                    if prepend and value >= insertion_position
                    else 0
                )
            if value < start:
                return value
            if value > end:
                return value + delta
            return start + mapped_offset(old_text, new_text, value - start)

        self.messages[key] = Message(
            frame, source, new_text, phase, markdown, new_blocks, complex_document
        )
        replaced_at = perf_counter()
        transaction.endEditBlock()
        doc.documentLayout().documentSize()
        selected.setPosition(min(moved(anchor), doc.characterCount() - 1))
        selected.setPosition(
            min(moved(position), doc.characterCount() - 1), QTextCursor.MoveMode.KeepAnchor
        )
        self.setTextCursor(selected)
        visible.setPosition(min(moved(visible_position), doc.characterCount() - 1))
        bar.setValue(
            bar.maximum() if follow else bar.value() + self.cursorRect(visible).top() - visible_y
        )
        self.viewport().update()
        finished = perf_counter()
        self.last_cost = {
            "parse_style_ms": (parsed_at - started) * 1000,
            "replace_ms": (replaced_at - parsed_at) * 1000,
            "layout_anchor_ms": (finished - replaced_at) * 1000,
            "total_ms": (finished - started) * 1000,
        }

    def prepare(self, source, phase, markdown=True):
        parsed = rich_document(source, phase, self.font(), markdown)
        return parsed, blocks(parsed)

    def loadResource(self, resource_type, url):  # noqa: N802
        # Match the existing no-external-resource policy; images are out of M1 scope.
        del resource_type, url
        return None  # noqa: RET501 -- Qt virtual resource hook returns an empty QVariant

    def line_rects(self):
        layout = self.document().documentLayout()
        scroll = self.verticalScrollBar().value()
        result = []
        for key, item in self.messages.items():
            if item.phase not in COLORS:
                continue
            rect = layout.frameBoundingRect(item.frame)
            top = rect.top() - scroll + 7
            bottom = rect.bottom() - scroll - 9
            if bottom >= 0 and top <= self.viewport().height():
                result.append(
                    (key, QRectF(rect.left() + 2, top, 2, bottom - top), QColor(COLORS[item.phase]))
                )
        return result

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        self.painted_lines = self.line_rects()
        painter = QPainter(self.viewport())
        painter.setClipRect(event.rect())
        for _, rect, color in self.painted_lines:
            painter.setPen(QPen(color, 2))
            painter.drawLine(rect.topLeft(), rect.bottomLeft())
        painter.end()

    def resizeEvent(self, event):  # noqa: N802
        bar = self.verticalScrollBar()
        follow = bar.value() == bar.maximum() and not self.textCursor().hasSelection()
        visible = self.cursorForPosition(QPoint(30, 4))
        y = self.cursorRect(visible).top()
        super().resizeEvent(event)
        self.document().documentLayout().documentSize()
        bar.setValue(bar.maximum() if follow else bar.value() + self.cursorRect(visible).top() - y)
