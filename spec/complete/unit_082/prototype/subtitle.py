"""Public summary presentation independent of the conversation document."""

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QTextBrowser, QVBoxLayout, QWidget


class Subtitle(QWidget):
    def __init__(self):
        super().__init__()
        self.turn = None
        self.parts = {}
        self.items = []
        self.current = None
        self.active = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel("推論")
        self.label.setFont(QFont("Meiryo", 10.5, QFont.Weight.Bold))
        layout.addWidget(self.label)
        self.text = QTextBrowser()
        self.text.setFont(QFont("Meiryo", 10.5))
        self.text.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.text.setFixedHeight(self.text.fontMetrics().lineSpacing() * 3 + 10)
        self.text.setVerticalScrollBarPolicy(self.text.verticalScrollBarPolicy().ScrollBarAlwaysOff)
        layout.addWidget(self.text)
        self.setFixedHeight(self.sizeHint().height())

    def start(self, turn):
        self.turn, self.active = turn, True
        self.parts.clear()
        self.items.clear()
        self.current = None
        self.text.clear()
        self.label.setText("推論")

    def delta(self, turn, item, index, text):
        if not self.active or turn != self.turn or not text:
            return
        if item not in self.items:
            self.items.append(item)
        key = item, index
        self.parts[key] = self.parts.get(key, "") + text
        latest_item = self.items[-1]
        latest_index = max(i for ident, i in self.parts if ident == latest_item)
        self.current = latest_item, latest_index
        self.text.setPlainText(self.parts[self.current])
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())

    def boundary(self, turn, item, index):
        # No blank flash: a new part becomes visible only when it contains text.
        pass

    def complete_item(self, turn, item, paragraphs):
        if not self.active or turn != self.turn:
            return
        self.parts = {key: value for key, value in self.parts.items() if key[0] != item}
        if item not in self.items:
            self.items.append(item)
        for index, paragraph in enumerate(paragraphs):
            self.parts[item, index] = paragraph
        if item == self.items[-1]:
            self.current = (item, len(paragraphs) - 1) if paragraphs else None
            self.text.setPlainText(paragraphs[-1] if paragraphs else "")
            self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())

    def finish(self, turn, status="終了"):
        if turn == self.turn:
            self.active = False
            self.label.setText(f"推論 · {status}")
