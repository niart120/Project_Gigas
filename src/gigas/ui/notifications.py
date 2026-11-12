"""Bounded recent-failure history independent of service and shutdown state."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gigas.ui.elided_label import ElidedLabel
from gigas.ui.geometry import fit_to_screen

type NoticeSource = Literal["settings", "video", "input", "codex", "recording", "shutdown"]

_SOURCES: dict[NoticeSource, str] = {
    "settings": "設定",
    "video": "映像",
    "input": "入力",
    "codex": "Codex",
    "recording": "記録",
    "shutdown": "終了",
}


@dataclass(frozen=True)
class Notice:
    """One display-safe failure snapshot, without retry actions or live state.

    Attributes:
        source: Category whose previous failure this snapshot replaces.
        title: Short application-owned failure label for the banner.
        explanation: Confirmed impact and available recovery route, in plain text.
        details: Display-safe diagnostic text preserved for selection and copying.
    """

    source: NoticeSource
    title: str
    explanation: str
    details: str


class NoticeDialog(QDialog):
    """Show a frozen, copyable failure until an explicit selection refreshes it."""

    def __init__(self, parent: QWidget) -> None:
        """Create a modeless detail view without opening it."""
        super().__init__(parent)
        self.setWindowTitle("最近の失敗")
        self.resize(620, 420)
        self.notice: Notice | None = None
        layout = QVBoxLayout(self)
        self.sources = QComboBox(self)
        layout.addWidget(self.sources)
        self.title = ElidedLabel(parent=self)
        layout.addWidget(self.title)
        self.explanation = QPlainTextEdit(self)
        self.explanation.setReadOnly(True)
        self.explanation.setMaximumHeight(100)
        layout.addWidget(self.explanation)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        close = QPushButton("閉じる", self)
        close.setAutoDefault(False)
        close.clicked.connect(self.close)
        actions.addWidget(close)
        layout.addLayout(actions)

    def display(self, notice: Notice) -> None:
        """Replace the displayed snapshot only after an explicit user action."""
        self.notice = notice
        self.sources.setCurrentIndex(self.sources.findData(notice.source))
        self.title.setText(notice.title)
        self.explanation.setPlainText(notice.explanation)
        self.details.setPlainText(notice.details)


class NotificationCenter(QObject):
    """Keep the latest failure per source; dismissal never changes service state.

    Producers suppress repeated observations of one failure before publishing.
    A later success does not remove history. Publishing never opens a dialog,
    changes its displayed snapshot, or takes focus from the current operation.

    Args:
        parent: Window owning the banner, menu action, and optional detail view.
    """

    published = Signal(object)

    def __init__(self, parent: QWidget) -> None:
        """Prepare hidden presentation widgets without connecting service actions."""
        super().__init__(parent)
        self._owner = parent
        self._notices: dict[NoticeSource, Notice] = {}
        self._latest: NoticeSource | None = None
        self.dialog: NoticeDialog | None = None
        self.banner = QFrame(parent)
        self.banner.setFrameShape(QFrame.Shape.StyledPanel)
        row = QHBoxLayout(self.banner)
        row.setContentsMargins(8, 3, 8, 3)
        self._title = ElidedLabel(parent=self.banner)
        row.addWidget(self._title, 1)
        details = QPushButton("詳細…", self.banner)
        details.clicked.connect(self._show_latest)
        row.addWidget(details)
        self.dismiss_button = QPushButton("閉じる", self.banner)
        self.dismiss_button.clicked.connect(self.banner.hide)
        row.addWidget(self.dismiss_button)
        self.banner.hide()
        self.entry_action = QAction("通知…", self)
        self.entry_action.setEnabled(False)
        self.entry_action.triggered.connect(self._show_latest)

    @property
    def notices(self) -> Mapping[NoticeSource, Notice]:
        """Return the latest immutable failure per source, not unresolved incidents."""
        return MappingProxyType(self._notices)

    @Slot(object)
    def publish(self, notice: object) -> None:
        """Retain a typed failure and show its banner without interrupting copying."""
        if not isinstance(notice, Notice):
            return
        self._notices[notice.source] = notice
        self._latest = notice.source
        self._title.setText(f"{_SOURCES[notice.source]} · {notice.title}")
        self.entry_action.setEnabled(True)
        self.banner.show()
        self._sync_sources()
        self.published.emit(notice)

    def show_details(
        self, source: NoticeSource | None = None, *, parent: QWidget | None = None
    ) -> None:
        """Explicitly open or refresh a source, defaulting to the newest failure."""
        selected = self._latest if source is None else source
        if selected not in self._notices:
            return
        self.show_snapshot(self._notices[selected], parent=parent)

    def show_snapshot(self, notice: Notice, *, parent: QWidget | None = None) -> None:
        """Open a specific failure without publishing or replacing recent history.

        Args:
            notice: Immutable failure associated with the explicitly chosen UI action.
                It need not appear in recent history. Later publications leave this
                displayed snapshot and any text selection unchanged.
            parent: Active settings dialog, if opening details from a modal editor.
        """
        if self.dialog is None:
            self.dialog = NoticeDialog(self._owner)
            self.dialog.sources.activated.connect(self._select_source)
        owner = self._owner if parent is None else parent
        if self.dialog.parentWidget() is not owner:
            self.dialog.setParent(owner, self.dialog.windowFlags())
        self.dialog.setWindowModality(
            Qt.WindowModality.NonModal if parent is None else Qt.WindowModality.WindowModal
        )
        self._sync_sources()
        if self.dialog.sources.findData(notice.source) < 0:
            self.dialog.sources.addItem(_SOURCES[notice.source], notice.source)
        self.dialog.display(notice)
        self.dialog.show()
        fit_to_screen(self.dialog)
        self.dialog.raise_()
        self.dialog.activateWindow()

    def close_details(self) -> None:
        """Hide the owned modeless window after application shutdown is confirmed."""
        if self.dialog is not None:
            self.dialog.close()

    def _sync_sources(self) -> None:
        if self.dialog is not None:
            for source, label in _SOURCES.items():
                if source in self._notices and self.dialog.sources.findData(source) < 0:
                    self.dialog.sources.addItem(label, source)

    @Slot()
    def _show_latest(self) -> None:
        self.show_details()

    def _select_source(self, index: int) -> None:
        if self.dialog is not None:
            source = self.dialog.sources.itemData(index)
            for key, notice in self._notices.items():
                if source == key:
                    self.dialog.display(notice)
                    break
