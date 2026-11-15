"""Native Markdown parsing and bounded delivery to the GUI document."""

from collections import deque
from dataclasses import dataclass
from functools import partial
from time import perf_counter
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QFont,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextListFormat,
)
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from PySide6.QtCore import QUrl

COLORS = {"commentary": "#426b95", "partial_answer": "#735296", "final_answer": "#735296"}
DARK_COLORS = {"commentary": "#82b2e0", "partial_answer": "#c1a0e5", "final_answer": "#c1a0e5"}
LABELS = {
    "commentary": "Codex · 進捗",
    "partial_answer": "Codex · 回答",
    "final_answer": "Codex · 回答",
}
type BlockStyle = tuple[
    str, QTextBlockFormat, QTextListFormat | None, list[tuple[str, QTextCharFormat]]
]
type Blocks = list[tuple[int, BlockStyle]]


class ResourceFreeDocument(QTextDocument):
    """A document that never resolves embedded files or remote resources."""

    def loadResource(self, resource_type: int, url: "QUrl | str") -> None:  # noqa: N802
        """Leave image resources unresolved in both parsing and display."""
        del resource_type, url


def block_snapshot(doc: QTextDocument) -> Blocks:
    """Capture native text and formatting for a safe unchanged-prefix comparison."""
    result: Blocks = []
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


def formatted_document(
    source: str, heading: str, font: QFont, color: QColor, background: QColor, *, markdown: bool
) -> QTextDocument:
    """Parse Markdown with Qt, retaining heading levels while limiting font sizes."""
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
    if heading:
        cursor.insertBlock()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.insertText(heading)
        listing = cursor.block().textList()
        if listing is not None:
            listing.remove(cursor.block())
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
            fmt.setBackground(background)
            fmt.setLeftMargin(8)
            fmt.setRightMargin(8)
        edit.setBlockFormat(fmt)
        if level or (heading and block == doc.begin()):
            edit.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            char = QTextCharFormat()
            char.setFontPointSize(font.pointSizeF() * (1.15 if level in (1, 2) else 1))
            char.setFontWeight(QFont.Weight.Bold)
            if heading and block == doc.begin():
                char.setForeground(color)
            edit.mergeCharFormat(char)
        block = block.next()
    return doc


@dataclass(frozen=True, slots=True)
class ParseJob:
    """Immutable source and presentation inputs for one response revision."""

    key: int
    revision: int
    source: str
    heading: str
    font: QFont
    color: QColor
    background: QColor


@dataclass(slots=True)
class Parsed:
    """Worker-owned parse transferred to the GUI thread before delivery."""

    job: ParseJob
    document: QTextDocument
    blocks: Blocks


class _Parser(QObject):
    ready = Signal(int, object)

    @Slot(int, object)
    def parse(self, generation: int, jobs: list[ParseJob]) -> None:
        results = []
        for job in jobs:
            doc = formatted_document(
                job.source, job.heading, job.font, job.color, job.background, markdown=True
            )
            snapshot = block_snapshot(doc)
            # Detach here; the receiver adopts the document on its own thread.
            # Avoid locking the GUI's event queue from this Python worker.
            # Qt supports nullptr here; PySide's stub omits the optional type.
            doc.moveToThread(None)  # ty: ignore[invalid-argument-type]
            results.append(Parsed(job, doc, snapshot))
        self.ready.emit(generation, results)


class MarkdownQueue(QObject):
    """One parser, one in-flight batch and at most one pending source per item.

    The application owns thread cleanup even if the view is destroyed first.
    Shutdown never waits on the GUI thread; the application observes stopped.
    """

    ready = Signal(object)

    def __init__(self, owner: QObject) -> None:
        """Create a lazy worker and a 50ms coalescing timer."""
        super().__init__(QApplication.instance())
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._parser = _Parser(self._pool)
        self._parser.ready.connect(self._receive)
        owner.destroyed.connect(self.shutdown)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.flush)
        self._pending: dict[int, ParseJob] = {}
        self._generation = 0
        self._busy = False
        self._closing = False
        self._next_start = 0.0

    @property
    def stopped(self) -> bool:
        """Whether no parser thread remains running after shutdown."""
        return not self._busy and self._pool.waitForDone(0)

    @property
    def idle(self) -> bool:
        """Whether all currently queued display work has been delivered."""
        return not self._busy and not self._pending

    def enqueue(self, job: ParseJob) -> None:
        """Retain the latest source without queuing every received delta."""
        if self._closing:
            return
        self._pending[job.key] = job
        if not self._busy and not self._timer.isActive():
            self._timer.start(50)

    def clear(self) -> None:
        """Invalidate in-flight results when the displayed conversation changes."""
        self._generation += 1
        self._pending.clear()
        self._timer.stop()

    def flush(self) -> None:
        """Submit a coalesced batch without blocking reception or native controls."""
        self._timer.stop()
        if self._closing or self._busy or not self._pending:
            return
        self._busy = True
        jobs = list(self._pending.values())
        self._pending.clear()
        self._next_start = perf_counter() + 0.05
        self._pool.start(partial(self._parser.parse, self._generation, jobs))

    @Slot(int, object)
    def _receive(self, generation: int, results: list[Parsed]) -> None:
        self._deliver(generation, deque(results))

    def _deliver(self, generation: int, results: deque[Parsed]) -> None:
        # Python 3.12's Windows monotonic clock has only 15.6ms resolution.
        deadline = perf_counter() + 0.008
        while results:
            result = results.popleft()
            result.document.moveToThread(self.thread())
            if generation == self._generation and not self._closing:
                self.ready.emit(result)
            result.document.deleteLater()
            if results and perf_counter() >= deadline:
                QTimer.singleShot(0, partial(self._deliver, generation, results))
                return
        self._busy = False
        if self._pending and not self._closing:
            self._timer.start(max(0, round((self._next_start - perf_counter()) * 1000)))

    @Slot()
    def shutdown(self) -> None:
        """Discard queued rendering and request asynchronous thread termination."""
        self._closing = True
        self.clear()
