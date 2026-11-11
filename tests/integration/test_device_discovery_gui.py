from dataclasses import replace
from pathlib import Path
from threading import Event

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.game_io.capture import CaptureService
from gigas.game_io.capture.models import DeviceInfo
from gigas.storage.settings import Settings, load_settings, save_settings
from tests.integration.peers.capture_peer import PeerBackend
from tests.integration.peers.controlled_capture import ControlledCapture
from tests.integration.test_video_gui import (
    video_shell as video_shell,  # noqa: PLC0414 -- reuse the application lifetime fixture
)


def test_open_enumerates_asynchronously_and_preserves_edits_through_retry(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    assert capture.refreshes == 1
    assert dialog.capture_discovery_status.text() == "検索中"
    assert dialog.capture_choices.currentData() == "path-b"
    assert not dialog.capture_refresh_button.isEnabled()
    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData("path-b"))
    assert dialog.capture_choices.currentData() == "path-b"
    dialog.model.setText("unsaved model")
    dialog.capture_fps.setCurrentIndex(dialog.capture_fps.findData(30))
    shell.open_settings()
    assert capture.refreshes == 1
    pulse = Event()
    QTimer.singleShot(0, pulse.set)
    qtbot.waitUntil(pulse.is_set)

    capture.complete_enumeration(error="enumeration failed")
    qtbot.waitUntil(lambda: dialog.capture_discovery_status.text() == "列挙失敗")
    assert dialog.capture_choices.currentData() == "path-b"
    assert dialog.capture_choices.currentData() == "path-b"
    assert shell.window.notifications.notices["video"].details == "enumeration failed"
    assert dialog.capture_refresh_button.isEnabled()
    assert capture.refreshes == 1

    dialog.capture_refresh_button.click()
    assert capture.refreshes == 2
    capture.complete_enumeration((DeviceInfo("First", "path-a"),))
    qtbot.waitUntil(lambda: dialog.capture_choices.findData("path-a") >= 0)
    assert dialog.capture_discovery_status.isHidden()
    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData("path-a"))
    dialog.capture_refresh_button.click()
    capture.complete_enumeration(())
    qtbot.waitUntil(lambda: not shell.video.discovering)
    assert dialog.capture_discovery_status.isHidden()
    assert dialog.values() == replace(
        shell.baseline, capture_device="path-a", capture_fps=30, model="unsaved model"
    )
    assert not capture.connections
    assert load_settings(shell.path) == shell.baseline


def test_reopening_settings_refreshes_once_and_retains_cached_device_without_connecting(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    assert capture.refreshes == 1
    capture.complete_enumeration((DeviceInfo("Saved camera", "path-b"),))
    qtbot.waitUntil(lambda: dialog.capture_choices.findText("Saved camera") >= 0)
    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData("path-b"))
    dialog.close_button.click()
    assert not dialog.isVisible()
    shell.open_settings()
    assert shell.dialog is dialog
    assert capture.refreshes == 2
    assert dialog.capture_choices.currentData() == "path-b"
    assert (
        dialog.capture_choices.itemText(dialog.capture_choices.findData("path-b")) == "Saved camera"
    )
    assert not capture.connections
    shell.open_settings()
    assert capture.refreshes == 2


def test_catalog_result_does_not_hide_unconfirmed_worker_cleanup(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    capture.complete_enumeration((DeviceInfo("Saved camera", "path-b"),))
    capture.state = replace(
        capture.state, phase="error", cleanup="unconfirmed", error="worker cleanup failed"
    )
    qtbot.waitUntil(lambda: dialog.capture_discovery_status.text() == "列挙失敗")
    assert dialog.capture_choices.currentData() == "path-b"
    assert (
        dialog.capture_choices.itemText(dialog.capture_choices.findData("path-b")) == "Saved camera"
    )
    assert shell.window.notifications.notices["video"].details == "worker cleanup failed"
    assert not dialog.capture_refresh_button.isEnabled()


def test_streaming_settings_use_cached_devices_without_interrupting_preview(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    shell.apply_settings(shell.baseline)
    capture.state = replace(
        capture.state, devices=(DeviceInfo("Current camera", "path-b"),), devices_revision=1
    )
    capture.stream(1)
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    qtbot.waitUntil(lambda: dialog.capture_choices.currentText() == "Current camera")
    dialog.model.setText("unsaved")
    dialog.capture_choices.setCurrentIndex(0)
    capture.stream(2, bytes([0, 255, 0]))
    view = shell.window.video.view
    qtbot.waitUntil(
        lambda: (
            view.grab().toImage().pixelColor(view.width() // 2, view.height() // 2)
            == QColor("lime")
        )
    )
    assert capture.refreshes == 0
    assert capture.disconnects == 0
    assert len(capture.connections) == 1
    assert capture.state.generation == 1
    assert dialog.capture_choices.currentData() == ""
    assert dialog.values().capture_device == ""
    assert shell.baseline.capture_device == "path-b"
    assert load_settings(shell.path) == shell.baseline
    assert dialog.values().model == "unsaved"
    assert not dialog.capture_refresh_button.isEnabled()


def test_real_spawn_discovery_opens_no_video_and_reopen_keeps_capture_running(
    qtbot: QtBot, tmp_path: Path
) -> None:
    marker = tmp_path / "release.txt"
    service = CaptureService(backend_factory=PeerBackend(str(marker)))
    path = tmp_path / "settings.toml"
    save_settings(path, Settings(capture_device="path-b"))
    shell = ApplicationShell(path, capture_service=service)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.operation is None)
        shell.open_settings()
        qtbot.waitUntil(lambda: service.snapshot().devices_revision == 1)
        qtbot.waitUntil(lambda: service.snapshot().phase == "idle")
        assert service.snapshot().frame is None
        assert not marker.with_suffix(".txt.opened").exists()
        dialog = shell.dialog
        assert dialog is not None
        qtbot.waitUntil(lambda: dialog.capture_choices.count() == 3)
        shell.apply_settings(shell.baseline)
        qtbot.waitUntil(lambda: service.snapshot().frame is not None)
        first = service.snapshot().frame
        assert first is not None
        qtbot.waitUntil(lambda: not shell.video.applying)
        dialog.close_button.click()
        assert not dialog.isVisible()
        shell.open_settings()
        qtbot.waitUntil(
            lambda: (
                (frame := service.snapshot().frame) is not None
                and frame.sequence >= first.sequence + 5
            )
        )
        assert service.snapshot().generation == first.generation
        assert marker.with_suffix(".txt.enumerated").read_text(encoding="utf-8") == (
            "enumerated\nenumerated\n"
        )
        assert marker.with_suffix(".txt.opened").read_text(encoding="utf-8") == "opened\n"
    finally:
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible(), timeout=10000)
    assert not service.is_alive()
    assert service.snapshot().cleanup == "graceful"
