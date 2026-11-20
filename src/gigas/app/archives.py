"""Coordinate sidebar archive actions and the restore list in Settings."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QMessageBox

from gigas.runtime.codex.management_models import ThreadInfo, ThreadPage
from gigas.services.conversations import ArchiveResult, ManagementResult, StoredConversationTarget
from gigas.services.projects import ProjectDetails
from gigas.ui.conversation_sidebar import thread_title
from gigas.ui.notifications import Notice
from gigas.ui.project_dialog import archive_confirmation

if TYPE_CHECKING:
    from gigas.app.conversations import ConversationController
    from gigas.ui.settings_dialog import SettingsDialog


class ArchiveController(QObject):
    """Manage archive confirmations and result delivery on the GUI thread.

    Args:
        conversations: Owner of the sidebar's current selection and list pages.
    """

    def __init__(self, conversations: "ConversationController") -> None:
        """Bind archive actions to the current application session."""
        super().__init__(conversations)
        self.conversations = conversations
        self.service = conversations.service
        self.panel = conversations.panel
        self.window = conversations.window
        self.settings: SettingsDialog | None = None
        self.confirmation: QMessageBox | None = None
        self.auto_archive_days = 0
        self._checked_startup = False
        self._auto_pending = False
        self._mutation: str | None = None
        self._confirmed_draft: tuple[str, str] | None = None
        self._reading = False
        self._append = False
        self._refresh_pending = False
        conversations.sidebar.archive_requested.connect(self.single)
        conversations.sidebar_ready.connect(self._first_sidebar)
        self.service.management_completed.connect(self._complete)

    def attach(self, dialog: "SettingsDialog") -> None:
        """Route archive-list and restore requests for the lifetime of the settings editor."""
        self.settings = dialog
        dialog.archived_requested.connect(self._list)
        dialog.restore_requested.connect(self._restore)

    def reset_connection(self) -> None:
        """Release transient GUI state when its originating connection goes away."""
        if self.confirmation is not None:
            self.confirmation.reject()
        if self._mutation is not None:
            self._mutation = None
            self.panel.set_project_busy(False)
        self._reading = self._append = self._refresh_pending = self._auto_pending = False

    def _first_sidebar(self) -> None:
        if self._checked_startup:
            return
        self._checked_startup = True
        self._auto_pending = bool(self.auto_archive_days)
        if self._auto_pending:
            QTimer.singleShot(0, self._auto_archive)

    def _auto_archive(self) -> None:
        if self._auto_pending and self.conversations.can_switch and self.confirmation is None:
            self._auto_pending = False
            if self.auto_archive_days:
                self._begin("auto_archive")
                self.service.auto_archive_threads(self.auto_archive_days)

    def render(self, *, available: bool) -> None:
        """Update the settings controls when connection or execution admission changes."""
        connected = available and self.panel.can_list_conversations
        if not connected and self.confirmation is not None:
            self.confirmation.reject()
        if self.settings is not None:
            self.settings.set_archive_availability(
                connected,
                busy=self._reading or self._mutation is not None,
                can_restore=self.conversations.can_switch,
            )
        if self._auto_pending and self.conversations.can_switch:
            QTimer.singleShot(0, self._auto_archive)

    def single(self, project_id: str | None, thread_id: str) -> None:
        """Archive a sidebar chat, confirming disposal of its draft when needed."""
        row = self.conversations.thread_info(thread_id)
        if row is not None and row.project_id == project_id:
            self._confirm(project_id, (row.id,), thread_title(row), bulk=False)

    def bulk(self, details: ProjectDetails) -> None:
        """Confirm all normal members of the project, including undisplayed pages."""
        if not details.threads:
            self.window.statusBar().showMessage("アーカイブするチャットはありません", 4000)
            return
        self._confirm(
            details.project.id,
            tuple(row.id for row in details.threads),
            details.project.name,
            bulk=True,
        )

    def _confirm(
        self, project_id: str | None, ids: tuple[str, ...], name: str, *, bulk: bool
    ) -> None:
        if self.confirmation is not None or not self.conversations.can_switch:
            return
        target = self.service.selected_target
        generation = self.conversations.generation
        selected = isinstance(target, StoredConversationTarget) and target.thread_id in ids
        draft = self.panel.draft if selected else ""
        has_draft = selected and (bool(draft) or self.panel.prompt.is_composing)

        def archive() -> None:
            if isinstance(target, StoredConversationTarget) and selected:
                self._confirmed_draft = target.thread_id, draft
            self._begin("archive", lock_draft=selected)
            self.service.archive_threads(project_id, ids)

        if not bulk and not has_draft:
            archive()
            return
        box = archive_confirmation(
            name=name,
            count=len(ids) if bulk else None,
            discard_draft=has_draft,
            parent=self.window,
        )
        self.confirmation = box

        def finished(result: int) -> None:
            self.confirmation = None
            box.deleteLater()
            if result != QMessageBox.StandardButton.Yes:
                return
            if (
                generation == self.conversations.generation
                and target is self.service.selected_target
                and self.conversations.can_switch
                and (not selected or draft == self.panel.draft)
            ):
                archive()
            else:
                self.window.statusBar().showMessage(
                    "状態が変わりました。操作をやり直してください", 4000
                )

        box.finished.connect(finished)
        box.show()

    def _apply_confirmed(self, value: ArchiveResult) -> None:
        discard = False
        if self._confirmed_draft is not None:
            thread_id, draft = self._confirmed_draft
            if any(thread.id == thread_id for thread in value.threads):
                discard = self.panel.draft == draft and not self.panel.prompt.is_composing
                self._confirmed_draft = None
        self.conversations.apply_archive(value.threads, value.new_target, discard_draft=discard)

    def _begin(self, operation: str, *, lock_draft: bool = False) -> None:
        self._mutation = operation
        self.panel.set_project_busy(True, lock_draft=lock_draft)

    def _list(self, cursor: str | None = None) -> None:
        if not self.panel.can_list_conversations:
            return
        if self._reading:
            self._refresh_pending |= cursor is None
            return
        self._reading = True
        self._append = cursor is not None
        self.render(available=True)
        self.service.list_archived_threads(cursor=cursor)

    def _restore(self, thread_id: str) -> None:
        if self.conversations.can_switch and self._mutation is None:
            self._begin("restore")
            self.service.restore_thread(thread_id)

    def _complete(self, result: object) -> None:
        if (
            not isinstance(result, ManagementResult)
            or result.generation != self.conversations.generation
        ):
            return
        if result.operation == "archive_reconciled" and isinstance(result.value, ArchiveResult):
            self._apply_confirmed(result.value)
        elif result.operation == "archived" and self._reading:
            self._reading = False
            if self.settings is not None and isinstance(result.value, ThreadPage):
                names = {
                    project.id: project.name
                    for thread in result.value.threads
                    if thread.project_id is not None
                    and (project := self.conversations.project_info(thread.project_id)) is not None
                }
                self.settings.set_archived_page(
                    result.value, append=self._append, project_names=names
                )
            if self._refresh_pending:
                self._refresh_pending = False
                self._list()
        elif self._mutation is not None and result.operation == self._mutation:
            self._mutation = None
            self.panel.set_project_busy(False)
            if isinstance(result.value, ArchiveResult):
                self._apply_confirmed(result.value)
            elif isinstance(result.value, ThreadInfo):
                self.conversations.refresh_group(result.value.project_id)
            if (
                self.settings is not None
                and self.settings.isVisible()
                and self.settings.tabs.currentIndex() == self.settings.archive_tab
            ):
                self._list()
        else:
            return
        if result.error:
            recovery = (
                "「切断」を選んでから再度接続し、チャット一覧を確認してください。"
                if not self.panel.can_list_conversations
                else "チャット一覧を更新して、操作結果を確認してください。"
            )
            self.window.notifications.publish(
                Notice("codex", "アーカイブ操作の失敗", recovery, result.error)
            )
        self.render(available=True)
