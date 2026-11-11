from collections.abc import Iterator
from dataclasses import replace

import cv2
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from gigas.app.video import VideoController
from gigas.storage.settings import Settings
from gigas.ui.main_window import MainWindow
from tests.integration.peers.controlled_capture import ControlledCapture


@pytest.fixture
def video(qtbot: QtBot) -> Iterator[tuple[MainWindow, VideoController, ControlledCapture]]:
    window = MainWindow()
    qtbot.addWidget(window)
    capture = ControlledCapture()
    controller = VideoController(window.video, capture, window)
    controller.failed.connect(window.notifications.publish)
    controller.start()
    controller.set_available(True)
    window.show()
    yield window, controller, capture
    controller.shutdown()
    assert controller.shutdown_complete
    window.allow_close = True
    window.close()


def test_view_menu_opens_successful_capture_information_without_a_failure_notice(
    video: tuple[MainWindow, VideoController, ControlledCapture],
) -> None:
    window, controller, capture = video
    controller.apply_capture(Settings(capture_device="camera", capture_fps=60))
    capture.stream(sequence=7, actual_size=(640, 480))
    capture.state = replace(
        capture.state,
        backend=cv2.CAP_MSMF,
        reported_fps=59.94,
        measured_fps=47.25,
        diagnostics=("DirectShow: open failed",),
    )
    controller._poll()

    assert window.video.info_action in window.view_menu.actions()
    window.video.info_action.trigger()
    dialog = window.video.info_dialog
    assert dialog is not None
    assert dialog.isVisible()
    assert not dialog.isModal()
    assert dialog.details.isReadOnly()
    text = dialog.details.toPlainText()
    for value in ("1920x1080", "60", "640x480", "59.94", "47.25", "MSMF", "1400"):
        assert value in text
    assert "世代: 1" in text
    assert "連番: 7" in text
    assert "終了確認: none" in text
    assert "DirectShow: open failed" in text
    assert not window.notifications.notices
    assert not window.notifications.banner.isVisible()


def test_information_stays_frozen_for_copy_until_explicit_reopen(
    video: tuple[MainWindow, VideoController, ControlledCapture], qtbot: QtBot
) -> None:
    window, controller, capture = video
    controller.apply_capture(Settings(capture_device="camera"))
    capture.stream(sequence=4, actual_size=(32, 24))
    capture.state = replace(capture.state, diagnostics=("diagnostic\n" * 100,))
    window.video.info_action.trigger()
    dialog = window.video.info_dialog
    assert dialog is not None
    original = dialog.details.toPlainText()
    cursor = dialog.details.textCursor()
    cursor.setPosition(20)
    cursor.setPosition(80, QTextCursor.MoveMode.KeepAnchor)
    dialog.details.setTextCursor(cursor)
    dialog.details.verticalScrollBar().setValue(25)
    selected = dialog.details.textCursor().selectedText()
    scroll = dialog.details.verticalScrollBar().value()

    controller.apply_capture(Settings(capture_device="camera", capture_resolution="hd"))
    capture.stream(sequence=9, actual_size=(1280, 720))
    controller._poll()
    assert dialog.details.toPlainText() == original
    assert dialog.details.textCursor().selectedText() == selected
    assert dialog.details.verticalScrollBar().value() == scroll
    dialog.details.selectAll()
    qtbot.keyClick(dialog.details, Qt.Key.Key_C, modifier=Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == original
    qtbot.keyClicks(dialog.details, "cannot edit")
    assert dialog.details.toPlainText() == original

    window.video.info_action.trigger()
    assert window.video.info_dialog is dialog
    assert "1280x720" in dialog.details.toPlainText()
    assert "世代: 2" in dialog.details.toPlainText()
    assert "連番: 9" in dialog.details.toPlainText()


def test_idle_and_automatic_information_do_not_request_or_guess_a_resolution(
    video: tuple[MainWindow, VideoController, ControlledCapture],
) -> None:
    window, controller, capture = video
    window.video.info_action.trigger()
    dialog = window.video.info_dialog
    assert dialog is not None
    assert capture.connections == []
    assert capture.refreshes == 0
    assert capture.disconnects == 0
    assert "未要求" in dialog.details.toPlainText()
    assert "未取得" in dialog.details.toPlainText()

    controller.apply_capture(Settings(capture_device="camera", capture_resolution="auto"))
    capture.stream(actual_size=(720, 480))
    window.video.info_action.trigger()
    assert "要求解像度: 自動" in dialog.details.toPlainText()
    assert "720x480" in dialog.details.toPlainText()
    assert "1920x1080" not in dialog.details.toPlainText()
    assert capture.refreshes == 0
    assert len(capture.connections) == 1


def test_owned_information_closes_only_after_main_window_close_is_allowed(
    video: tuple[MainWindow, VideoController, ControlledCapture],
) -> None:
    window, _controller, _capture = video
    window.video.info_action.trigger()
    dialog = window.video.info_dialog
    assert dialog is not None
    window.close()
    assert dialog.isVisible()
    window.allow_close = True
    window.close()
    assert not dialog.isVisible()
