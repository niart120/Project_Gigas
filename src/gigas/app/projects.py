"""Connect project dialogs to management without owning conversation execution."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from gigas.runtime.codex.models import SessionStatus
from gigas.services.application import ServiceEvent
from gigas.services.conversations import ManagementResult
from gigas.services.projects import ProjectDeleted, ProjectDetails, ProjectFolderStatus
from gigas.ui.notifications import Notice
from gigas.ui.project_dialog import ProjectEditDialog, project_delete_confirmation

if TYPE_CHECKING:
    from gigas.app.conversations import ConversationController
    from gigas.runtime.codex.management import ProjectInfo
    from gigas.services.application import ApplicationServiceThread
    from gigas.ui.main_window import MainWindow


class ProjectController(QObject):
    """Own project editors, confirmations and folder-probe projections.

    Args:
        window: Existing main window and notification center.
        service: Owner of mutations and execution resources.
        conversations: Owner of sidebar metadata and conversation display.
        parent: Application shell.
    """

    archive_project_ready = Signal(object)

    def __init__(
        self,
        window: "MainWindow",
        service: "ApplicationServiceThread",
        conversations: "ConversationController",
        parent: QObject,
    ) -> None:
        """Wire menu intent and asynchronous results without connecting to Codex."""
        super().__init__(parent)
        self.window = window
        self.service = service
        self.conversations = conversations
        self.editor: ProjectEditDialog | None = None
        self.confirmation: QMessageBox | None = None
        self._intent: tuple[int, str, str] | None = None
        self._operation: tuple[int, str, str] | None = None
        self._probes: set[tuple[int, str]] = set()
        conversations.projects_available.connect(self._probe_projects)
        window.sidebar.edit_requested.connect(lambda pid: self._open(pid, "edit"))
        window.sidebar.delete_requested.connect(lambda pid: self._open(pid, "delete"))
        window.sidebar.archive_project_requested.connect(lambda pid: self._open(pid, "archive"))
        service.management_completed.connect(self._complete)
        service.received.connect(self._event)

    def _probe_projects(self, projects: "tuple[ProjectInfo, ...]") -> None:
        generation = self.conversations.generation
        if self._operation is not None and self._operation[0] != generation:
            self._operation = None
            self.window.connection.set_project_busy(False)
            if self.editor is not None:
                self.editor.reject()
        self._probes = {key for key in self._probes if key[0] == generation}
        for project in projects:
            self.service.probe_project_folder(project)

    def _probe(self, project_id: str) -> None:
        key = self.conversations.generation, project_id
        if key not in self._probes:
            self._probes.add(key)
            self.service.inspect_project(project_id)

    def _open(self, project_id: str, intent: str) -> None:
        if not self.conversations.can_switch or self._operation is not None:
            return
        if self.editor is not None and self.editor.isVisible():
            self.editor.raise_()
            self.editor.activateWindow()
            return
        if self.confirmation is not None:
            self.confirmation.raise_()
            return
        self._intent = self.conversations.generation, project_id, intent
        self._probe(project_id)

    def _show_editor(self, project: "ProjectInfo") -> None:
        editor = ProjectEditDialog(project, self.window)
        self.editor = editor
        generation = self.conversations.generation

        def save(name: str, root: object) -> None:
            from pathlib import Path  # noqa: PLC0415 -- validate a Qt object signal.

            if not isinstance(root, Path) or generation != self.conversations.generation:
                return
            if not self.conversations.can_switch or self._operation is not None:
                return
            self._operation = generation, project.id, "project_update"
            editor.set_pending(True)
            self.window.connection.set_project_busy(True)
            self.service.update_project(project, name=name, root=root)

        editor.save_requested.connect(save)
        editor.delete_requested.connect(lambda: self._confirm_delete(project))
        editor.show()

    def _confirm_delete(self, project: "ProjectInfo") -> None:
        if self.confirmation is not None or self._operation is not None:
            return
        box = project_delete_confirmation(project, self.window)
        self.confirmation = box
        generation = self.conversations.generation

        def finished(result: int) -> None:
            self.confirmation = None
            box.deleteLater()
            if (
                result == QMessageBox.StandardButton.Discard
                and generation == self.conversations.generation
                and self.conversations.can_switch
                and self._operation is None
            ):
                self._operation = generation, project.id, "project_delete"
                if self.editor is not None:
                    self.editor.set_pending(True)
                self.window.connection.set_project_busy(True)
                self.service.delete_project(project)

        box.finished.connect(finished)
        box.show()

    def _complete(self, value: object) -> None:
        if not isinstance(value, ManagementResult):
            return
        if value.generation != self.conversations.generation or value.project_id is None:
            return
        key = value.generation, value.project_id
        if value.operation == "project_folder":
            folder = value.value
            if isinstance(folder, ProjectFolderStatus) and (
                folder.project == self.conversations.project_info(folder.project.id)
            ):
                self.window.sidebar.set_directory_status(folder.project.id, folder.status)
            return
        if value.operation == "project_inspect":
            if key not in self._probes:
                return
            self._probes.discard(key)
            details = value.value
            if isinstance(details, ProjectDetails):
                self.window.sidebar.set_directory_status(value.project_id, details.directory_status)
            if self._intent is not None and self._intent[:2] == key:
                intent = self._intent[2]
                self._intent = None
                if isinstance(details, ProjectDetails) and self.conversations.can_switch:
                    if intent == "edit":
                        self._show_editor(details.project)
                    elif intent == "archive":
                        self.archive_project_ready.emit(details)
                    else:
                        self._confirm_delete(details.project)
                elif value.error:
                    self._notify(value.error)
            return
        if self._operation != (*key, value.operation):
            return
        self._operation = None
        self.window.connection.set_project_busy(False)
        if self.editor is not None:
            self.editor.set_pending(False)
        if value.error:
            self._notify(value.error)
            return
        if isinstance(value.value, ProjectDetails):
            details = value.value
            self.conversations.apply_project(details.project, details.threads)
            self.window.sidebar.set_directory_status(details.project.id, details.directory_status)
            self.window.statusBar().showMessage("プロジェクトを保存しました", 4000)
        elif isinstance(value.value, ProjectDeleted):
            self.conversations.remove_project(value.value.project_id)
            self.window.statusBar().showMessage("プロジェクトを削除しました", 4000)
        if self.editor is not None:
            self.editor.accept()

    def _notify(self, detail: str) -> None:
        self.window.notifications.publish(
            Notice(
                "codex",
                "プロジェクトの操作失敗",
                "処理を完了できませんでした。詳細を確認し、必要なら接続し直して編集画面を開き直してください。",
                detail,
            )
        )

    def _event(self, value: object) -> None:
        if (
            isinstance(value, ServiceEvent)
            and value.generation == self.conversations.generation
            and isinstance(value.event, SessionStatus)
            and value.event.phase in {"closed", "error"}
        ):
            self._probes.clear()
            self._intent = None
            self.window.connection.set_project_busy(False)
            if self.confirmation is not None:
                self.confirmation.reject()
