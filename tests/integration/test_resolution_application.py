import pytest
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from tests.integration.peers.controlled_capture import ControlledCapture
from tests.integration.test_app_lifetime import ControlledIO
from tests.integration.test_video_application import (
    application as application,  # noqa: PLC0414 -- shared controlled I/O fixture
)


def test_auto_resolution_waits_for_old_cleanup_and_first_new_frame(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO], qtbot: QtBot
) -> None:
    shell, capture, _io = application
    dialog = shell.dialog
    assert dialog is not None
    original = capture.snapshot().frame
    assert original is not None
    dialog.capture_resolution.setCurrentIndex(dialog.capture_resolution.findData("auto"))
    dialog.ok_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert len(capture.connections) == 1
    assert capture.snapshot().frame is None
    assert dialog.isVisible()
    assert shell.video.apply_state == "stopping"
    capture.complete_disconnect()
    qtbot.waitUntil(lambda: len(capture.connections) == 2)
    assert capture.connections[-1].width is None
    assert capture.connections[-1].height is None
    assert dialog.isVisible()
    capture.stream(actual_size=(48, 32))
    qtbot.waitUntil(lambda: not dialog.isVisible())
    frame = capture.snapshot().frame
    assert frame is not None
    assert frame.generation > original.generation
    assert (frame.width, frame.height) == (48, 32)


def test_display_changes_keep_actual_frame_and_requested_resolution(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO], qtbot: QtBot
) -> None:
    shell, capture, _io = application
    capture.stream(actual_size=(32, 24))
    dialog = shell.dialog
    assert dialog is not None
    frame = capture.snapshot().frame
    assert frame is not None
    dialog.preview_mode.setCurrentIndex(dialog.preview_mode.findData(False))
    dialog.preview_fps.setCurrentIndex(dialog.preview_fps.findData(15))
    dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert len(capture.connections) == 1
    assert capture.disconnects == 0
    assert capture.snapshot().frame is frame
    assert not shell.baseline.preview_letterbox
    assert not dialog.save_button.isEnabled()


@pytest.mark.parametrize("acquisition_field", ["resolution", "fps"])
def test_current_device_hint_only_compares_device_selection(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO],
    qtbot: QtBot,
    acquisition_field: str,
) -> None:
    shell, capture, _io = application
    dialog = shell.dialog
    assert dialog is not None
    assert dialog.capture_connection_status.isHidden()
    if acquisition_field == "resolution":
        dialog.capture_resolution.setCurrentIndex(dialog.capture_resolution.findData("auto"))
    else:
        dialog.capture_fps.setCurrentIndex(dialog.capture_fps.findData(30))
    assert dialog.save_button.isEnabled()
    assert dialog.capture_connection_status.isHidden()
    assert len(capture.connections) == 1

    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData("path-b"))
    assert not dialog.capture_connection_status.isHidden()
    assert dialog.capture_connection_status.text() == "現在: First"
    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData("path-a"))
    assert dialog.capture_connection_status.isHidden()

    dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.video.apply_state == "stopping"
    assert not dialog.capture_connection_status.isHidden()
    assert dialog.capture_connection_status.text() == shell.window.video.status.text()
    capture.complete_disconnect()
    qtbot.waitUntil(lambda: len(capture.connections) == 2)
    capture.stream()
    qtbot.waitUntil(lambda: shell.video.apply_state == "complete")
    assert dialog.capture_connection_status.isHidden()
