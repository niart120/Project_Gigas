from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from pytestqt.qtbot import QtBot

from gigas.app.settings_worker import SettingsIO
from gigas.app.shell import ApplicationShell
from gigas.app.video import VideoController
from gigas.game_io.capture import CaptureService
from gigas.game_io.capture.models import CaptureOptions, DeviceInfo
from gigas.storage.settings import Settings, load_settings, save_settings
from gigas.ui.video_panel import VideoPanel
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
    shell.apply_settings(shell.baseline)
    capture.state = replace(
        capture.state,
        devices=(DeviceInfo("First", "path-a"), DeviceInfo("Second", "path-b")),
        devices_revision=1,
    )
    capture.stream()
    qtbot.waitUntil(lambda: not shell.video.applying)
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
    assert not dialog.save_button.isEnabled()
    assert not dialog.ok_button.isEnabled()
    assert dialog.close_button.isEnabled()
    shell.apply_settings(shell.baseline)
    assert len(capture.connections) == 1
    io.release.set()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert load_settings(shell.path).capture_device == "path-b"
    assert capture.disconnects == 1
    assert len(capture.connections) == 1
    assert shell.video.applying
    assert dialog.save_button.isEnabled()
    capture.complete_disconnect()
    qtbot.waitUntil(lambda: len(capture.connections) == 2)
    assert capture.connections[-1] == CaptureOptions("path-b", fps=30)
    assert capture.refreshes == 0
    assert shell.video.applying
    capture.stream()
    qtbot.waitUntil(lambda: not shell.video.applying)
    assert shell.video.apply_state == "complete"
    assert shell.window.video.status.text() == "取得中"


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


@pytest.mark.parametrize("failure", ["unconfirmed", "old_error", "rejected", "new_error"])
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
    if failure in {"unconfirmed", "old_error"}:
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
        assert shell.window.video.status.text() == "終了未確認"
    assert shell.window.notifications.notices["video"].details
    assert shell.baseline.capture_device == "path-b"
    assert load_settings(shell.path).capture_device == "path-b"
    assert not shell.dirty
    assert capture.snapshot().frame is None
    count = len(capture.connections)
    for _ in range(3):
        shell.video.timer.timeout.emit()
    assert len(capture.connections) == count == (2 if failure == "new_error" else 1)
    assert capture.refreshes == 0


@pytest.mark.parametrize(("initial", "target"), [("enumerate", "path-b"), ("capture", "")])
def test_latest_target_replaces_unstarted_switch(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, initial: str, target: str
) -> None:
    capture = CaptureService(backend_factory=PeerBackend())
    entered, release = Event(), Event()
    run_request = capture._run_request

    def wait_before_spawn(options: CaptureOptions | None, generation: int) -> None:
        entered.set()
        if not release.wait(5):
            message = "test did not release capture request"
            raise TimeoutError(message)
        run_request(options, generation)

    monkeypatch.setattr(capture, "_run_request", wait_before_spawn)
    panel = VideoPanel()
    qtbot.addWidget(panel)
    controller = VideoController(panel, capture)
    controller.start()
    try:
        if initial == "enumerate":
            controller.refresh_devices()
        else:
            controller.apply_capture(Settings(capture_device="path-a"))
        qtbot.waitUntil(entered.is_set)
        controller.apply_capture(Settings(capture_device="path-c"))
        controller.apply_capture(Settings(capture_device=target, capture_resolution="auto"))
        release.set()
        qtbot.waitUntil(lambda: not controller.applying)
        assert controller.apply_state == "complete"
        if target:
            assert controller.active_settings is not None
            assert controller.active_settings.capture_device == target
            assert capture.snapshot().frame is not None
        else:
            assert controller.active_settings is None
            assert capture.snapshot().phase == "idle"
            assert capture.snapshot().frame is None
        assert capture.snapshot().generation == 1
    finally:
        release.set()
        controller.shutdown()
        capture.join(5)
        assert controller.shutdown_complete


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
        shell.apply_settings(shell.baseline)
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


def test_empty_selection_clears_saved_device_and_disconnects(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO], qtbot: QtBot
) -> None:
    shell, capture, _io = application
    assert shell.dialog is not None
    shell.dialog.capture_choices.setCurrentIndex(0)
    shell.dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.baseline.capture_device == ""
    assert load_settings(shell.path).capture_device == ""
    assert shell.dialog.capture_choices.currentData() == ""
    assert capture.disconnects == 1
    capture.complete_disconnect()
    qtbot.waitUntil(lambda: shell.video.apply_state == "complete")
    assert len(capture.connections) == 1
    assert capture.refreshes == 0


def test_reapplying_same_capture_conditions_keeps_connection(qtbot: QtBot) -> None:
    capture = ControlledCapture()
    panel = VideoPanel()
    qtbot.addWidget(panel)
    controller = VideoController(panel, capture)
    controller.start()
    settings = Settings(capture_device="path-a")
    try:
        controller.apply_capture(settings)
        controller.apply_capture(settings)
        capture.stream(actual_size=(32, 24))
        controller.timer.timeout.emit()
        controller.apply_capture(replace(settings, preview_fps=15, model="changed"))
        assert len(capture.connections) == 1
        assert capture.disconnects == 0
        assert controller.apply_state == "complete"
    finally:
        controller.shutdown()
        assert controller.shutdown_complete


def test_modal_edit_blocks_menu_until_cancel_then_switches_device(
    application: tuple[ApplicationShell, ControlledCapture, ControlledIO], qtbot: QtBot
) -> None:
    shell, capture, io = application
    assert shell.dialog is not None
    shell.dialog.model.setText("draft")
    shell.window.video_device_actions["path-b"].trigger()
    assert io.saves == 0
    assert shell.dialog.model.text() == "draft"
    shell.dialog.close_button.click()
    shell.window.video_device_actions["path-b"].trigger()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert capture.disconnects == 1
    capture.complete_disconnect()
    qtbot.waitUntil(lambda: len(capture.connections) == 2)
    assert capture.connections[-1].device_path == "path-b"
