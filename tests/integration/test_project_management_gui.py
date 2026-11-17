from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.runtime.codex.connection import CodexConnection, RuntimeEvent
from gigas.runtime.codex.models import ConnectionOptions
from gigas.services.application import ApplicationServiceThread
from gigas.services.conversations import StoredConversationTarget
from gigas.storage.settings import Settings, save_settings
from tests.integration.peers.controlled_capture import ControlledCapture
from tests.integration.test_project_gui import close, connect
from tests.integration.test_project_management import ProjectPeer
from tests.unit.test_codex_history import _items


def managed_shell(path: Path, peer: ProjectPeer) -> ApplicationShell:
    """Assemble the real GUI and services with a persistent metadata peer."""
    capture = ControlledCapture()

    def factory(
        options: ConnectionOptions, emit: Callable[[RuntimeEvent], None]
    ) -> CodexConnection:
        return CodexConnection(peer, options, emit)

    service = ApplicationServiceThread(path.parent, capture.reader(), factory)
    return ApplicationShell(path, service=service, capture_service=capture)


def test_edit_updates_members_and_keeps_displayed_history_and_draft(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.toml"
    save_settings(path, Settings())
    root = tmp_path / "new"
    root.mkdir()
    peer = ProjectPeer(tmp_path)
    peer.responses["thread/items/list"] = {
        "data": [{"turnId": "turn-1", "item": item} for item in reversed(_items())]
    }
    peer.responses["thread/turns/list"] = {
        "data": [{"id": "turn-1", "status": "completed", "itemsView": "notLoaded", "items": []}],
    }
    shell = managed_shell(path, peer)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        connect(qtbot, shell)
        qtbot.waitUntil(lambda: "project-1" in shell.window.sidebar._groups)
        group = shell.window.sidebar._groups["project-1"]
        group.heading.setChecked(True)
        qtbot.waitUntil(lambda: "thread-0" in shell.conversations._threads)
        shell.conversations.select_conversation("thread-0")
        qtbot.waitUntil(lambda: isinstance(shell.service.selected_target, StoredConversationTarget))
        panel = shell.window.connection
        qtbot.waitUntil(
            lambda: bool(panel.transcript.toPlainText()) and shell.conversations.can_switch
        )
        before = panel.transcript.toPlainText()
        assert before
        panel.prompt.setPlainText("Keep this draft")
        group.edit_action.trigger()
        qtbot.waitUntil(lambda: shell.projects.editor is not None)
        editor = shell.projects.editor
        assert editor is not None
        editor.name.setText("Renamed")
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_: str(root))
        qtbot.mouseClick(editor.choose_button, Qt.MouseButton.LeftButton)
        qtbot.mouseClick(editor.save_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: not editor.isVisible())
        assert peer.project.name == "Renamed"
        assert all(member["cwd"] == str(root) for member in peer.members.values())
        assert panel.draft == "Keep this draft"
        assert panel.transcript.toPlainText() == before
        assert shell.service.selected_target == StoredConversationTarget(
            "thread-0", root, "project-1"
        )
        assert not any(method.startswith("turn/") for method, _ in peer.calls)
    finally:
        close(qtbot, shell)


def test_delete_confirmation_ignores_unsaved_edit_and_keeps_new_draft(
    qtbot: QtBot, tmp_path: Path
) -> None:
    path = tmp_path / "settings.toml"
    save_settings(path, Settings())
    peer = ProjectPeer(tmp_path / "missing", count=0)
    shell = managed_shell(path, peer)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        connect(qtbot, shell)
        qtbot.waitUntil(lambda: "project-1" in shell.window.sidebar._groups)
        shell.conversations.new_conversation("project-1")
        qtbot.waitUntil(lambda: shell.service.selected_target.project_id == "project-1")
        qtbot.waitUntil(lambda: shell.conversations.can_switch)
        shell.window.connection.prompt.setPlainText("Unsaved message")
        group = shell.window.sidebar._groups["project-1"]
        group.edit_action.trigger()
        qtbot.waitUntil(lambda: shell.projects.editor is not None)
        editor = shell.projects.editor
        assert editor is not None
        editor.name.setText("Unsaved name")
        qtbot.mouseClick(editor.delete_button, Qt.MouseButton.LeftButton)
        box = shell.projects.confirmation
        assert box is not None
        assert "Unsaved name" not in box.text()
        button = box.button(QMessageBox.StandardButton.Discard)
        assert button is not None
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: "project-1" not in shell.window.sidebar._groups)
        assert peer.deleted
        assert shell.service.selected_target.project_id is None
        assert shell.window.connection.draft == "Unsaved message"
        assert not any(method in {"thread/resume", "project/update"} for method, _ in peer.calls)
    finally:
        close(qtbot, shell)
