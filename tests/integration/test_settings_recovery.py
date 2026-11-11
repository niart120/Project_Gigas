from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
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


@pytest.mark.parametrize("succeeds", [False, True])
def test_settings_location_menu_opens_own_directory_and_reports_failure(
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
    shell.window.settings_location_action.trigger()
    assert opened == [shell.path.parent]
    assert ("settings" in shell.window.notifications.notices) is not succeeds


def test_save_error_details_remain_accessible_from_settings(
    controlled: tuple[ApplicationShell, ControlledIO], qtbot: QtBot
) -> None:
    shell, io = controlled
    dialog = shell.dialog
    assert dialog is not None
    assert dialog.error_details_button.isHidden()
    io.fail = True
    io.release.set()
    dialog.model.setText("edited")
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
    shell.window.settings_location_action.trigger()
    latest = shell.window.notifications.notices["settings"]
    assert latest is not original

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
