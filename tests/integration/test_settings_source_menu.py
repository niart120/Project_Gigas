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
    dialog.close_button.click()
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
    else:
        assert shell.baseline.capture_device == ""
        assert load_settings(shell.path).capture_device == ""
        assert capture.disconnects == 1
        assert capture.snapshot().frame is None
        capture.complete_disconnect()
        qtbot.waitUntil(lambda: shell.video.apply_state == "complete")
        assert shell.window.video_device_actions[""].isChecked()
    assert len(capture.connections) == 1

    shell.open_settings()
    assert dialog.capture_choices.currentData() == shell.baseline.capture_device


def test_device_menus_apply_only_the_selected_connection(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO],
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell, capture, _io = application
    assert shell.dialog is not None
    shell.dialog.close_button.click()
    applied = []
    monkeypatch.setattr(shell.service, "apply_input", applied.append)
    shell.window.video_device_actions["path-a"].trigger()
    assert applied == []
    before = len(capture.connections)
    action = next(a for a in shell.window.input_protocol_menu.actions() if a.data() == "3ds")
    action.trigger()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.baseline.serial_protocol == "3ds"
    assert shell.baseline.serial_baudrate == 115200
    assert len(capture.connections) == before
    assert applied == [None]
