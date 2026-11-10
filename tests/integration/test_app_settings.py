from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.storage.settings import Settings, load_settings


@pytest.fixture
def shell(qtbot: QtBot, tmp_path: Path) -> Iterator[ApplicationShell]:
    application = ApplicationShell(tmp_path / "settings.toml")
    application.start()
    qtbot.waitUntil(lambda: application.operation is None)
    yield application
    if application.window.isVisible():
        application.window.close()
        qtbot.waitUntil(lambda: not application.window.isVisible())


def test_settings_reopens_same_dialog_with_edits(shell: ApplicationShell, qtbot: QtBot) -> None:
    shell.window.settings_button.click()
    dialog = shell.dialog
    assert dialog is not None
    qtbot.waitUntil(dialog.isVisible)
    dialog.model.setPlainText("edited")

    shell.window.settings_button.click()

    assert shell.dialog is dialog
    assert dialog.model.toPlainText() == "edited"
    assert not dialog.isModal()


def test_defaults_only_written_on_explicit_save(shell: ApplicationShell, qtbot: QtBot) -> None:
    shell.window.settings_button.click()
    dialog = shell.dialog
    assert dialog is not None
    assert not shell.path.exists()
    dialog.close()
    assert not shell.path.exists()
    shell.window.settings_button.click()
    dialog.save_button.click()
    qtbot.waitUntil(shell.path.exists)
    assert load_settings(shell.path) == Settings()


def test_all_settings_round_trip_and_clear(shell: ApplicationShell, qtbot: QtBot) -> None:
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    values = Settings(" 映像\n入力 ", " COM3 ", "unknown-model", "custom-effort", "api_key")
    dialog.capture_device.setPlainText(values.capture_device)
    dialog.serial_port.setPlainText(values.serial_port)
    dialog.model.setPlainText(values.model)
    dialog.effort.setPlainText(values.effort)
    dialog.auth_method.setCurrentIndex(1)
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
        assert other.dialog.capture_device.toPlainText() == values.capture_device
        assert other.dialog.model.toPlainText() == values.model
        assert other.dialog.auth_method.currentData() == "api_key"
        other.dialog.capture_device.clear()
        other.dialog.serial_port.clear()
        other.dialog.model.clear()
        other.dialog.effort.clear()
        other.dialog.save_button.click()
        qtbot.waitUntil(lambda: other.operation is None)
        assert load_settings(shell.path) == Settings(auth_method="api_key")
    finally:
        other.window.close()
        qtbot.waitUntil(lambda: not other.window.isVisible())
