"""Connect public conversation management to the existing Qt views."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import QMessageBox

from gigas.runtime.codex.history import history_entries
from gigas.runtime.codex.management_models import ProjectPage, ThreadPage
from gigas.runtime.codex.models import ConversationStatus, DialogueEvent, SessionStatus
from gigas.services.codex import ProjectResult, ServiceEvent, ServiceResult
from gigas.services.conversations import (
    BrowseGoalEvent,
    BrowseResult,
    HistoryResult,
    ManagementResult,
    NewConversationTarget,
    StoredConversationTarget,
)
from gigas.ui.confirmation import draft_confirmation
from gigas.ui.conversation_sidebar import thread_title
from gigas.ui.notifications import Notice

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.runtime.codex.management_models import ProjectInfo, ThreadInfo
    from gigas.services.codex import CodexServiceThread
    from gigas.ui.main_window import MainWindow


class ConversationController(QObject):
    """Coordinate fetched metadata and display requests, leaving selection to the service.

    Args:
        window: Existing application views and notification center.
        service: Owner of the selected target and all runtime operations.
        parent: Owning application shell.
    """

    def __init__(
        self, window: "MainWindow", service: "CodexServiceThread", parent: QObject
    ) -> None:
        """Wire view intent without connecting to Codex or fetching any pages."""
        super().__init__(parent)
        self.window = window
        self.service = service
        self.panel = window.connection
        self.sidebar = window.sidebar
        self.generation = 0
        self.confirmation: QMessageBox | None = None
        self._available = True
        self._project_pending = False
        self._target = service.selected_target
        self._projects: dict[str, ProjectInfo] = {}
        self._threads: dict[str, ThreadInfo] = {}
        self._listed = False
        self._projects_pending = False
        self._thread_requests: dict[str | None, str | None] = {}
        self._threads_loaded: set[str | None] = set()
        self._refresh_threads: set[str | None] = set()
        self._selection_pending: str | None = None
        self._history_pending: StoredConversationTarget | None = None
        self._history_cursor: str | None = None
        self._unlisted_target: StoredConversationTarget | None = None
        self.sidebar.group_opened.connect(self._open_group)
        self.sidebar.new_requested.connect(self.new_conversation)
        self.sidebar.thread_requested.connect(self.select_conversation)
        self.sidebar.projects_more_requested.connect(self._list_projects)
        self.sidebar.threads_more_requested.connect(self._list_threads)
        self.panel.history_button.clicked.connect(self._load_history)
        self.panel.state_changed.connect(self._render)
        service.management_completed.connect(self._management_complete)
        service.browse_goal_changed.connect(self._browse_goal)
        service.received.connect(self._event)
        service.completed.connect(self._complete)
        service.input_busy_changed.connect(self.panel.set_input_busy)
        self.panel.replace_target("未所属", "新しい会話")
        self._render()

    def activate(self, generation: int) -> None:
        """Adopt one connection generation while retaining displayed documents."""
        self.generation = generation
        self._reset_requests()
        self._listed = False
        self._threads_loaded.clear()
        self.panel.activate(generation)

    def set_available(self, available: bool, *, project_pending: bool = False) -> None:
        """Apply shell lifetime and picker admission without blocking stop or disconnect."""
        self._available = available
        self._project_pending = project_pending
        if not available and self.confirmation is not None:
            self.confirmation.reject()
        self._render()

    @property
    def can_switch(self) -> bool:
        """Whether a new UI selection may begin; service admission remains authoritative."""
        return (
            self._available
            and not self._project_pending
            and self._selection_pending is None
            and self.confirmation is None
            and self.panel.can_switch_conversation
        )

    def confirm_switch(self, action: "Callable[[], None]") -> None:
        """Run a selection after explicit draft disposal, rechecking admission on acceptance."""
        if not self.can_switch:
            return
        if not self.panel.draft:
            self.panel.discard_draft()
            action()
            return
        target = self.service.selected_target
        generation = self.generation
        box = draft_confirmation(self.window)
        self.confirmation = box

        def finished(result: int) -> None:
            self.confirmation = None
            box.deleteLater()
            if (
                result == QMessageBox.StandardButton.Discard
                and generation == self.generation
                and target is self.service.selected_target
                and self.can_switch
            ):
                self.panel.discard_draft()
                action()
            self._render()

        box.finished.connect(finished)
        box.show()
        self._render()

    def new_conversation(self, project_id: str | None = None) -> None:
        """Select an unstarted assignment through the service after draft confirmation."""

        def choose() -> None:
            self._selection_pending = "new"
            self.panel.begin_loading(self._project_name(project_id), "新しい会話")
            self.service.new_conversation(project_id)
            self._render()

        self.confirm_switch(choose)

    def select_conversation(self, thread_id: str) -> None:
        """Read a fetched conversation without resuming or recreating its workspace."""
        target = self.service.selected_target
        if isinstance(target, StoredConversationTarget) and target.thread_id == thread_id:
            return
        thread = self._threads.get(thread_id)
        if thread is None:
            return

        def choose() -> None:
            self._selection_pending = "select"
            self.panel.begin_loading(self._project_name(thread.project_id), thread_title(thread))
            self.service.select_conversation(thread_id)
            self._render()

        self.confirm_switch(choose)

    def adopt_project(self, result: ProjectResult) -> bool:
        """Display a current successful picker result; return whether it was adopted."""
        project = result.project
        target = self.service.selected_target
        if (
            result.generation != self.generation
            or result.error
            or project is None
            or not isinstance(target, NewConversationTarget)
            or target.project_id != project.id
        ):
            return False
        self._projects[project.id] = project
        self.sidebar.upsert_project(project)
        self._replace_new(target)
        return True

    def _project_name(self, project_id: str | None) -> str:
        if project_id is None:
            return "未所属"
        project = self._projects.get(project_id)
        return project.name if project is not None else project_id

    def _replace_new(self, target: NewConversationTarget) -> None:
        self._target = target
        self._history_pending = None
        self._history_cursor = None
        self._unlisted_target = None
        self.panel.replace_target(self._project_name(target.project_id), "新しい会話")
        self._update_heading()
        self.sidebar.set_selection(target.project_id, None)
        self.panel.prompt.setFocus()
        self._render()

    def _open_group(self, project_id: str | None) -> None:
        if project_id not in self._threads_loaded:
            self._list_threads(project_id)

    def _list_projects(self, cursor: str | None = None) -> None:
        if not self._available or not self.panel.can_list_conversations or self._projects_pending:
            return
        self._projects_pending = True
        self.sidebar.set_projects_loading(True)
        self.service.list_projects(cursor=cursor)

    def _list_threads(self, project_id: str | None, cursor: str | None = None) -> None:
        if (
            not self._available
            or not self.panel.can_list_conversations
            or project_id in self._thread_requests
            or (project_id is not None and project_id not in self._projects)
        ):
            return
        self._thread_requests[project_id] = cursor
        self.sidebar.set_threads_loading(project_id, True)
        self.service.list_threads(project_id, cursor=cursor)

    def _load_history(self) -> None:
        target = self.service.selected_target
        if (
            not self.panel.history_button.isEnabled()
            or not isinstance(target, StoredConversationTarget)
            or self._history_cursor is None
        ):
            return
        self._history_pending = target
        self.service.load_history(cursor=self._history_cursor)
        self._render()

    def _show_browse(self, target: StoredConversationTarget, result: BrowseResult) -> None:
        self._target = target
        self._threads[result.thread.id] = result.thread
        self._history_pending = None
        self._history_cursor = result.history.next_cursor
        self._unlisted_target = None
        self.panel.replace_target(
            self._project_name(result.thread.project_id),
            thread_title(result.thread),
            thread_id=result.thread.id,
            entries=history_entries(result.thread.id, result.history),
            goal=result.goal,
        )
        self.sidebar.set_selection(target.project_id, target.thread_id)
        self._update_heading()

    @Slot(object)
    def _management_complete(self, result: object) -> None:
        if not isinstance(result, ManagementResult) or result.generation != self.generation:
            return
        operation, value = result.operation, result.value
        if operation == "projects":
            if not self._projects_pending:
                return
            self._projects_pending = False
            self.sidebar.set_projects_loading(False)
            if isinstance(value, ProjectPage):
                self._projects.update((project.id, project) for project in value.projects)
                self.sidebar.set_projects(value, append=True)
                self._update_heading()
        elif operation == "threads":
            if result.project_id not in self._thread_requests:
                return
            cursor = self._thread_requests.pop(result.project_id)
            self.sidebar.set_threads_loading(result.project_id, False)
            if isinstance(value, ThreadPage):
                self._threads.update((thread.id, thread) for thread in value.threads)
                self._threads_loaded.add(result.project_id)
                self.sidebar.set_threads(result.project_id, value, append=cursor is not None)
                self._update_heading()
                if self._unlisted_target is not None and any(
                    thread.id == self._unlisted_target.thread_id for thread in value.threads
                ):
                    self._unlisted_target = None
                    self._refresh_threads.discard(result.project_id)
            if result.project_id in self._refresh_threads:
                self._refresh_threads.remove(result.project_id)
                self._list_threads(result.project_id)
        elif operation in {"new", "select"}:
            if operation != self._selection_pending:
                return
            self._selection_pending = None
            target = self.service.selected_target
            if isinstance(value, NewConversationTarget) and value is target:
                self._replace_new(value)
            elif (
                isinstance(value, BrowseResult)
                and isinstance(target, StoredConversationTarget)
                and target.thread_id == value.thread.id
            ):
                self._show_browse(target, value)
            else:
                self.panel.cancel_loading()
        elif operation == "history":
            target = self.service.selected_target
            if self._history_pending is not target or (
                isinstance(value, HistoryResult) and value.target is not target
            ):
                return
            self._history_pending = None
            if isinstance(value, HistoryResult):
                self.panel.transcript.prepend_items(
                    history_entries(value.target.thread_id, value.page)
                )
                self._history_cursor = value.page.next_cursor
        if result.error and self._available:
            self.window.notifications.publish(
                Notice(
                    "codex",
                    "会話の取得失敗",
                    "会話情報を取得できませんでした。対象を選び直すか、接続し直してください。",
                    result.error,
                )
            )
        self._render()

    @Slot(int, object)
    def _browse_goal(self, generation: int, event: object) -> None:
        if (
            generation == self.generation
            and isinstance(event, BrowseGoalEvent)
            and event.target is self.service.selected_target
        ):
            self.panel.set_browse_goal(event.goal)

    @Slot(object)
    def _event(self, envelope: object) -> None:
        if not isinstance(envelope, ServiceEvent) or envelope.generation != self.generation:
            return
        if isinstance(envelope.event, SessionStatus) and envelope.event.phase in {
            "closed",
            "error",
        }:
            self._reset_requests()
        if (
            isinstance(envelope.event, ConversationStatus)
            and self._selection_pending is None
            and not self._project_pending
        ):
            target = self.service.selected_target
            if isinstance(self._target, NewConversationTarget) and isinstance(
                target, StoredConversationTarget
            ):
                self._target = target
                self._unlisted_target = target
                self.sidebar.set_selection(target.project_id, target.thread_id)
                self._update_heading()
        if isinstance(envelope.event, DialogueEvent) and (
            envelope.event.kind == "turn_completed"
            or (
                envelope.event.kind in {"message", "activity"}
                and envelope.event.payload.get("partial") is False
            )
        ):
            self._refresh_unlisted()
        self._render()

    @Slot(object)
    def _complete(self, result: object) -> None:
        if not isinstance(result, ServiceResult) or result.generation != self.generation:
            return
        if result.operation == "disconnect":
            self._reset_requests()
        if (
            result.operation in {"send", "goal_set"}
            and result.message is not None
            and result.message.outcome == "accepted"
        ):
            self._refresh_unlisted()
        self._render()

    def _refresh_unlisted(self) -> None:
        target = self._unlisted_target
        if target is not None and target is self.service.selected_target:
            if target.project_id in self._thread_requests:
                self._refresh_threads.add(target.project_id)
            else:
                self._list_threads(target.project_id)

    def _update_heading(self) -> None:
        target = self._target
        title = "新しい会話"
        project = self._projects.get(target.project_id) if target.project_id is not None else None
        root = project.root if project is not None else None
        if isinstance(target, StoredConversationTarget):
            thread = self._threads.get(target.thread_id)
            title = thread_title(thread) if thread is not None else "名前のない会話"
            root = target.cwd
        self.panel.update_target_heading(self._project_name(target.project_id), title)
        self.window.setWindowTitle(f"GIGAS · {root}" if root is not None else "GIGAS")

    def _reset_requests(self) -> None:
        self._projects_pending = False
        self.sidebar.set_projects_loading(False)
        for project_id in self._thread_requests:
            self.sidebar.set_threads_loading(project_id, False)
        self._thread_requests.clear()
        self._refresh_threads.clear()
        self._selection_pending = None
        self._history_pending = None
        self.panel.cancel_loading()

    def _render(self) -> None:
        listing = self._available and self.panel.can_list_conversations
        if listing and not self._listed:
            self._listed = True
            self._list_projects()
            for project_id in self.sidebar.expanded_projects:
                self._list_threads(project_id)
        self.sidebar.set_listing_enabled(listing)
        self.sidebar.set_actions_enabled(self.can_switch)
        self.panel.history_button.setVisible(self._history_cursor is not None)
        self.panel.history_button.setEnabled(
            listing and self._history_pending is None and self._selection_pending is None
        )
