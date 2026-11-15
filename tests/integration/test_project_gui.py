import asyncio
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox
from pytestqt.qtbot import QtBot

from gigas.app.settings_worker import SettingsIO
from gigas.app.shell import ApplicationShell
from gigas.runtime.codex.connection import CodexConnection
from gigas.runtime.codex.models import ConnectionOptions, ConversationStatus, SessionStatus
from gigas.runtime.codex.transport import JsonObject, JsonValue
from gigas.services.codex import CodexServiceThread, ServiceEvent
from gigas.storage.settings import Settings, SettingsError, load_settings, save_settings
from tests.integration.peers.controlled_capture import ControlledCapture
from tests.unit.test_codex_connection import FakePeer

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.runtime.codex.connection import RuntimeEvent
    from gigas.services.codex import ServiceResult
    from gigas.ui.notifications import Notice


class ProjectsPeer(FakePeer):
    """Keep public Project registrations at the runtime boundary."""

    def __init__(self) -> None:
        """Prepare an empty catalog and an optional list gate."""
        super().__init__()
        self.projects: list[JsonValue] = []
        self.list_entered = Event()
        self.list_release: Event | None = None

    async def request(self, method: str, params: JsonObject | None) -> JsonValue:
        """Return public metadata without creating local recording files."""
        if method == "project/list":
            self.list_entered.set()
            if self.list_release is not None and not await asyncio.to_thread(
                self.list_release.wait, 5
            ):
                message = "Project list release"
                raise TimeoutError(message)
            self.responses[method] = {"data": list(self.projects), "nextCursor": None}
        elif method == "project/create":
            assert params is not None
            project: JsonObject = {
                "id": f"project-{len(self.projects) + 1}",
                "name": params["name"],
                "roots": params["roots"],
            }
            self.projects.append(project)
            self.responses[method] = {"project": project}
        return await super().request(method, params)


def make_shell(
    path: Path, *, io: SettingsIO | None = None, authenticated: bool = True
) -> tuple[ApplicationShell, ProjectsPeer, ControlledCapture]:
    capture = ControlledCapture()
    peer = ProjectsPeer()
    peer.authenticated = authenticated

    def factory(
        options: ConnectionOptions, emit: "Callable[[RuntimeEvent], None]"
    ) -> CodexConnection:
        return CodexConnection(peer, options, emit)

    service = CodexServiceThread(path.parent, capture.reader(), factory)
    shell = ApplicationShell(path, io, service=service, capture_service=capture)
    return shell, peer, capture


def connect(qtbot: QtBot, shell: ApplicationShell) -> int:
    results: list[ServiceResult] = []
    shell.service.completed.connect(results.append)
    shell.window.connection.connect_action.trigger()
    qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
    result = next(result for result in results if result.operation == "connect")
    assert not result.error
    qtbot.waitUntil(lambda: shell.operation is None)
    return result.generation


def close(qtbot: QtBot, shell: ApplicationShell) -> None:
    qtbot.waitUntil(lambda: shell.operation is None)
    shell.request_close()
    qtbot.waitUntil(lambda: not shell.window.isVisible())


def test_new_project_persists_selection_without_starting_capture(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.toml"
    target = tmp_path / "new-project"
    startup = tmp_path / "startup"
    startup.mkdir()
    monkeypatch.chdir(startup)
    save_settings(path, Settings())
    shell, peer, capture = make_shell(path)
    monkeypatch.chdir(tmp_path)

    target.mkdir()

    def choose(_parent: object, _title: str, directory: str) -> str:
        assert Path(directory) == startup
        return str(target)

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", choose)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        assert not shell.window.new_project_action.isEnabled()
        connect(qtbot, shell)
        assert shell.window.new_project_action.isEnabled()
        shell.window.new_project_action.trigger()
        assert not shell.window.new_project_action.isEnabled()
        assert shell.window.connection.disconnect_action.isEnabled()
        qtbot.waitUntil(lambda: shell.baseline.last_project_path == str(target))
        group = shell.window.sidebar._groups["project-1"]
        qtbot.waitUntil(group.empty.isVisible)
        assert list(target.iterdir()) == []
        assert load_settings(path).last_project_path == str(target)
        assert str(target) in shell.window.windowTitle()
        assert capture.connections == []
        assert shell.window.new_project_action.isEnabled()
        assert [method for method, _ in peer.calls if method.startswith("project/")] == [
            "project/list",
            "project/list",
            "project/create",
        ]
    finally:
        close(qtbot, shell)


def test_open_preserves_materials_and_restart_does_not_select_the_saved_path(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "existing"
    target.mkdir()
    notes = target / "攻略.md"
    notes.write_text("既存資料", encoding="utf-8")
    records = target / "records"
    records.mkdir()
    original = records / "keep.png"
    original.write_bytes(b"original bytes")
    path = tmp_path / "settings.toml"
    save_settings(path, Settings(last_project_path=str(target)))

    def choose(_parent: object, _title: str, directory: str) -> str:
        assert Path(directory) == target
        return str(target)

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", choose)
    shell, peer, _capture = make_shell(path)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        assert peer.calls == []
        assert str(target) not in shell.window.windowTitle()
        connect(qtbot, shell)
        shell.window.new_project_action.trigger()
        qtbot.waitUntil(lambda: str(target) in shell.window.windowTitle())
        assert [method for method, _ in peer.calls if method.startswith("project/")] == [
            "project/list",
            "project/list",
            "project/create",
        ]
    finally:
        close(qtbot, shell)

    restored, restored_peer, _capture = make_shell(path)
    restored.start()
    try:
        qtbot.waitUntil(lambda: restored.ready)
        assert not any(method == "project/create" for method, _ in restored_peer.calls)
        assert str(target) not in restored.window.windowTitle()
        qtbot.waitUntil(restored.window.connection.disconnect_action.isEnabled)
        assert notes.read_text(encoding="utf-8") == "既存資料"
        assert original.read_bytes() == b"original bytes"
        assert {entry.name for entry in target.iterdir()} == {"攻略.md", "records"}
    finally:
        close(qtbot, restored)


def test_missing_selection_hint_is_not_opened_or_reported_as_an_error(
    qtbot: QtBot, tmp_path: Path
) -> None:
    path = tmp_path / "settings.toml"
    target = tmp_path / "missing"
    save_settings(path, Settings(last_project_path=str(target)))
    shell, peer, _capture = make_shell(path)
    notices: list[Notice] = []
    shell.window.notifications.published.connect(notices.append)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        assert shell.window.connection.connect_action.isEnabled()
        assert not shell.window.new_project_action.isEnabled()
        assert notices == []
        shell.open_settings()
        assert shell.dialog is not None
        shell.dialog.preview_fps.setCurrentIndex(shell.dialog.preview_fps.findData(60))
        shell.dialog.ok_button.click()
        qtbot.waitUntil(lambda: shell.operation is None)
        assert load_settings(path).last_project_path == str(target)
        assert notices == []
        assert peer.calls == []
        assert not target.exists()
    finally:
        if shell.dialog is not None:
            shell.dialog.set_values(shell.baseline)
        close(qtbot, shell)


@pytest.mark.parametrize("authenticated", [False, True])
def test_project_selection_requires_an_idle_connection_even_after_a_thread(
    qtbot: QtBot, tmp_path: Path, *, authenticated: bool
) -> None:
    shell, _peer, _capture = make_shell(tmp_path / "settings.toml", authenticated=authenticated)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        assert not shell.window.new_project_action.isEnabled()
        generation = connect(qtbot, shell)
        assert shell.conversations.can_switch
        assert shell.window.new_project_action.isEnabled()
        for conversation in (
            ConversationStatus(sending=True),
            ConversationStatus(stopping=True),
            ConversationStatus("thread-1", "turn-1"),
        ):
            shell.service.received.emit(ServiceEvent(generation, conversation))
            assert not shell.window.new_project_action.isEnabled()
        shell.service.received.emit(ServiceEvent(generation, ConversationStatus("thread-1")))
        assert shell.window.new_project_action.isEnabled()
        shell.window.connection.disconnect_action.trigger()
        assert not shell.window.new_project_action.isEnabled()
        qtbot.waitUntil(shell.window.connection.connect_action.isEnabled)
        assert not shell.window.new_project_action.isEnabled()
    finally:
        close(qtbot, shell)


def test_recording_failure_retains_observation_and_target_in_existing_details(
    qtbot: QtBot, tmp_path: Path
) -> None:
    shell, _peer, _capture = make_shell(tmp_path / "settings.toml")
    notices: list[Notice] = []
    shell.window.notifications.published.connect(notices.append)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        shell.service.recording_failed.emit("observation-123", str(tmp_path), "disk full")
        assert len(notices) == 1
        assert "observation-123" in notices[0].details
        assert str(tmp_path) in notices[0].details
        assert "disk full" in notices[0].details
        shell.window.connection.activate(1)
        shell.service.received.emit(
            ServiceEvent(1, SessionStatus("error", detail="operation failed"))
        )
        shell.window.notifications.show_details("recording")
        assert shell.window.notifications.dialog is not None
        assert "observation-123" in shell.window.notifications.dialog.details.toPlainText()
        assert shell.window.connection.disconnect_action.isEnabled()
    finally:
        close(qtbot, shell)


def test_selection_remains_active_when_last_path_cannot_be_saved(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "existing"
    target.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: str(target))

    def fail_save(_path: Path, _settings: Settings) -> None:
        message = "injected settings write failure"
        raise SettingsError(message)

    shell, _peer, _capture = make_shell(
        tmp_path / "settings.toml", io=SettingsIO(load_settings, fail_save)
    )
    notices: list[Notice] = []
    shell.window.notifications.published.connect(notices.append)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        connect(qtbot, shell)
        notices.clear()
        shell.window.new_project_action.trigger()
        qtbot.waitUntil(lambda: bool(notices))
        assert str(target) in shell.window.windowTitle()
        assert shell.baseline.last_project_path == ""
        assert shell.window.new_project_action.isEnabled()
        assert shell.window.connection.disconnect_action.isEnabled()
        assert notices[-1].details == "injected settings write failure"
    finally:
        close(qtbot, shell)


def test_pending_selection_blocks_execution_but_allows_stop_and_close(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "existing"
    target.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: str(target))
    shell, peer, _capture = make_shell(tmp_path / "settings.toml")
    results: list[ServiceResult] = []
    shell.service.completed.connect(results.append)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        connect(qtbot, shell)
        qtbot.waitUntil(lambda: not shell.window.sidebar.loading.isVisible())
        peer.list_entered.clear()
        peer.list_release = Event()
        shell.window.connection.prompt.setPlainText("画面を確認して")
        qtbot.waitUntil(shell.window.connection.send_button.isEnabled)
        shell.window.new_project_action.trigger()
        box = shell.conversations.confirmation
        assert box is not None
        box.button(QMessageBox.StandardButton.Discard).click()
        qtbot.waitUntil(peer.list_entered.is_set)
        assert not shell.window.connection.send_button.isEnabled()
        shell.window.connection.composer.goal_mode.setChecked(True)
        assert not shell.window.connection.send_button.isEnabled()
        assert shell.window.connection.disconnect_action.isEnabled()
        shell.request_close()
        qtbot.waitUntil(lambda: any(result.operation == "stop" for result in results))
        assert not shell.committed
        assert shell.window.isVisible()
    finally:
        if peer.list_release is not None:
            peer.list_release.set()
        if not shell.committed:
            shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())
    assert load_settings(shell.path).last_project_path == str(target)


def test_cancel_folder_picker_leaves_project_catalog_unchanged(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shell, peer, _ = make_shell(tmp_path / "settings.toml")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: "")
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        connect(qtbot, shell)
        calls = list(peer.calls)
        shell.window.new_project_button.click()
        assert peer.calls == calls
        assert shell.baseline.last_project_path == ""
    finally:
        close(qtbot, shell)
