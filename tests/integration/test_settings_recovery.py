from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.game_io.input.config import resolve_serial_config
from gigas.game_io.input.serial import SerialPort
from gigas.storage.settings import load_settings
from gigas.ui.main_window import MainWindow
from gigas.ui.notifications import Notice
from tests.integration.peers.controlled_capture import ControlledCapture
from tests.integration.test_app_lifetime import ControlledIO
from tests.integration.test_app_lifetime import (
    controlled as controlled,  # noqa: PLC0414 -- shared controlled I/O fixture
)
from tests.integration.test_settings_apply_contract import (
    app as app,  # noqa: PLC0414 -- shared isolated application fixture
)

if TYPE_CHECKING:
    from gigas.game_io.input.config import SerialConfig


def test_stop_during_save_does_not_reconnect_after_late_success(
    controlled: tuple[ApplicationShell, ControlledIO],
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell, io = controlled
    dialog = shell.dialog
    assert dialog is not None
    applied: list[SerialConfig | None] = []
    stopped: list[bool] = []
    monkeypatch.setattr(shell.service, "apply_input", applied.append)
    monkeypatch.setattr(shell.service, "interrupt", lambda: stopped.append(True))
    assert not shell.window.connection.disconnect_action.isEnabled()
    dialog.set_serial_ports((SerialPort("COM3", "Controller"),))
    dialog.serial_port.setCurrentIndex(dialog.serial_port.findData("COM3"))
    dialog.serial_protocol.setCurrentIndex(dialog.serial_protocol.findData("pokecon"))
    dialog.save_button.click()
    qtbot.waitUntil(io.entered.is_set)
    assert not dialog.save_button.isEnabled()
    assert dialog.stop_button.isEnabled()

    dialog.stop_button.click()

    assert stopped == [True]
    io.release.set()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert load_settings(shell.path).serial_port == "COM3"
    assert shell.baseline.serial_port == "COM3"
    assert applied == []
    dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert applied == [resolve_serial_config("COM3", "pokecon", 9600)]


@pytest.mark.parametrize("succeeds", [False, True])
def test_settings_location_opens_own_directory_and_reports_failure(
    app: tuple[ApplicationShell, ControlledCapture],
    monkeypatch: pytest.MonkeyPatch,
    succeeds: bool,
) -> None:
    shell, _capture = app
    opened: list[Path] = []

    def open_url(url: QUrl) -> bool:
        opened.append(Path(url.toLocalFile()))
        return succeeds

    monkeypatch.setattr(QDesktopServices, "openUrl", open_url)
    shell.open_settings()
    assert shell.dialog is not None
    shell.dialog.open_folder_button.click()
    assert opened == [shell.path.parent]
    assert ("settings" in shell.window.notifications.notices) is not succeeds
    if not succeeds:
        details = shell.window.notifications.dialog
        assert details is not None
        assert details.isVisible()
        assert details.parentWidget() is shell.dialog
        assert details.details.toPlainText() == str(shell.path.parent)


def test_save_error_details_remain_accessible_from_settings(
    controlled: tuple[ApplicationShell, ControlledIO],
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell, io = controlled
    dialog = shell.dialog
    assert dialog is not None
    assert dialog.error_details_button.isHidden()
    io.fail = True
    io.release.set()
    dialog.model.setText("edited")
    dialog.observation_resolution.setCurrentIndex(dialog.observation_resolution.findData("hd"))
    dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert dialog.error_details_button.isVisible()
    dialog.error_details_button.click()
    details = shell.window.notifications.dialog
    assert details is not None
    assert details.isVisible()
    assert details.parentWidget() is dialog
    assert details.isModal()
    assert details.details.toPlainText() == shell.error
    assert dialog.model.text() == "edited"
    assert dialog.observation_resolution.currentData() == "hd"
    assert shell.baseline.observation_resolution == "full_hd"
    stopped: list[bool] = []
    monkeypatch.setattr(shell.service, "interrupt", lambda: stopped.append(True))
    assert not shell.window.connection.disconnect_action.isEnabled()
    stop = next(button for button in details.findChildren(QPushButton) if button.text() == "停止")
    stop.click()
    assert stopped == [True]
    assert details.isVisible()


def test_form_details_keep_current_error_after_another_settings_failure(
    controlled: tuple[ApplicationShell, ControlledIO],
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell, io = controlled
    dialog = shell.dialog
    assert dialog is not None
    io.fail = True
    io.release.set()
    dialog.model.setText("edited")
    dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    original = shell.window.notifications.notices["settings"]
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda _url: False)
    shell.open_settings()
    assert shell.dialog is not None
    shell.dialog.open_folder_button.click()
    latest = shell.window.notifications.notices["settings"]
    assert latest is not original
    details = shell.window.notifications.dialog
    assert details is not None
    assert details.notice is latest
    details.close()

    dialog.error_details_button.click()

    details = shell.window.notifications.dialog
    assert details is not None
    assert details.notice is original
    assert details.details.toPlainText() == shell.error
    assert shell.window.notifications.notices["settings"] is latest
    shell.window.notifications.entry_action.trigger()
    assert details.notice is latest


def test_direct_snapshot_preserves_history_and_copy_selection(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    notices = window.notifications
    current = Notice("settings", "保存失敗", "", "current error")
    notices.show_snapshot(current)
    details = notices.dialog
    assert details is not None
    assert details.notice is current
    assert details.sources.currentData() == "settings"
    assert not notices.notices
    assert not notices.entry_action.isEnabled()
    assert notices.banner.isHidden()
    cursor = details.details.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    details.details.setTextCursor(cursor)
    latest = Notice("settings", "場所を開けない", "", "folder error")
    notices.publish(latest)
    assert details.notice is current
    assert details.details.textCursor().selectedText() == current.details
    assert notices.notices["settings"] is latest


def test_device_failure_details_use_latest_notification(
    controlled: tuple[ApplicationShell, ControlledIO],
) -> None:
    shell, _io = controlled
    notices = shell.window.notifications
    notices.publish(Notice("input", "接続失敗", "", "old input"))
    latest = Notice("video", "接続失敗", "", "new video")
    notices.publish(latest)
    shell._show_settings_error()
    assert notices.dialog is not None
    assert notices.dialog.notice is latest
