"""Latest public reasoning paragraph shown independently of conversation history."""

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import QLabel, QTextBrowser, QVBoxLayout, QWidget

if TYPE_CHECKING:
    from gigas.runtime.codex.models import ReasoningEvent


class ReasoningPanel(QWidget):
    """Show a fixed three-line tail, with no replay queue or invented reasoning."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty subtitle without taking ownership of turn execution."""
        super().__init__(parent)
        self._turn: str | None = None
        self._active = False
        self._items: list[str] = []
        self._latest = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush)
        self.label = QLabel("推論", self)
        self.text = QTextBrowser(self)
        self.text.setAccessibleName("推論")
        self.text.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.text.document().setDocumentMargin(0)
        self.text.setOpenLinks(False)
        self.text.setOpenExternalLinks(False)
        self.text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scrollbar = self.text.verticalScrollBar()
        scrollbar.rangeChanged.connect(lambda _minimum, maximum: scrollbar.setValue(maximum))
        self.text.setFixedHeight(self.text.fontMetrics().lineSpacing() * 3)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        layout.addWidget(self.text)
        self.setFixedHeight(self.sizeHint().height())

    def start(self, turn_id: str | None) -> None:
        """Clear the previous subtitle for a new turn or selected conversation."""
        self._turn = turn_id
        self._active = turn_id is not None
        self._items.clear()
        self._timer.stop()
        self._latest = ""
        self.text.clear()
        self.label.setText("推論")

    def receive(self, event: "ReasoningEvent") -> None:
        """Replace the latest nonempty public paragraph of the newest reasoning item."""
        if not self._active or event.turn_id != self._turn:
            return
        nonempty = [part for part in event.summary if part.text]
        # An item with no public summary does not retract the preceding item's text.
        # An empty completion can still correct an item that supplied a subtitle.
        if not nonempty and (not event.completed or event.item_id not in self._items):
            return
        if event.item_id not in self._items:
            self._items.append(event.item_id)
        if event.item_id != self._items[-1]:
            return
        self._latest = max(nonempty, key=lambda part: part.index).text if nonempty else ""
        if not self._timer.isActive():
            self._timer.start(50)

    def _flush(self) -> None:
        self._timer.stop()
        self.text.setPlainText(self._latest)
        cursor = QTextCursor(self.text.document())
        cursor.select(QTextCursor.SelectionType.Document)
        fmt = QTextBlockFormat()
        fmt.setLineHeight(
            self.text.fontMetrics().lineSpacing(),
            QTextBlockFormat.LineHeightTypes.FixedHeight.value,
        )
        cursor.mergeBlockFormat(fmt)
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())

    def finish(self, *, interrupted: bool = False) -> None:
        """Retain the final subtitle while removing the impression of live updates."""
        if self._turn is not None:
            self._flush()
            self._active = False
            self.label.setText("推論 · 中断" if interrupted else "推論 · 終了")
