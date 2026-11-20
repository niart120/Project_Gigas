import time
from pathlib import Path

from PySide6.QtGui import QInputMethodEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from pytestqt.qtbot import QtBot

from gigas.runtime.codex.models import DialogueEvent
from gigas.services.application import ServiceEvent
from gigas.services.conversations import NewConversationTarget
from gigas.storage.settings import Settings, save_settings
from tests.integration.peers.archives import ArchivePeer
from tests.integration.test_project_gui import close, connect
from tests.integration.test_project_management_gui import managed_shell


def test_bulk_retry_counts_hidden_chats_preserves_draft_until_archived_and_restores(
    qtbot: QtBot, tmp_path: Path
) -> None:
    path = tmp_path / "settings.toml"
    save_settings(path, Settings())
    peer = ArchivePeer(tmp_path, count=8, page_size=50)
    for index, member in enumerate(peer.members.values()):
        member.update({"name": f"攻略 {index}", "updatedAt": 100 + index})
    shell = managed_shell(path, peer)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        connect(qtbot, shell)
        qtbot.waitUntil(lambda: peer.project.id in shell.window.sidebar._groups)
        group = shell.window.sidebar._groups[peer.project.id]
        group.heading.setChecked(True)
        qtbot.waitUntil(lambda: group.threads.count() == 5)
        assert group.threads.item(0).text() == "攻略 7"
        shell.conversations.select_conversation("thread-7")
        qtbot.waitUntil(
            lambda: shell.conversations.can_switch and shell.window.connection.heading.isVisible()
        )
        panel = shell.window.connection
        QApplication.sendEvent(panel.prompt, QInputMethodEvent("みかくてい", []))
        shell.window.sidebar.archive_requested.emit(peer.project.id, "thread-7")
        box = shell.archives.confirmation
        assert box is not None
        assert box.informativeText()
        box.reject()
        assert not peer.archived
        QApplication.sendEvent(panel.prompt, QInputMethodEvent("", []))
        panel.prompt.setPlainText("未送信")
        group.archive_action.trigger()
        qtbot.waitUntil(lambda: shell.archives.confirmation is not None)
        box = shell.archives.confirmation
        assert box is not None
        assert "8" in box.text()
        assert box.informativeText()
        box.reject()
        assert panel.draft == "未送信"
        assert not peer.archived
        peer.reject_archive = "thread-4"
        group.archive_action.trigger()
        qtbot.waitUntil(lambda: shell.archives.confirmation is not None)
        box = shell.archives.confirmation
        assert box is not None
        box.button(QMessageBox.StandardButton.Yes).click()
        qtbot.waitUntil(lambda: shell.conversations.can_switch and panel.draft == "")
        assert peer.archived == {"thread-7", "thread-6", "thread-5"}
        assert shell.service.selected_target == NewConversationTarget(peer.project.id)
        panel.prompt.setPlainText("次の攻略")
        peer.reject_archive = None
        group.archive_action.trigger()
        qtbot.waitUntil(lambda: shell.archives.confirmation is not None)
        box = shell.archives.confirmation
        assert box is not None
        assert "5" in box.text()
        assert not box.informativeText()
        box.button(QMessageBox.StandardButton.Yes).click()
        qtbot.waitUntil(lambda: group.threads.count() == 0)
        shell.open_settings()
        dialog = shell.dialog
        assert dialog is not None
        dialog.tabs.setCurrentIndex(dialog.archive_tab)
        qtbot.waitUntil(lambda: dialog.archive.rows.topLevelItemCount() == 8)
        row = dialog.archive.rows.topLevelItem(0)
        assert row is not None
        assert row.text(1) == peer.project.name
        dialog.archive.rows.setCurrentItem(row)
        dialog.archive.restore.click()
        qtbot.waitUntil(lambda: dialog.archive.rows.topLevelItemCount() == 7)
        assert group.threads.count() == 1
        assert panel.draft == "次の攻略"
        assert shell.service.selected_target == NewConversationTarget(peer.project.id)
        dialog.close_button.click()
    finally:
        close(qtbot, shell)


def test_automatic_archive_only_after_first_connection(qtbot: QtBot, tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    save_settings(path, Settings(auto_archive_days=7))
    peer = ArchivePeer(tmp_path, count=3, page_size=50)
    peer.read["thread-0"] = {"revision": "unread", "firstUnread": {"turnId": "turn-1"}}
    peer.members["thread-2"]["updatedAt"] = int(time.time())
    shell = managed_shell(path, peer)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        assert not peer.archived
        connect(qtbot, shell)
        qtbot.waitUntil(lambda: peer.archived == {"thread-1"} and shell.conversations.can_switch)
        with qtbot.waitSignal(shell.service.completed):
            shell.service.disconnect_runtime()
        qtbot.waitUntil(
            lambda: shell.operation is None and not shell.window.connection.can_list_conversations
        )
        peer.read["thread-0"] = {"revision": "read", "firstUnread": None}
        connect(qtbot, shell)
        qtbot.waitUntil(lambda: shell.conversations.can_switch)
        assert peer.archived == {"thread-1"}
        scans = [
            params
            for method, params in peer.calls
            if method == "thread/list" and params and "projectId" not in params
        ]
        assert len(scans) == 1
    finally:
        close(qtbot, shell)


def test_ambiguous_archive_keeps_text_entered_while_disconnected(
    qtbot: QtBot, tmp_path: Path
) -> None:
    path = tmp_path / "settings.toml"
    save_settings(path, Settings())
    peer = ArchivePeer(tmp_path, count=6, page_size=50)
    shell = managed_shell(path, peer)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        connect(qtbot, shell)
        qtbot.waitUntil(lambda: peer.project.id in shell.window.sidebar._groups)
        group = shell.window.sidebar._groups[peer.project.id]
        group.heading.setChecked(True)
        qtbot.waitUntil(group.more_button.isVisible)
        group.more_button.click()
        qtbot.waitUntil(lambda: group.threads.count() == 6)
        shell.conversations.select_conversation("thread-5")
        qtbot.waitUntil(lambda: shell.conversations.can_switch)
        peer.members["thread-5"].update({"name": "更新した会話", "updatedAt": int(time.time())})
        shell.service.received.emit(
            ServiceEvent(
                shell.conversations.generation, DialogueEvent("turn_completed", {}, "turn-1")
            )
        )
        qtbot.waitUntil(lambda: group.threads.item(0).text() == "更新した会話")
        peer.lose_archive_response = "thread-5"
        peer.fail_reconciliation = True
        shell.window.sidebar.archive_requested.emit(peer.project.id, "thread-5")
        qtbot.waitUntil(lambda: peer.closed and not shell.window.connection.can_list_conversations)
        shell.window.connection.prompt.setPlainText("切断後に入力した原稿")
        peer.fail_reconciliation = False
        shell.window.connection.disconnect_action.trigger()
        qtbot.waitUntil(
            lambda: shell.operation is None and shell.window.connection.connect_action.isEnabled()
        )
        connect(qtbot, shell)
        qtbot.waitUntil(lambda: shell.conversations.can_switch)
        assert shell.service.selected_target == NewConversationTarget(peer.project.id)
        assert shell.window.connection.draft == "切断後に入力した原稿"
        assert group.threads.count() == 5
    finally:
        close(qtbot, shell)
