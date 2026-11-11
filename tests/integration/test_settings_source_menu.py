import pytest
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.storage.settings import load_settings
from tests.integration.peers.controlled_capture import ControlledCapture
from tests.integration.test_app_lifetime import ControlledIO
from tests.integration.test_video_application import (
    application as application,  # noqa: PLC0414 -- shared controlled I/O fixture
)


@pytest.mark.parametrize("save_fails", [False, True])
def test_menu_offline_selection_saves_before_stopping_and_preserves_stream_on_failure(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO],
    qtbot: QtBot,
    save_fails: bool,
) -> None:
    shell, capture, io = application
    dialog = shell.dialog
    assert dialog is not None
    original = capture.snapshot().frame
    assert original is not None
    io.fail = save_fails
    io.release.clear()
    shell.window.video_device_actions[""].trigger()
    assert shell.operation == "save"
    qtbot.waitUntil(io.entered.is_set)
    assert capture.snapshot().frame is original
    assert capture.disconnects == 0
    assert shell.window.video_device_actions["path-a"].isChecked()
    assert load_settings(shell.path).capture_device == "path-a"
    io.release.set()
    qtbot.waitUntil(lambda: shell.operation is None)
    if save_fails:
        assert shell.error
        assert shell.baseline.capture_device == "path-a"
        assert load_settings(shell.path).capture_device == "path-a"
        assert capture.snapshot().frame is original
        assert capture.disconnects == 0
        assert shell.window.video_device_actions["path-a"].isChecked()
        assert dialog.capture_target == "path-a"
    else:
        assert shell.baseline.capture_device == ""
        assert load_settings(shell.path).capture_device == ""
        assert capture.disconnects == 1
        assert capture.snapshot().frame is None
        assert dialog.capture_target == ""
        capture.complete_disconnect()
        qtbot.waitUntil(lambda: shell.video.apply_state == "complete")
        assert shell.window.video_device_actions[""].isChecked()
        assert not shell.window.video.connect_action.isEnabled()
    assert len(capture.connections) == 1


def test_menu_offline_selection_clears_remembered_device_after_temporary_stop(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO], qtbot: QtBot
) -> None:
    shell, capture, io = application
    dialog = shell.dialog
    assert dialog is not None
    capture.finish_disconnect = True
    shell.window.video.disconnect_action.trigger()
    capture.complete_enumeration(capture.state.devices)
    shell.video.timer.timeout.emit()
    assert shell.baseline.capture_device == "path-a"
    assert load_settings(shell.path).capture_device == "path-a"
    assert io.saves == 0
    assert shell.window.video_device_actions[""].isChecked()
    assert shell.window.video_device_actions[""].isEnabled()
    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData("path-a"))
    assert not shell.dirty
    shell.window.video_device_actions[""].trigger()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert io.saves == 1
    assert shell.baseline.capture_device == ""
    assert load_settings(shell.path).capture_device == ""
    assert dialog.capture_target == ""
    assert capture.disconnects == 1
    assert len(capture.connections) == 1


def test_menu_temporary_stop_keeps_saved_source_available_for_restart(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO], qtbot: QtBot
) -> None:
    shell, capture, io = application
    capture.finish_disconnect = True
    shell.window.video.disconnect_action.trigger()
    capture.complete_enumeration(capture.state.devices)
    qtbot.waitUntil(shell.window.video.connect_action.isEnabled)
    assert shell.baseline.capture_device == "path-a"
    assert load_settings(shell.path).capture_device == "path-a"
    assert io.saves == 0
    shell.window.video.connect_action.trigger()
    assert capture.connections[-1].device_path == "path-a"
    assert len(capture.connections) == 2


@pytest.mark.parametrize("cancel_with_stop", [False, True])
def test_menu_target_updates_open_editor_only_after_save_without_reviving_cancelled_target(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO],
    qtbot: QtBot,
    cancel_with_stop: bool,
) -> None:
    shell, capture, io = application
    dialog = shell.dialog
    assert dialog is not None
    io.release.clear()
    shell.window.video_device_actions["path-b"].trigger()
    qtbot.waitUntil(io.entered.is_set)
    assert dialog.capture_target == "path-a"
    if cancel_with_stop:
        shell.window.video.disconnect_action.trigger()
        assert dialog.capture_target == ""
    io.release.set()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert load_settings(shell.path).capture_device == "path-b"
    assert dialog.capture_target == ("" if cancel_with_stop else "path-b")
    capture.complete_disconnect()
    shell.video.timer.timeout.emit()
    if cancel_with_stop:
        capture.complete_enumeration(capture.state.devices)
        shell.video.timer.timeout.emit()
        assert len(capture.connections) == 1
    else:
        qtbot.waitUntil(lambda: len(capture.connections) == 2)
        assert capture.connections[-1].device_path == "path-b"
        capture.stream()
        qtbot.waitUntil(lambda: not shell.video.applying)
