import errno
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from gigas.app.settings_worker import SettingsIO
from gigas.app.shell import ApplicationShell
from gigas.game_io.capture.models import DeviceInfo
from gigas.game_io.input.serial import SerialPort
from gigas.storage.settings import Settings, SettingsError, load_settings, save_settings
from gigas.ui.settings_dialog import SettingsDialog


@pytest.fixture
def shell(
    qtbot: QtBot,
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[ApplicationShell]:
    initial = getattr(request, "param", None)
    if isinstance(initial, Settings):
        save_settings(tmp_path / "settings.toml", initial)
    elif isinstance(initial, str):
        (tmp_path / "settings.toml").write_text(initial, encoding="utf-8")
    application = ApplicationShell(tmp_path / "settings.toml")
    monkeypatch.setattr(application.service, "apply_input", lambda _config: None)
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
    qtbot.mouseClick(shell.window.settings_button, Qt.MouseButton.LeftButton)
    dialog = shell.dialog
    assert dialog is not None
    qtbot.waitUntil(dialog.isVisible)
    dialog.model.setText("edited")

    shell.window.settings_action.trigger()

    assert shell.dialog is dialog
    assert dialog.model.text() == "edited"
    assert dialog.isModal()
    dialog.close_button.click()
    qtbot.keyClick(shell.window, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(dialog.isVisible)


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
    assert load_settings(shell.path) == Settings(serial_baudrate=9600)


def test_all_settings_round_trip_and_offline_clears_device(
    shell: ApplicationShell, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    dialog.set_values(Settings(auth_method="api_key"))
    values = Settings(
        " 映像\n入力 ",
        " COM3 ",
        "unknown-model",
        "custom-effort",
        "api_key",
        serial_protocol="pokecon",
        serial_baudrate=57600,
    )
    dialog.set_capture_devices((DeviceInfo("Camera", values.capture_device),))
    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData(values.capture_device))
    dialog.set_serial_ports((SerialPort(values.serial_port, "Controller"),))
    dialog.serial_port.setCurrentIndex(dialog.serial_port.findData(values.serial_port))
    dialog.serial_protocol.setCurrentIndex(dialog.serial_protocol.findData(values.serial_protocol))
    dialog.serial_baudrate.setCurrentIndex(dialog.serial_baudrate.findData(values.serial_baudrate))
    dialog.model.setText(values.model)
    dialog.effort.setText(values.effort)
    dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert load_settings(shell.path) == values
    shell.window.close()
    qtbot.waitUntil(lambda: not shell.window.isVisible())

    other = ApplicationShell(shell.path)
    monkeypatch.setattr(other.service, "apply_input", lambda _config: None)
    other.start()
    try:
        qtbot.waitUntil(lambda: other.operation is None)
        other.open_settings()
        assert other.dialog is not None
        assert other.dialog.values() == values
        assert other.dialog.capture_choices.currentData() == values.capture_device
        assert other.dialog.capture_choices.findData(values.capture_device) >= 0
        assert other.dialog.model.text() == values.model
        assert other.dialog.auth_method.currentData() == "api_key"
        other.dialog.capture_choices.setCurrentIndex(0)
        other.dialog.serial_port.setCurrentIndex(0)
        other.dialog.model.clear()
        other.dialog.effort.clear()
        other.dialog.save_button.click()
        qtbot.waitUntil(lambda: other.operation is None)
        assert load_settings(shell.path) == Settings(
            auth_method="api_key", serial_protocol="pokecon", serial_baudrate=57600
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
        qtbot.waitUntil(lambda: not shell.window.isVisible())


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


@pytest.mark.parametrize("shell", ["[broken"], indirect=True)
def test_failed_initial_load_preserves_original_and_exposes_details(
    shell: ApplicationShell,
) -> None:
    shell.open_settings()
    assert shell.dialog is not None
    assert not shell.ready
    assert not shell.dialog.save_button.isEnabled()
    shell.dialog.error_details_button.click()
    details = shell.window.notifications.dialog
    assert details is not None
    assert str(shell.path) in details.details.toPlainText()
    assert shell.path.read_text(encoding="utf-8") == "[broken"
    details.close()
    shell.dialog.close_button.click()
    assert not shell.dialog.isVisible()


@pytest.mark.parametrize("shell", [Settings(model="original")], indirect=True)
def test_save_failure_preserves_edits_and_reports_cleanup(
    shell: ApplicationShell, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        assert "remove temporary file" in shell.window.notifications.notices["settings"].details
        assert load_settings(shell.path).model == "original"

    shell.dialog.model.setText("retry")
    shell.dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert load_settings(shell.path).model == "retry"
    assert not shell.error
    assert not shell.dirty


@pytest.mark.parametrize("shell", [Settings(model="first\r\nsecond\u00a0word")], indirect=True)
def test_unedited_text_preserves_crlf_and_nonbreaking_space(
    shell: ApplicationShell, qtbot: QtBot
) -> None:
    original = Settings(model="first\r\nsecond\u00a0word")
    shell.open_settings()
    assert not shell.dirty
    assert shell.dialog is not None
    shell.dialog.effort.setText("high")
    shell.apply_settings()
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
        diagnostic = application.window.notifications.notices["settings"].details
        assert "RuntimeError" in diagnostic
        assert str(application.path) in diagnostic
        assert "private-setting-value" not in diagnostic
        assert dialog.save_button.isEnabled() == (operation == "save")
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
        assert application.window.notifications.notices["settings"].details.endswith(
            "diagnostic-path/"
        )
    finally:
        application.window.close()
        qtbot.waitUntil(lambda: not application.window.isVisible())


@pytest.mark.parametrize(
    ("protocol", "rate"), [("ch552", 9600), ("pokecon", 9600), ("3ds", 115200)]
)
def test_serial_editor_keeps_draft_and_resolves_protocol_defaults(
    qtbot: QtBot, tmp_path: Path, protocol: str, rate: int
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    original = Settings(serial_port="COM7", serial_protocol=protocol)
    dialog.set_values(original)
    assert not dialog.serial_port.isEditable()
    assert dialog.serial_port.currentData() == "COM7"
    assert dialog.values().serial_baudrate == rate
    dialog.set_serial_ports((SerialPort("COM3", "Controller"),))
    assert dialog.serial_port.currentData() == "COM7"
    dialog.serial_port.setCurrentIndex(dialog.serial_port.findData("COM3"))
    dialog.set_serial_ports((), searching=True)
    dialog.set_serial_ports((), error="enumeration failed")
    assert dialog.serial_port.currentData() == "COM3"
    dialog.serial_protocol.setCurrentIndex(dialog.serial_protocol.findData("3ds"))
    assert dialog.values().serial_baudrate == 115200
    dialog.serial_baudrate.setCurrentIndex(dialog.serial_baudrate.findData(57600))
    values = dialog.values()
    dialog.set_values(values)
    assert dialog.values() == values
    dialog.serial_protocol.setCurrentIndex(dialog.serial_protocol.findData("ch552"))
    assert dialog.serial_baudrate.count() == 1
    assert dialog.values().serial_baudrate == 9600
    refreshed = []
    dialog.serial_refresh_requested.connect(lambda: refreshed.append(True))
    dialog.serial_refresh_button.click()
    dialog.set_editable(False)
    assert not dialog.serial_port.isEnabled()
    assert not dialog.serial_protocol.isEnabled()
    assert not dialog.serial_baudrate.isEnabled()
    assert not dialog.serial_refresh_button.isEnabled()
    assert refreshed == [True]
