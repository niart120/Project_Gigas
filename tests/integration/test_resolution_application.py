from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from tests.integration.peers.controlled_capture import ControlledCapture
from tests.integration.test_app_lifetime import ControlledIO
from tests.integration.test_video_application import (
    application as application,  # noqa: PLC0414 -- shared controlled I/O fixture
)


def test_auto_resolution_closes_editor_before_cleanup_and_uses_new_frame_dimensions(
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
    assert not dialog.isVisible()
    assert shell.video.apply_state == "stopping"
    capture.complete_disconnect()
    qtbot.waitUntil(lambda: len(capture.connections) == 2)
    assert capture.connections[-1].width is None
    assert capture.connections[-1].height is None
    assert not dialog.isVisible()
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
    assert dialog.save_button.isEnabled()
