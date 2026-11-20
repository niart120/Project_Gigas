"""Archived chat list and retention preference for the settings window."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gigas.storage.settings import AUTO_ARCHIVE_DAYS
from gigas.ui.conversation_sidebar import thread_title

if TYPE_CHECKING:
    from gigas.runtime.codex.management_models import ThreadPage


class ArchivedChats(QWidget):
    """Display one server page at a time, emitting intent without opening a chat."""

    page_requested = Signal(object)
    restore_requested = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        """Create the retention selector, list, and actions."""
        super().__init__(parent)
        self._connected = False
        self._busy = False
        self._can_restore = False
        self._needs_page = True
        self._has_page = False
        self._cursor: str | None = None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.days = QComboBox(self)
        for days in AUTO_ARCHIVE_DAYS:
            self.days.addItem(f"{days}日" if days else "無効", days)
        form.addRow("自動アーカイブ", self.days)
        layout.addLayout(form)
        self.rows = QTreeWidget(self)
        self.rows.setAccessibleName("アーカイブ済みチャット")
        self.rows.setHeaderLabels(["チャット", "所属", "最終更新"])
        self.rows.setRootIsDecorated(False)
        self.rows.setUniformRowHeights(True)
        self.rows.header().setStretchLastSection(False)
        self.rows.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.rows.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.rows.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.rows.itemSelectionChanged.connect(self._render)
        layout.addWidget(self.rows, 1)
        actions = QHBoxLayout()
        self.refresh = QPushButton("一覧更新", self)
        self.more = QPushButton("続きを表示", self)
        self.restore = QPushButton("復元", self)
        for button in (self.refresh, self.more, self.restore):
            button.setAutoDefault(False)
        self.status = QLabel(self)
        self.refresh.clicked.connect(lambda: self.page_requested.emit(None))
        self.more.clicked.connect(lambda: self.page_requested.emit(self._cursor))
        self.restore.clicked.connect(self._restore)
        actions.addWidget(self.refresh)
        actions.addWidget(self.more)
        actions.addWidget(self.status)
        actions.addStretch(1)
        actions.addWidget(self.restore)
        layout.addLayout(actions)
        self._render()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 -- Qt override
        """Refresh the first page when the user opens the archive tab."""
        super().showEvent(event)
        self._needs_page = True
        self._request_initial()

    def _request_initial(self) -> None:
        if self.isVisible() and self._needs_page and self._connected and not self._busy:
            self._needs_page = False
            self.page_requested.emit(None)

    def set_availability(self, connected: bool, *, busy: bool, can_restore: bool) -> None:
        """Apply current connection, request, and chat-management admission."""
        if self._connected and not connected:
            self.rows.clear()
            self._cursor = None
            self._has_page = False
            self._needs_page = True
        self._connected = connected
        self._busy = busy
        self._can_restore = can_restore
        self._render()
        self._request_initial()

    def set_page(self, page: "ThreadPage", *, append: bool, project_names: dict[str, str]) -> None:
        """Show the page in server order, updating overlapping IDs in place."""
        if not append:
            self.rows.clear()
        rows = {
            row.data(0, Qt.ItemDataRole.UserRole): row
            for index in range(self.rows.topLevelItemCount())
            if (row := self.rows.topLevelItem(index)) is not None
        }
        for thread in page.threads:
            item = rows.get(thread.id)
            if item is None:
                item = QTreeWidgetItem(self.rows)
                item.setData(0, Qt.ItemDataRole.UserRole, thread.id)
                rows[thread.id] = item
            item.setText(0, thread_title(thread))
            project = thread.project_id
            item.setText(
                1, project_names.get(project, project) if project is not None else "未所属"
            )
            updated = QDateTime.fromSecsSinceEpoch(thread.updated_at).toLocalTime()
            item.setText(2, updated.toString("yyyy/MM/dd HH:mm"))
        self._cursor = page.next_cursor
        self._has_page = True
        self._render()

    def _restore(self) -> None:
        selected = self.rows.selectedItems()
        if selected and self.restore.isEnabled():
            self.restore_requested.emit(selected[0].data(0, Qt.ItemDataRole.UserRole))

    def _render(self) -> None:
        available = self._connected and not self._busy
        self.refresh.setEnabled(available)
        self.more.setVisible(self._cursor is not None)
        self.more.setEnabled(available)
        self.restore.setEnabled(available and self._can_restore and bool(self.rows.selectedItems()))
        if not self._connected:
            status = "Codex未接続"
        elif self._busy:
            status = "処理中"
        elif self._has_page and not self.rows.topLevelItemCount() and self._cursor is None:
            status = "チャットはありません"
        else:
            status = ""
        self.status.setText(status)
