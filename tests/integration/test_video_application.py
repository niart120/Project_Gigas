from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from gigas.app.settings_worker import SettingsIO
from gigas.app.shell import ApplicationShell
from gigas.game_io.capture import CaptureService
from gigas.game_io.capture.models import CaptureOptions, DeviceInfo
from gigas.storage.settings import Settings, load_settings, save_settings
from tests.integration.peers.capture_peer import PeerBackend
from tests.integration.peers.controlled_capture import ControlledCapture
from tests.integration.test_app_lifetime import ControlledIO


@pytest.fixture
def application(
    qtbot: QtBot, tmp_path: Path
) -> Iterator[tuple[ApplicationShell, ControlledCapture, ControlledIO]]:
    path = tmp_path / "settings.toml"
    save_settings(path, Settings(capture_device="path-a"))
    capture = ControlledCapture()
    io = ControlledIO()
    io.release.set()
    shell = ApplicationShell(path, SettingsIO(io.load, io.save), capture_service=capture)
    shell.start()
    qtbot.waitUntil(lambda: shell.operation is None)
    shell.connect_video()
    capture.state = replace(
        capture.state,
        devices=(DeviceInfo("First", "path-a"), DeviceInfo("Second", "path-b")),
        devices_revision=1,
    )
    capture.stream()
    qtbot.waitUntil(shell.window.video.disconnect_button.isEnabled)
    shell.open_settings()
    assert shell.dialog is not None
    qtbot.waitUntil(
        lambda: shell.dialog is not None and shell.dialog.capture_choices.findData("path-b") >= 0
    )
    capture.finish_disconnect = False
    yield shell, capture, io
    io.release.set()
    qtbot.waitUntil(lambda: shell.operation is None)
    capture.finish_disconnect = True
    capture.finish_shutdown = True
    capture.state = replace(capture.state, cleanup="graceful")
    if shell.dialog is not None:
        shell.dialog.set_values(shell.baseline)
    if shell.confirmation is not None:
        shell.confirmation.reject()
    if shell.window.isVisible():
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())


def choose_second(shell: ApplicationShell) -> None:
    """Edit both acquisition fields without submitting them."""
    assert shell.dialog is not None
    shell.dialog.capture_choices.setCurrentIndex(shell.dialog.capture_choices.findData("path-b"))
    shell.dialog.capture_fps.setCurrentIndex(shell.dialog.capture_fps.findData(30))


def test_apply_saves_then_waits_for_cleanup_before_new_frames(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO], qtbot: QtBot
) -> None:
    shell, capture, io = application
    choose_second(shell)
    dialog = shell.dialog
    assert dialog is not None
    io.release.clear()
    dialog.save_button.click()
    qtbot.waitUntil(io.entered.is_set)
    assert capture.disconnects == 0
    assert capture.snapshot().phase == "streaming"
    assert not dialog.capture_connect_button.isEnabled()
    assert dialog.capture_disconnect_button.isEnabled()
    shell.connect_video()
    assert len(capture.connections) == 1
    io.release.set()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert load_settings(shell.path).capture_device == "path-b"
    assert capture.disconnects == 1
    assert len(capture.connections) == 1
    assert shell.video.applying
    assert not dialog.save_button.isEnabled()
    capture.complete_disconnect()
    qtbot.waitUntil(lambda: len(capture.connections) == 2)
    assert capture.connections[-1] == CaptureOptions("path-b", fps=30)
    assert capture.refreshes == 0
    assert shell.video.applying
    capture.stream()
    qtbot.waitUntil(lambda: not shell.video.applying)
    assert shell.video.apply_state == "complete"
    assert dialog.capture_connection_status.text() == "取得中"


def test_save_failure_preserves_stream_and_unsaved_values(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO], qtbot: QtBot
) -> None:
    shell, capture, io = application
    choose_second(shell)
    io.fail = True
    assert shell.dialog is not None
    shell.dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.error
    assert shell.dirty
    assert shell.baseline.capture_device == "path-a"
    assert shell.dialog.values().capture_device == "path-b"
    assert capture.snapshot().phase == "streaming"
    assert capture.disconnects == 0
    assert len(capture.connections) == 1


@pytest.mark.parametrize(
    "failure", ["unconfirmed", "old_error", "unreleased", "rejected", "new_error"]
)
def test_failed_switch_stays_stopped_without_rollback_or_retry(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO],
    qtbot: QtBot,
    failure: str,
) -> None:
    shell, capture, _io = application
    choose_second(shell)
    assert shell.dialog is not None
    shell.dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert capture.disconnects == 1
    if failure == "unreleased":
        capture.state = replace(capture.state, phase="idle", cleanup="none")
    elif failure in {"unconfirmed", "old_error"}:
        capture.state = replace(
            capture.state,
            phase="error",
            cleanup="unconfirmed" if failure == "unconfirmed" else "graceful",
            error="old cleanup failed",
        )
    else:
        capture.accept_connection = failure != "rejected"
        capture.complete_disconnect()
        if failure == "new_error":
            qtbot.waitUntil(lambda: len(capture.connections) == 2)
            capture.state = replace(
                capture.state, phase="error", error="new open failed", cleanup="graceful"
            )
    qtbot.waitUntil(lambda: shell.video.apply_state == "failed")
    assert shell.video.apply_error
    if failure == "unconfirmed":
        assert shell.dialog.capture_connection_status.text() == "終了未確認"
    assert shell.dialog.capture_connection_error.toPlainText()
    assert shell.baseline.capture_device == "path-b"
    assert load_settings(shell.path).capture_device == "path-b"
    assert not shell.dirty
    assert capture.snapshot().frame is None
    count = len(capture.connections)
    for _ in range(3):
        shell.video.timer.timeout.emit()
    assert len(capture.connections) == count == (2 if failure == "new_error" else 1)
    assert capture.refreshes == 0


def test_dirty_selection_cannot_connect_saved_device_but_can_refresh(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO], qtbot: QtBot
) -> None:
    shell, capture, _io = application
    capture.finish_disconnect = True
    shell.disconnect_video()
    choose_second(shell)
    dialog = shell.dialog
    assert dialog is not None
    assert not dialog.capture_connect_button.isEnabled()
    assert not shell.window.video.connect_button.isEnabled()
    assert dialog.capture_refresh_button.isEnabled()
    shell.connect_video()
    assert len(capture.connections) == 1
    assert shell.apply_settings()
    qtbot.waitUntil(lambda: len(capture.connections) == 2)
    assert capture.connections[-1].device_path == "path-b"


def test_disconnected_save_intent_can_be_cancelled_from_dialog(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO], qtbot: QtBot
) -> None:
    shell, capture, io = application
    capture.finish_disconnect = True
    shell.disconnect_video()
    choose_second(shell)
    io.release.clear()
    dialog = shell.dialog
    assert dialog is not None
    dialog.save_button.click()
    qtbot.waitUntil(io.entered.is_set)
    shell.video.timer.timeout.emit()
    assert dialog.capture_disconnect_button.isEnabled()
    assert shell.window.video.disconnect_button.isEnabled()
    dialog.capture_disconnect_button.click()
    io.release.set()
    qtbot.waitUntil(lambda: shell.operation is None)
    for _ in range(3):
        shell.video.timer.timeout.emit()
    assert len(capture.connections) == 1
    assert shell.baseline.capture_device == "path-b"


def test_unselected_saved_device_cannot_be_explicitly_connected(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO], qtbot: QtBot
) -> None:
    shell, capture, _io = application
    capture.finish_disconnect = True
    shell.disconnect_video()
    assert shell.dialog is not None
    shell.dialog.capture_choices.setCurrentIndex(0)
    shell.dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert not shell.dialog.capture_connect_button.isEnabled()
    assert not shell.window.video.connect_button.isEnabled()
    shell.connect_video()
    assert len(capture.connections) == 1


def test_real_spawn_applies_saved_fps_after_release_and_keeps_held_frame(
    qtbot: QtBot, tmp_path: Path
) -> None:
    marker = tmp_path / "release.txt"
    path = tmp_path / "settings.toml"
    save_settings(path, Settings(capture_device="path-b"))
    service = CaptureService(backend_factory=PeerBackend(str(marker)))
    shell = ApplicationShell(path, capture_service=service)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.operation is None)
        shell.connect_video()
        qtbot.waitUntil(lambda: service.snapshot().frame is not None)
        first = service.snapshot().frame
        assert first is not None
        held_bytes = first.bgr
        qtbot.waitUntil(lambda: not shell.video.applying)
        shell.open_settings()
        dialog = shell.dialog
        assert dialog is not None
        dialog.capture_fps.setCurrentIndex(dialog.capture_fps.findData(30))
        dialog.save_button.click()
        qtbot.waitUntil(
            lambda: (
                (frame := service.snapshot().frame) is not None
                and frame.generation > first.generation
            )
        )
        qtbot.waitUntil(lambda: not shell.video.applying)
        assert load_settings(path).capture_fps == 30
        assert shell.video.active_settings is not None
        assert shell.video.active_settings.capture_fps == 30
        assert first.bgr == held_bytes
        assert marker.read_text(encoding="utf-8") == "released\n"
        assert marker.with_suffix(".txt.opened").read_text(encoding="utf-8") == "opened\nopened\n"
    finally:
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible(), timeout=10000)
    assert not service.is_alive()
    assert service.snapshot().cleanup == "graceful"
    assert marker.read_text(encoding="utf-8") == "released\nreleased\n"


def test_empty_selection_saves_and_only_disconnects(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO], qtbot: QtBot
) -> None:
    shell, capture, _io = application
    assert shell.dialog is not None
    shell.dialog.capture_choices.setCurrentIndex(0)
    shell.dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.baseline.capture_device == ""
    assert capture.disconnects == 1
    capture.complete_disconnect()
    qtbot.waitUntil(lambda: shell.video.apply_state == "complete")
    assert len(capture.connections) == 1
    assert capture.refreshes == 0


@pytest.mark.parametrize("stop", ["disconnect", "exit_cancel"])
def test_manual_stop_cancels_connection_waiting_for_save(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO], qtbot: QtBot, stop: str
) -> None:
    shell, capture, io = application
    choose_second(shell)
    io.release.clear()
    assert shell.dialog is not None
    shell.dialog.save_button.click()
    qtbot.waitUntil(io.entered.is_set)
    if stop == "disconnect":
        shell.dialog.capture_disconnect_button.click()
        capture.complete_disconnect()
    else:
        shell.request_close()
        shell.cancel_close()
    io.release.set()
    qtbot.waitUntil(lambda: shell.operation is None)
    for _ in range(3):
        shell.video.timer.timeout.emit()
    assert shell.window.isVisible()
    assert shell.baseline.capture_device == "path-b"
    assert len(capture.connections) == 1
    assert not shell.video.applying


def test_saved_device_connect_and_disconnect_refresh_are_explicit(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO], qtbot: QtBot
) -> None:
    shell, capture, _io = application
    dialog = shell.dialog
    assert dialog is not None
    dialog.capture_disconnect_button.click()
    assert capture.disconnects == 1
    assert capture.refreshes == 0
    capture.complete_disconnect()
    qtbot.waitUntil(lambda: capture.refreshes == 1)
    capture.complete_enumeration((DeviceInfo("First", "path-a"),))
    qtbot.waitUntil(dialog.capture_connect_button.isEnabled)
    dialog.capture_connect_button.click()
    assert len(capture.connections) == 2
    capture.stream()
    qtbot.waitUntil(lambda: not shell.video.applying)
    assert capture.refreshes == 1


def test_display_only_apply_and_dirty_close_save_do_not_start_video(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO], qtbot: QtBot
) -> None:
    shell, capture, _io = application
    capture.finish_disconnect = True
    shell.window.video.disconnect_button.click()
    dialog = shell.dialog
    assert dialog is not None
    dialog.preview_fps.setCurrentIndex(dialog.preview_fps.findData(15))
    dialog.model.setText("model-only")
    dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert len(capture.connections) == 1
    assert shell.baseline.preview_fps == 15
    choose_second(shell)
    dialog.close_button.click()
    assert shell.confirmation is not None
    button = shell.confirmation.button(QMessageBox.StandardButton.Save)
    assert button is not None
    button.click()
    qtbot.waitUntil(lambda: not dialog.isVisible())
    assert shell.baseline.capture_device == "path-b"
    assert len(capture.connections) == 1


def test_common_snapshot_apply_rejects_dirty_editor_then_uses_same_switch(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO], qtbot: QtBot
) -> None:
    shell, capture, io = application
    target = replace(shell.baseline, capture_device="path-b")
    assert shell.dialog is not None
    shell.dialog.model.setText("draft")
    assert not shell.apply_settings(target)
    assert io.saves == 0
    assert shell.dialog.model.text() == "draft"
    shell.dialog.set_values(shell.baseline)
    assert shell.apply_settings(target)
    qtbot.waitUntil(lambda: shell.operation is None)
    assert capture.disconnects == 1
    capture.complete_disconnect()
    qtbot.waitUntil(lambda: len(capture.connections) == 2)
    assert capture.connections[-1].device_path == "path-b"
