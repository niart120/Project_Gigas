from dataclasses import replace
from pathlib import Path

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.app.video import VideoController
from gigas.runtime.codex.models import SessionStatus
from gigas.services.codex import ServiceEvent, ServiceResult
from gigas.storage.settings import Settings, save_settings
from gigas.ui.main_window import MainWindow
from gigas.ui.notifications import Notice
from gigas.ui.video_panel import VideoPanel
from tests.integration.peers.controlled_capture import ControlledCapture
from tests.integration.test_app_lifetime import ControlledIO
from tests.integration.test_app_lifetime import (
    controlled as controlled,  # noqa: PLC0414 -- controlled settings I/O
)


def test_notification_history_is_reopened_from_view_menu_after_dismissal(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    notices = window.notifications
    assert notices.entry_action in window.view_menu.actions()
    assert not notices.entry_action.isEnabled()
    notice = Notice("video", "映像接続失敗", "", "diagnostic")
    notices.publish(notice)
    notices.dismiss_button.click()
    assert not notices.banner.isVisible()
    notices.entry_action.trigger()
    assert notices.dialog is not None
    assert notices.dialog.isVisible()
    assert notices.dialog.notice is notice


def test_failures_keep_controls_reachable_and_history_after_recovery(
    controlled: tuple[ApplicationShell, ControlledIO], qtbot: QtBot
) -> None:
    shell, io = controlled
    panel = shell.window.connection
    panel.activate(1)
    panel.handle_event(ServiceEvent(1, SessionStatus("running")))
    io.fail = True
    io.release.set()
    assert shell.dialog is not None
    shell.dialog.model.setText("edited")
    shell.dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    notices = shell.window.notifications
    original = notices.notices["settings"]
    assert original.details == shell.error
    assert panel.interrupt_button.isEnabled()
    assert panel.disconnect_action.isEnabled()
    notices.dismiss_button.click()
    assert not notices.banner.isVisible()
    assert notices.entry_action.isEnabled()
    panel.handle_result(ServiceResult(1, "send", "send failed"))
    io.fail = False
    shell.dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert not shell.error
    assert set(notices.notices) == {"settings", "codex"}
    assert notices.notices["settings"] is original


def test_details_keep_copy_snapshot_until_explicitly_reopened(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    notices = window.notifications
    first = Notice("video", "映像接続失敗", "接続を確認できませんでした。", "line\n" * 150)
    notices.publish(first)
    notices.entry_action.trigger()
    dialog = notices.dialog
    assert dialog is not None
    assert dialog.isVisible()
    assert not dialog.isModal()
    cursor = dialog.details.textCursor()
    cursor.setPosition(30)
    cursor.setPosition(90, QTextCursor.MoveMode.KeepAnchor)
    dialog.details.setTextCursor(cursor)
    dialog.details.verticalScrollBar().setValue(25)
    selected = dialog.details.textCursor().selectedText()
    scroll = dialog.details.verticalScrollBar().value()
    notices.publish(Notice("video", "別の失敗", "再試行に失敗しました。", "new video"))
    notices.publish(Notice("settings", "保存失敗", "設定を適用できませんでした。", "new save"))
    assert dialog.notice is first
    assert dialog.details.textCursor().selectedText() == selected
    assert dialog.details.verticalScrollBar().value() == scroll
    dialog.close()
    notices.show_details("video")
    assert dialog.details.toPlainText() == "new video"
    assert dialog.sources.findData("settings") >= 0
    dialog.sources.setCurrentIndex(dialog.sources.findData("settings"))
    dialog.sources.activated.emit(dialog.sources.currentIndex())
    assert dialog.details.toPlainText() == "new save"
    window.allow_close = True
    window.close()
    assert not dialog.isVisible()


def test_notification_does_not_steal_prompt_focus(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.activateWindow()
    window.connection.prompt.setFocus()
    qtbot.waitUntil(lambda: QApplication.focusWidget() is window.connection.prompt)
    window.notifications.publish(Notice("video", "接続失敗", "接続を確認できませんでした。", "x"))
    assert QApplication.focusWidget() is window.connection.prompt
    assert window.notifications.dialog is None


def test_codex_correlates_event_result_but_reports_each_new_attempt(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    panel = window.connection
    published: list[object] = []
    window.notifications.published.connect(published.append)
    panel.activate(1)
    panel.handle_event(ServiceEvent(1, SessionStatus("login_required")))
    assert not published
    panel.handle_event(
        ServiceEvent(1, SessionStatus("login_required", detail="failed", failure_id=1))
    )
    panel.handle_result(ServiceResult(1, "login", "failed", failure_id=1))
    assert len(published) == 1
    panel.handle_event(
        ServiceEvent(1, SessionStatus("login_required", detail="failed", failure_id=2))
    )
    panel.handle_result(ServiceResult(1, "login", "failed", failure_id=2))
    panel.handle_result(ServiceResult(1, "login", "failed"))
    panel.handle_result(ServiceResult(1, "login", "failed"))
    assert len(published) == 4
    panel.activate(2)
    panel.handle_result(ServiceResult(1, "login", "stale", failure_id=3))
    panel.handle_event(ServiceEvent(2, SessionStatus("error", detail="new", failure_id=1)))
    assert len(published) == 5
    panel.handle_event(ServiceEvent(2, SessionStatus("error", detail="external error")))
    assert window.notifications.notices["codex"].details == "external error"


def test_video_polling_deduplicates_but_refresh_and_rejected_connect_notify_again(
    qtbot: QtBot,
) -> None:
    panel = VideoPanel()
    qtbot.addWidget(panel)
    capture = ControlledCapture()
    capture.start()
    controller = VideoController(panel, capture)
    controller.set_available(True)
    published: list[object] = []
    controller.failed.connect(published.append)
    for expected in (1, 2):
        controller.refresh_devices()
        capture.complete_enumeration(error="enumeration failed")
        for _ in range(3):
            controller.timer.timeout.emit()
        assert len(published) == expected
    capture.state = replace(capture.state, phase="idle", error="")
    controller.timer.timeout.emit()
    capture.accept_connection = False
    for expected in (3, 4):
        controller.apply_capture(Settings(capture_device="path"))
        controller.timer.timeout.emit()
        assert len(published) == expected
    assert not capture.connections


def test_long_model_and_diagnostic_do_not_expand_main_window(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(800, 550)
    window.show()
    panel = window.connection
    panel.activate(1)
    model = "<b>model</b>" * 200
    panel.handle_event(ServiceEvent(1, SessionStatus("running", model, "high")))
    panel.handle_result(ServiceResult(1, "send", "failure/" * 500))
    QApplication.processEvents()
    assert window.width() <= 800
    assert panel.model.text() == f"{model} · high"
    assert panel.model.accessibleName() == panel.model.text()
    assert panel.status.text() == "実行中"
    window.notifications.show_details()
    dialog = window.notifications.dialog
    assert dialog is not None
    assert dialog.details.toPlainText() == "failure/" * 500
    assert dialog.width() <= 800


def test_shutdown_notice_dismissal_does_not_confirm_cleanup(qtbot: QtBot, tmp_path: Path) -> None:
    capture = ControlledCapture()
    path = tmp_path / "settings.toml"
    save_settings(path, Settings(capture_device="path"))
    shell = ApplicationShell(path, capture_service=capture)
    shell.start()
    notices = shell.window.notifications
    published: list[object] = []
    notices.published.connect(published.append)
    try:
        qtbot.waitUntil(lambda: shell.operation is None)
        capture.finish_shutdown = False
        shell.window.close()
        capture.alive = False
        capture.state = replace(
            capture.state, phase="error", cleanup="unconfirmed", error="child alive"
        )
        qtbot.waitUntil(lambda: "shutdown" in notices.notices)
        notices.show_details("shutdown")
        dialog = notices.dialog
        assert dialog is not None
        assert "child alive" in dialog.details.toPlainText()
        notices.dismiss_button.click()
        for _ in range(3):
            shell.video.timer.timeout.emit()
            shell._finish_shutdown()
        assert sum(getattr(notice, "source", None) == "shutdown" for notice in published) == 1
        assert shell.window.isVisible()
        assert not shell.window.allow_close
        shell.open_settings()
        assert shell.dialog is None
        capture.state = replace(capture.state, phase="closed", cleanup="graceful", error="")
        qtbot.waitUntil(lambda: not shell.window.isVisible())
        assert not dialog.isVisible()
        assert "shutdown" in notices.notices
    finally:
        capture.finish_shutdown = True
        capture.alive = False
        capture.state = replace(capture.state, phase="closed", cleanup="graceful", error="")
        shell._finish_shutdown()
        qtbot.waitUntil(lambda: not shell.window.isVisible())


def test_failure_after_successful_application_reports_acquisition_not_application(
    qtbot: QtBot,
) -> None:
    panel = VideoPanel()
    qtbot.addWidget(panel)
    capture = ControlledCapture()
    capture.start()
    controller = VideoController(panel, capture)
    controller.set_available(True)
    published: list[Notice] = []
    controller.failed.connect(published.append)
    controller.apply_capture(Settings(capture_device="path"))
    capture.stream()
    controller.timer.timeout.emit()
    assert controller.apply_state == "complete"
    capture.state = replace(capture.state, phase="error", cleanup="graceful", error="frame timeout")
    controller.timer.timeout.emit()
    assert published[-1].title == "映像取得失敗"


def test_service_exit_after_discovery_is_not_a_new_discovery_failure(qtbot: QtBot) -> None:
    panel = VideoPanel()
    qtbot.addWidget(panel)
    capture = ControlledCapture()
    controller = VideoController(panel, capture)
    controller.start()
    controller.timer.stop()
    published: list[Notice] = []
    controller.failed.connect(published.append)
    controller.refresh_devices()
    capture.complete_enumeration()
    controller.timer.timeout.emit()
    capture.alive = False
    controller.timer.timeout.emit()
    assert published[-1].title == "映像サービス終了"


def test_service_exit_is_not_hidden_by_an_earlier_local_request_failure(qtbot: QtBot) -> None:
    panel = VideoPanel()
    qtbot.addWidget(panel)
    capture = ControlledCapture()
    capture.accept_connection = False
    controller = VideoController(panel, capture)
    controller.start()
    controller.timer.stop()
    controller.set_available(True)
    published: list[Notice] = []
    controller.failed.connect(published.append)
    controller.apply_capture(Settings(capture_device="path"))
    assert len(published) == 1
    capture.alive = False
    controller.timer.timeout.emit()
    assert len(published) == 2
    assert published[-1].title == "映像サービス終了"
    assert published[-1].details == "映像サービス終了"
