from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog
from pytestqt.qtbot import QtBot

from gigas.app.settings_worker import SettingsIO
from gigas.app.shell import ApplicationShell
from gigas.runtime.codex.models import SessionStatus
from gigas.services.codex import ServiceEvent, ServiceResult
from gigas.storage.project import ProjectWriter
from gigas.storage.settings import Settings, SettingsError, load_settings, save_settings
from tests.integration.peers.controlled_capture import ControlledCapture

if TYPE_CHECKING:
    from gigas.ui.notifications import Notice


def test_new_project_persists_selection_without_starting_capture(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.toml"
    target = tmp_path / "new-project"
    save_settings(path, Settings(capture_device="saved-camera"))
    capture = ControlledCapture()
    shell = ApplicationShell(path, capture_service=capture)

    def choose(dialog: QFileDialog) -> int:
        assert dialog.acceptMode() == QFileDialog.AcceptMode.AcceptSave
        assert dialog.fileMode() == QFileDialog.FileMode.AnyFile
        assert dialog.testOption(QFileDialog.Option.DontConfirmOverwrite)
        dialog.selectFile(str(target))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QFileDialog, "exec", choose)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        shell.window.new_project_action.trigger()
        assert not shell.window.project_menu.isEnabled()
        assert not shell.window.connection.connect_action.isEnabled()
        qtbot.waitUntil(lambda: shell.baseline.last_project_path == str(target))
        assert (target / "project.json").is_file()
        assert load_settings(path).last_project_path == str(target)
        assert str(target) in shell.window.windowTitle()
        assert capture.connections == []
        assert shell.window.project_menu.isEnabled()
        assert shell.window.connection.connect_action.isEnabled()
    finally:
        qtbot.waitUntil(lambda: shell.operation is None)
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())

    ProjectWriter.open(target).close()


def test_open_and_restart_restore_project_and_keep_existing_records(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "existing"
    ProjectWriter.create(target).close()
    original = (target / "project.json").read_bytes()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: str(target))
    path = tmp_path / "settings.toml"
    shell = ApplicationShell(path)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        shell.window.open_project_action.trigger()
        qtbot.waitUntil(lambda: shell.baseline.last_project_path == str(target))
    finally:
        qtbot.waitUntil(lambda: shell.operation is None)
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())

    restored = ApplicationShell(path)
    restored.start()
    try:
        qtbot.waitUntil(lambda: str(target) in restored.window.windowTitle())
        assert restored.window.connection.connect_action.isEnabled()
        assert (target / "project.json").read_bytes() == original
    finally:
        qtbot.waitUntil(lambda: restored.operation is None)
        restored.request_close()
        qtbot.waitUntil(lambda: not restored.window.isVisible())


def test_restore_failure_keeps_chat_available_and_is_not_retried_on_settings_save(
    qtbot: QtBot, tmp_path: Path
) -> None:
    path = tmp_path / "settings.toml"
    target = tmp_path / "missing"
    save_settings(path, Settings(last_project_path=str(target)))
    shell = ApplicationShell(path)
    notices: list[Notice] = []
    shell.window.notifications.published.connect(notices.append)
    shell.start()
    try:
        qtbot.waitUntil(lambda: bool(notices))
        assert shell.window.connection.connect_action.isEnabled()
        assert shell.window.project_menu.isEnabled()
        assert str(target) in notices[-1].details
        shell.open_settings()
        assert shell.dialog is not None
        shell.dialog.model.setText("changed")
        shell.dialog.ok_button.click()
        qtbot.waitUntil(lambda: shell.operation is None)
        assert load_settings(path).last_project_path == str(target)
        assert len(notices) == 1
        assert not target.exists()
    finally:
        if shell.dialog is not None:
            shell.dialog.set_values(shell.baseline)
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())


def test_connected_session_disables_project_switch_until_disconnect_result(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shell = ApplicationShell(tmp_path / "settings.toml")
    monkeypatch.setattr(shell.service, "connect_runtime", lambda _options: 7)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        shell.window.connection.connect_action.trigger()
        assert not shell.window.project_menu.isEnabled()
        shell.service.received.emit(ServiceEvent(7, SessionStatus("closed")))
        assert not shell.window.project_menu.isEnabled()
        shell.service.completed.emit(ServiceResult(7, "disconnect"))
        assert shell.window.project_menu.isEnabled()
    finally:
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())


def test_recording_failure_retains_observation_and_target_in_existing_details(
    qtbot: QtBot, tmp_path: Path
) -> None:
    shell = ApplicationShell(tmp_path / "settings.toml")
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
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())


def test_selection_remains_active_when_last_path_cannot_be_saved(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "existing"
    ProjectWriter.create(target).close()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: str(target))

    def fail_save(_path: Path, _settings: Settings) -> None:
        message = "injected settings write failure"
        raise SettingsError(message)

    shell = ApplicationShell(tmp_path / "settings.toml", SettingsIO(load_settings, fail_save))
    notices: list[Notice] = []
    shell.window.notifications.published.connect(notices.append)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        shell.window.open_project_action.trigger()
        qtbot.waitUntil(lambda: bool(notices))
        assert str(target) in shell.window.windowTitle()
        assert shell.baseline.last_project_path == ""
        assert shell.window.connection.connect_action.isEnabled()
        assert notices[-1].details == "injected settings write failure"
    finally:
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())


def test_close_waits_for_accepted_project_selection_and_settings_save(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "existing"
    ProjectWriter.create(target).close()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: str(target))
    opening = Event()
    release = Event()
    original_open = ProjectWriter.open

    def blocked_open(_cls: type[ProjectWriter], path: Path) -> ProjectWriter:
        opening.set()
        assert release.wait(5)
        return original_open(path)

    monkeypatch.setattr(ProjectWriter, "open", classmethod(blocked_open))
    shell = ApplicationShell(tmp_path / "settings.toml")
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        shell.window.open_project_action.trigger()
        qtbot.waitUntil(opening.is_set)
        shell.request_close()
        assert not shell.committed
        assert shell.window.isVisible()
    finally:
        release.set()
        qtbot.waitUntil(lambda: not shell.window.isVisible())
    assert load_settings(shell.path).last_project_path == str(target)
