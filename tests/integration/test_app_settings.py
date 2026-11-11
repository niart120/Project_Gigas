import errno
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from gigas.app.settings_worker import SettingsIO
from gigas.app.shell import ApplicationShell
from gigas.game_io.capture.models import DeviceInfo
from gigas.storage.settings import Settings, SettingsError, load_settings, save_settings


@pytest.fixture
def shell(qtbot: QtBot, tmp_path: Path) -> Iterator[ApplicationShell]:
    application = ApplicationShell(tmp_path / "settings.toml")
    application.start()
    qtbot.waitUntil(lambda: application.operation is None)
    yield application
    if application.window.isVisible():
        if application.dialog is not None:
            application.dialog.set_values(application.baseline)
        if application.confirmation is not None:
            application.confirmation.reject()
        application.window.close()
        qtbot.waitUntil(lambda: not application.window.isVisible())


def test_settings_reopens_same_dialog_with_edits(shell: ApplicationShell, qtbot: QtBot) -> None:
    shell.window.settings_action.trigger()
    dialog = shell.dialog
    assert dialog is not None
    qtbot.waitUntil(dialog.isVisible)
    dialog.model.setText("edited")

    shell.window.settings_action.trigger()

    assert shell.dialog is dialog
    assert dialog.model.text() == "edited"
    assert not dialog.isModal()


def test_defaults_only_written_on_explicit_save(shell: ApplicationShell, qtbot: QtBot) -> None:
    shell.window.settings_action.trigger()
    dialog = shell.dialog
    assert dialog is not None
    assert not shell.path.exists()
    dialog.close()
    assert not shell.path.exists()
    shell.window.settings_action.trigger()
    dialog.save_button.click()
    qtbot.waitUntil(shell.path.exists)
    assert load_settings(shell.path) == Settings()


def test_all_settings_round_trip_and_disconnect_preserves_last_device(
    shell: ApplicationShell, qtbot: QtBot
) -> None:
    save_settings(shell.path, Settings(auth_method="api_key"))
    shell.reload()
    qtbot.waitUntil(lambda: shell.operation is None)
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    values = Settings(" 映像\n入力 ", " COM3 ", "unknown-model", "custom-effort", "api_key")
    dialog.set_capture_devices((DeviceInfo("Camera", values.capture_device),))
    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData(values.capture_device))
    dialog.serial_port.setText(values.serial_port)
    dialog.model.setText(values.model)
    dialog.effort.setText(values.effort)
    dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert load_settings(shell.path) == values
    shell.window.close()
    qtbot.waitUntil(lambda: not shell.window.isVisible())

    other = ApplicationShell(shell.path)
    other.start()
    try:
        qtbot.waitUntil(lambda: other.operation is None)
        other.open_settings()
        assert other.dialog is not None
        assert other.dialog.values() == values
        assert other.dialog.capture_choices.currentData() == ""
        assert other.dialog.capture_choices.findData(values.capture_device) >= 0
        assert other.dialog.model.text() == values.model
        assert other.dialog.auth_method.currentData() == "api_key"
        other.dialog.capture_choices.setCurrentIndex(0)
        other.dialog.serial_port.clear()
        other.dialog.model.clear()
        other.dialog.effort.clear()
        other.dialog.save_button.click()
        qtbot.waitUntil(lambda: other.operation is None)
        assert load_settings(shell.path) == Settings(
            capture_device=values.capture_device, auth_method="api_key"
        )
    finally:
        other.window.close()
        qtbot.waitUntil(lambda: not other.window.isVisible())


def test_restoring_baseline_clears_dirty(shell: ApplicationShell) -> None:
    shell.open_settings()
    assert shell.dialog is not None
    assert not shell.dirty
    shell.dialog.model.setText("changed")
    assert shell.dirty
    shell.dialog.model.clear()
    assert not shell.dirty


@pytest.mark.parametrize("choice", ["save", "discard", "cancel"])
def test_dirty_application_close_choices(
    shell: ApplicationShell, qtbot: QtBot, choice: str
) -> None:
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    dialog.model.setText("edited")
    shell.window.close()
    box = shell.confirmation
    assert box is not None
    buttons = {
        "save": QMessageBox.StandardButton.Save,
        "discard": QMessageBox.StandardButton.Discard,
        "cancel": QMessageBox.StandardButton.Cancel,
    }
    box.button(buttons[choice]).click()
    if choice == "cancel":
        assert dialog.isVisible()
        assert dialog.model.text() == "edited"
        assert not shell.path.exists()
    else:
        qtbot.waitUntil(lambda: not dialog.isVisible())
        if choice == "save":
            assert load_settings(shell.path).model == "edited"
        else:
            assert not shell.path.exists()
        assert not shell.window.isVisible()


@pytest.mark.parametrize("target", ["button", "window", "escape"])
def test_dirty_dialog_cancel_discards_without_saving_or_confirmation(
    shell: ApplicationShell, qtbot: QtBot, target: str
) -> None:
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    dialog.model.setText("edited")
    if target == "button":
        dialog.close_button.click()
    elif target == "window":
        dialog.close()
    else:
        qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    assert shell.confirmation is None
    assert not dialog.isVisible()
    assert not shell.path.exists()
    assert shell.window.isVisible()
    shell.open_settings()
    assert dialog.model.text() == shell.baseline.model == ""


def test_failed_reload_blocks_save_until_recovered(shell: ApplicationShell, qtbot: QtBot) -> None:
    shell.path.write_text("[broken", encoding="utf-8")
    shell.open_settings()
    assert shell.dialog is not None
    shell.window.reload_action.trigger()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert not shell.dialog.save_button.isEnabled()
    assert str(shell.path) in shell.dialog.details.toPlainText()
    assert shell.path.read_text(encoding="utf-8") == "[broken"
    shell.path.write_text('[agent]\nmodel="repaired"', encoding="utf-8")
    shell.window.reload_action.trigger()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.dialog.ok_button.isEnabled()
    assert not shell.dialog.model.isReadOnly()
    assert shell.dialog.model.text() == "repaired"
    shell.dialog.model.setText("after recovery")
    assert shell.dialog.save_button.isEnabled()


def test_save_failure_preserves_edits_and_reports_cleanup(
    shell: ApplicationShell, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_settings(shell.path, Settings(model="original"))
    shell.reload()
    qtbot.waitUntil(lambda: shell.operation is None)
    shell.open_settings()
    assert shell.dialog is not None
    shell.dialog.model.setText("edited")

    def fail(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(errno.EACCES, "injected denial")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail)
        patch.setattr(Path, "unlink", fail)
        shell.dialog.save_button.click()
        qtbot.waitUntil(lambda: shell.operation is None)
        assert shell.dialog.model.text() == "edited"
        assert shell.dialog.save_button.isEnabled()
        assert "remove temporary file" in shell.dialog.details.toPlainText()
        assert load_settings(shell.path).model == "original"

    shell.dialog.model.setText("retry")
    shell.dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert load_settings(shell.path).model == "retry"
    assert not shell.error
    assert not shell.dirty


def test_reload_confirms_discard_and_keeps_values_on_failure(
    shell: ApplicationShell, qtbot: QtBot
) -> None:
    shell.open_settings()
    assert shell.dialog is not None
    shell.dialog.model.setText("edited")
    shell.window.reload_action.trigger()
    box = shell.confirmation
    assert box is not None
    assert box.button(QMessageBox.StandardButton.Save) is None
    box.button(QMessageBox.StandardButton.Cancel).click()
    assert shell.dialog.model.text() == "edited"
    assert shell.operation is None

    shell.path.write_text("[broken", encoding="utf-8")
    shell.window.reload_action.trigger()
    assert shell.confirmation is not None
    shell.confirmation.button(QMessageBox.StandardButton.Discard).click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.dialog.model.text() == "edited"
    assert not shell.dialog.save_button.isEnabled()
    shell.window.close()
    assert shell.confirmation is not None
    assert shell.confirmation.button(QMessageBox.StandardButton.Save) is None
    shell.confirmation.button(QMessageBox.StandardButton.Cancel).click()
    assert shell.dialog.isVisible()


def test_unedited_text_preserves_crlf_and_nonbreaking_space(
    shell: ApplicationShell, qtbot: QtBot
) -> None:
    original = Settings(model="first\r\nsecond\u00a0word")
    save_settings(shell.path, original)
    shell.reload()
    qtbot.waitUntil(lambda: shell.operation is None)
    shell.open_settings()
    assert not shell.dirty
    assert shell.dialog is not None
    shell.dialog.effort.setText("high")
    shell.save()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert load_settings(shell.path).model == original.model


@pytest.mark.parametrize("operation", ["load", "save"])
def test_unexpected_worker_failure_returns_without_exposing_exception_text(
    qtbot: QtBot, tmp_path: Path, operation: str
) -> None:
    def fail_load(_path: Path) -> Settings:
        message = "private-setting-value"
        raise RuntimeError(message)

    def fail_save(path: Path, _settings: Settings) -> None:
        fail_load(path)

    io = SettingsIO(load=fail_load) if operation == "load" else SettingsIO(save=fail_save)
    application = ApplicationShell(tmp_path / "settings.toml", io)
    application.start()
    try:
        qtbot.waitUntil(lambda: application.operation is None)
        application.open_settings()
        dialog = application.dialog
        assert dialog is not None
        if operation == "save":
            dialog.model.setText("unsaved")
            dialog.save_button.click()
            qtbot.waitUntil(lambda: application.operation is None)
            assert dialog.model.text() == "unsaved"
            assert application.baseline.model == ""
        diagnostic = dialog.details.toPlainText()
        assert "RuntimeError" in diagnostic
        assert str(application.path) in diagnostic
        assert "private-setting-value" not in diagnostic
        assert dialog.save_button.isEnabled() == (operation == "save")
        assert application.window.reload_action.isEnabled()
        assert not application.path.exists()
    finally:
        if application.dialog is not None:
            application.dialog.set_values(application.baseline)
        application.window.close()
        qtbot.waitUntil(lambda: not application.window.isVisible())


def test_windows_fit_screen_and_long_error_keeps_actions_reachable(
    qtbot: QtBot, tmp_path: Path
) -> None:
    def fail_save(_path: Path, _settings: Settings) -> None:
        message = "diagnostic-path/" * 100
        raise SettingsError(message)

    application = ApplicationShell(tmp_path / "settings.toml", SettingsIO(save=fail_save))
    application.start()
    try:
        qtbot.waitUntil(lambda: application.operation is None)
        assert (
            application.window.screen()
            .availableGeometry()
            .contains(application.window.frameGeometry())
        )
        application.open_settings()
        dialog = application.dialog
        assert dialog is not None
        dialog.save_button.click()
        qtbot.waitUntil(lambda: application.operation is None)
        assert dialog.screen().availableGeometry().contains(dialog.frameGeometry())
        for button in (dialog.ok_button, dialog.save_button, dialog.close_button):
            assert button.isVisible()
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().topLeft()))
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        assert application.window.reload_action in application.window.file_menu.actions()
        assert dialog.details.toPlainText().endswith("diagnostic-path/")
    finally:
        application.window.close()
        qtbot.waitUntil(lambda: not application.window.isVisible())
