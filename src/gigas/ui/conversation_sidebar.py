"""Project and conversation identifiers without service or execution ownership."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionToolButton,
    QStylePainter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gigas.ui.elided_label import ElidedLabel

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent

    from gigas.runtime.codex.management_models import (
        ProjectInfo,
        ProjectPage,
        ThreadInfo,
        ThreadPage,
    )


def thread_title(thread: "ThreadInfo") -> str:
    """Return a single-line name, preview, or fallback without changing metadata.

    Whitespace is collapsed for display. A name containing only whitespace is
    treated as empty, followed by the preview and then the unnamed label.
    """
    name = " ".join((thread.name or "").split())
    preview = " ".join(thread.preview.split())
    return name or preview or "名前のない会話"


class ConversationHeading(QWidget):
    """Show the current assignment and title independently of sidebar visibility."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create a compact heading; the caller decides when a target is known."""
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.project = ElidedLabel(parent=self)
        self.title = ElidedLabel(parent=self)
        self.loading = QLabel("読込中", self)
        self.loading.hide()
        row = QHBoxLayout()
        row.addWidget(self.title, 1)
        row.addWidget(self.loading)
        layout.addWidget(self.project)
        layout.addLayout(row)

    def set_target(self, project: str, title: str, *, loading: bool = False) -> None:
        """Display supplied identifiers without selecting or fetching a conversation.

        Args:
            project: Public Project name or the caller's unassigned label.
            title: Public conversation name or the caller's new-conversation label.
            loading: Whether the candidate's initial history is being fetched.
        """
        self.project.setText(project)
        self.title.setText(title)
        self.loading.setVisible(loading)


class _GroupButton(QToolButton):
    """Keep a complete accessible identifier while eliding its painted text."""

    def paintEvent(self, event: "QPaintEvent") -> None:  # noqa: N802 -- Qt override
        del event
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        text = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, max(0, self.width() - 32)
        )
        option.text = text.replace("&", "&&")
        # Expansion is not conversation selection. Preserve the checked semantics
        # while painting an ordinary heading at the left of its full hit target.
        option.state &= ~QStyle.StateFlag.State_On
        option.rect.setWidth(min(self.width(), self.fontMetrics().horizontalAdvance(text) + 32))
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)


class _ConversationGroup(QWidget):
    """Display one assignment and its fetched rows, with separate local loading."""

    opened = Signal()
    new_requested = Signal()
    thread_requested = Signal(str)
    more_requested = Signal(str)

    def __init__(self, name: str, parent: QWidget | None) -> None:
        super().__init__(parent)
        self._enabled = False
        self._listing_enabled = False
        self._loading = False
        self._cursor: str | None = None
        self._selected: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.heading = _GroupButton(self)
        self.heading.setCheckable(True)
        self.heading.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.heading.setArrowType(Qt.ArrowType.RightArrow)
        self.heading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.heading.setAutoRaise(True)
        self.new_button = QToolButton(self)
        self.new_button.setText("+")
        self.new_button.setAccessibleName("新しい会話")
        self.new_button.setAutoRaise(True)
        self.new_button.clicked.connect(self.new_requested)
        row.addWidget(self.heading, 1)
        row.addWidget(self.new_button)
        layout.addLayout(row)
        self.content = QWidget(self)
        content = QVBoxLayout(self.content)
        content.setContentsMargins(12, 0, 0, 0)
        self.loading = QLabel("読込中", self.content)
        self.threads = QListWidget(self.content)
        self.threads.setFrameShape(QFrame.Shape.NoFrame)
        self.threads.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.threads.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.threads.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.threads.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.threads.itemSelectionChanged.connect(lambda: self.set_selection(self._selected))
        self.set_name(name)
        self.threads.itemClicked.connect(self._request_thread)
        self.threads.itemActivated.connect(self._request_thread)
        self.more_button = QPushButton("続きを表示", self.content)
        self.more_button.clicked.connect(self._more)
        content.addWidget(self.loading)
        content.addWidget(self.threads)
        content.addWidget(self.more_button)
        layout.addWidget(self.content)
        self.content.hide()
        self.heading.toggled.connect(self._expand)
        self._render()

    def set_name(self, name: str) -> None:
        self.heading.setText(name)
        self.heading.setShortcut(QKeySequence())
        self.heading.setAccessibleName(name)
        self.heading.setToolTip("")
        self.threads.setAccessibleName(name)

    def _expand(self, expanded: bool) -> None:
        self.heading.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.content.setVisible(expanded)
        if expanded:
            self.opened.emit()

    def set_threads(self, page: "ThreadPage", *, append: bool) -> None:
        if not append:
            self.threads.clear()
        rows = {
            self.threads.item(i).data(Qt.ItemDataRole.UserRole): self.threads.item(i)
            for i in range(self.threads.count())
        }
        for thread in page.threads:
            item = rows.get(thread.id)
            if item is None:
                item = QListWidgetItem(self.threads)
                item.setData(Qt.ItemDataRole.UserRole, thread.id)
                rows[thread.id] = item
            title = thread_title(thread)
            item.setText(title)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, title)
        self._cursor = page.next_cursor
        self._loading = False
        self.set_selection(self._selected)
        self._render()

    def set_selection(self, thread_id: str | None) -> None:
        self._selected = thread_id
        with QSignalBlocker(self.threads):
            self.threads.clearSelection()
            for i in range(self.threads.count()):
                item = self.threads.item(i)
                item.setSelected(item.data(Qt.ItemDataRole.UserRole) == thread_id)

    def _request_thread(self, item: QListWidgetItem) -> None:
        identifier = item.data(Qt.ItemDataRole.UserRole)
        if self._enabled and not self._loading and isinstance(identifier, str):
            self.thread_requested.emit(identifier)

    def set_loading(self, loading: bool) -> None:
        self._loading = loading
        self._render()

    def set_actions_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._render()

    def set_listing_enabled(self, enabled: bool) -> None:
        self._listing_enabled = enabled
        self._render()

    def _more(self) -> None:
        if self._cursor is not None:
            self.more_requested.emit(self._cursor)

    def _render(self) -> None:
        self.new_button.setEnabled(self._enabled)
        self.threads.setEnabled(self._enabled and not self._loading)
        self.loading.setVisible(self._loading)
        self.more_button.setVisible(self._cursor is not None)
        self.more_button.setEnabled(self._listing_enabled and not self._loading)
        self.threads.setFixedHeight(
            sum(self.threads.sizeHintForRow(i) for i in range(self.threads.count()))
        )


class ConversationSidebar(QScrollArea):
    """Render supplied public pages and emit intent without choosing execution.

    New and thread signals do not change the selected highlight. The controller
    confirms selection with set_selection. Disclosure remains usable while
    conversation actions are disabled. No signal performs an RPC by itself.
    """

    group_opened = Signal(object)
    new_requested = Signal(object)
    thread_requested = Signal(str)
    projects_more_requested = Signal(str)
    threads_more_requested = Signal(object, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create an empty unassigned group with conversation actions disabled."""
        super().__init__(parent)
        self._groups: dict[str | None, _ConversationGroup] = {}
        self._enabled = False
        self._listing_enabled = False
        self._loading = False
        self._cursor: str | None = None
        self._selection: tuple[str | None, str | None] = (None, None)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAccessibleName("会話")
        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("会話", body))
        self._rows = QVBoxLayout()
        layout.addLayout(self._rows)
        self.loading = QLabel("読込中", body)
        self.projects_more_button = QPushButton("続きを表示", body)
        self.projects_more_button.clicked.connect(self._more_projects)
        layout.addWidget(self.loading)
        layout.addWidget(self.projects_more_button)
        layout.addStretch(1)
        self.setWidget(body)
        self._add_group(None, "未所属").heading.setChecked(True)
        self._render()

    def _add_group(self, project_id: str | None, name: str) -> _ConversationGroup:
        group = _ConversationGroup(name, self.widget())
        group.opened.connect(lambda: self.group_opened.emit(project_id))
        group.new_requested.connect(lambda: self.new_requested.emit(project_id))
        group.thread_requested.connect(self.thread_requested)
        group.more_requested.connect(
            lambda cursor: self.threads_more_requested.emit(project_id, cursor)
        )
        self._rows.addWidget(group)
        self._groups[project_id] = group
        group.set_actions_enabled(self._enabled)
        group.set_listing_enabled(self._listing_enabled)
        if self._selection[0] == project_id:
            group.set_selection(self._selection[1])
        return group

    def upsert_project(self, project: "ProjectInfo") -> None:
        """Add or rename a picker result without changing the Project page cursor."""
        group = self._groups.get(project.id)
        if group is None:
            self._add_group(project.id, project.name)
        else:
            group.set_name(project.name)

    @property
    def expanded_projects(self) -> tuple[str | None, ...]:
        """Return the currently expanded assignments without starting retrieval."""
        return tuple(key for key, group in self._groups.items() if group.heading.isChecked())

    def set_projects(self, page: "ProjectPage", *, append: bool = False) -> None:
        """Display a public page, retaining matching groups' expansion and selection.

        Args:
            page: Project names and the opaque next-page cursor.
            append: Keep earlier pages. False removes groups absent from this page;
                matching groups retain their displayed conversation rows.
        """
        if not append:
            retained = {project.id for project in page.projects}
            for identifier, group in tuple(self._groups.items()):
                if identifier is not None and identifier not in retained:
                    self._rows.removeWidget(group)
                    group.hide()
                    group.deleteLater()
                    del self._groups[identifier]
        for project in page.projects:
            self.upsert_project(project)
        if not append:
            for index, project in enumerate(page.projects, start=1):
                self._rows.insertWidget(index, self._groups[project.id])
        self._cursor = page.next_cursor
        self._loading = False
        self._render()

    def set_threads(
        self, project_id: str | None, page: "ThreadPage", *, append: bool = False
    ) -> None:
        """Display one known assignment's page, preserving a matching highlight.

        Args:
            project_id: Displayed Project ID, or None for unassigned conversations.
            page: Supplied server-ordered rows and opaque next-page cursor.
            append: Retain preceding rows and update duplicate IDs in place.

        Raises:
            KeyError: The Project group has not been displayed.
        """
        self._groups[project_id].set_threads(page, append=append)

    def set_selection(self, project_id: str | None, thread_id: str | None) -> None:
        """Highlight the confirmed target, including when its row arrives in a later page.

        Args:
            project_id: Selected Project ID, or None for unassigned.
            thread_id: Selected public thread ID, or None for a new conversation.
        """
        self._selection = project_id, thread_id
        for identifier, group in self._groups.items():
            group.set_selection(thread_id if identifier == project_id else None)

    def set_projects_loading(self, loading: bool) -> None:
        """Change only the Project-page indicator and its continuation button."""
        self._loading = loading
        self._render()

    def set_threads_loading(self, project_id: str | None, loading: bool) -> None:
        """Disable one assignment's rows while keeping its disclosure usable.

        Raises:
            KeyError: The Project group has not been displayed.
        """
        self._groups[project_id].set_loading(loading)

    def set_actions_enabled(self, enabled: bool) -> None:
        """Set new/selection availability without disabling disclosure or pagination."""
        self._enabled = enabled
        for group in self._groups.values():
            group.set_actions_enabled(enabled)
        self._render()

    def set_listing_enabled(self, enabled: bool) -> None:
        """Set page-request availability independently of conversation switching."""
        self._listing_enabled = enabled
        for group in self._groups.values():
            group.set_listing_enabled(enabled)
        self._render()

    def _more_projects(self) -> None:
        if self._cursor is not None:
            self.projects_more_requested.emit(self._cursor)

    def _render(self) -> None:
        self.loading.setVisible(self._loading)
        self.projects_more_button.setVisible(self._cursor is not None)
        self.projects_more_button.setEnabled(self._listing_enabled and not self._loading)
