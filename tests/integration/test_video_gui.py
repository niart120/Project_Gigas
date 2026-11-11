from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from pytestqt.qtbot import QtBot

from gigas.app.settings_worker import SettingsIO
from gigas.app.shell import ApplicationShell
from gigas.game_io.capture import CaptureService, CaptureSnapshot
from gigas.game_io.models import CaptureOptions, DeviceInfo, Frame
from gigas.storage.settings import Settings, load_settings, save_settings
from tests.integration.peers.capture_peer import PeerBackend


class ControlledCapture:
    """Expose controllable service outcomes without opening a capture device."""

    def __init__(self) -> None:
        """Prepare request history and an idle state."""
        self.state = CaptureSnapshot()
        self.alive = False
        self.connections: list[CaptureOptions] = []
        self.refreshes = 0
        self.disconnects = 0
        self.shutdowns = 0
        self.finish_shutdown = True

    def start(self) -> None:
        """Start the simulated service without discovering devices."""
        self.alive = True

    def refresh_devices(self) -> bool:
        """Accept only an idle device enumeration request."""
        if self.state.phase not in {"idle", "error"}:
            return False
        self.refreshes += 1
        self.state = replace(self.state, phase="enumerating")
        return True

    def connect(self, options: CaptureOptions) -> bool:
        """Record an immutable connection request."""
        if self.state.phase not in {"idle", "error"}:
            return False
        self.connections.append(options)
        self.state = replace(self.state, phase="connecting", generation=len(self.connections))
        return True

    def disconnect(self) -> None:
        """Complete a requested disconnection immediately."""
        self.disconnects += 1
        self.state = replace(self.state, phase="idle", frame=None, cleanup="graceful")

    def shutdown(self) -> None:
        """Optionally keep shutdown pending for lifetime tests."""
        self.shutdowns += 1
        self.state = replace(self.state, phase="stopping", frame=None)
        if self.finish_shutdown:
            self.state = replace(self.state, phase="closed", cleanup="graceful")
            self.alive = False

    def is_alive(self) -> bool:
        """Return the controlled thread lifetime."""
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        """Reject any GUI attempt to wait for a running thread."""
        assert timeout == 0

    def snapshot(self) -> CaptureSnapshot:
        """Return the immutable current state."""
        return self.state

    def stream(self, sequence: int = 1, bgr: bytes = bytes([0, 0, 255])) -> None:
        """Publish one solid image in the latest connection."""
        options = self.connections[-1]
        frame = Frame(
            bgr * (options.width * options.height),
            options.width,
            options.height,
            len(self.connections),
            sequence,
            datetime.now(UTC),
            sequence,
        )
        self.state = replace(
            self.state, phase="streaming", frame=frame, reported_fps=59.94, measured_fps=59.5
        )


@pytest.fixture
def video_shell(
    qtbot: QtBot, tmp_path: Path
) -> Iterator[tuple[ApplicationShell, ControlledCapture]]:
    capture = ControlledCapture()
    path = tmp_path / "settings.toml"
    save_settings(path, Settings(capture_device="path-b", capture_width=16, capture_height=16))
    shell = ApplicationShell(path, capture_service=capture)
    shell.start()
    qtbot.waitUntil(lambda: shell.operation is None)
    yield shell, capture
    capture.finish_shutdown = True
    capture.state = replace(capture.state, cleanup="graceful")
    capture.alive = False
    if shell.dialog is not None:
        shell.dialog.set_values(shell.baseline)
    if shell.confirmation is not None:
        shell.confirmation.reject()
    if shell.window.isVisible():
        shell.window.close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())


def test_explicit_discovery_preserves_edits_and_connection_uses_saved_mode(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    assert capture.alive
    assert capture.refreshes == 0
    assert not capture.connections
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    dialog.model.setPlainText("unsaved model")
    dialog.capture_width.setValue(32)
    dialog.capture_refresh_button.click()
    assert capture.refreshes == 1
    assert not dialog.capture_refresh_button.isEnabled()
    capture.state = replace(
        capture.state,
        phase="idle",
        devices=(DeviceInfo("Camera", "path-a"), DeviceInfo("Camera", "path-b")),
        devices_revision=1,
    )
    qtbot.waitUntil(lambda: dialog.capture_choices.count() == 3)
    assert dialog.values().model == "unsaved model"
    assert dialog.values().capture_width == 32
    assert dialog.capture_choices.currentData() == "path-b"

    shell.window.video.connect_button.click()
    assert capture.connections == [CaptureOptions("path-b", 16, 16, 60)]
    assert not shell.window.video.connect_button.isEnabled()
    assert not dialog.capture_refresh_button.isEnabled()
    capture.stream()
    view = shell.window.video.view
    qtbot.waitUntil(
        lambda: (
            view.grab().toImage().pixelColor(view.width() // 2, view.height() // 2) == QColor("red")
        )
    )
    dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.baseline.capture_width == 32
    assert shell.video.active_settings is not None
    assert shell.video.active_settings.capture_width == 16
    assert len(capture.connections) == 1
    save_settings(shell.path, replace(shell.baseline, capture_width=64))
    shell.reload()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.baseline.capture_width == 64
    assert shell.video.active_settings.capture_width == 16
    assert len(capture.connections) == 1


def test_slow_preview_uses_latest_frame_and_clears_disconnect_without_waiting_for_next_image(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    shell.baseline = replace(shell.baseline, preview_fps=1)
    shell.window.video.connect_button.click()
    capture.stream()
    view = shell.window.video.view

    def color() -> QColor:
        return view.grab().toImage().pixelColor(view.width() // 2, view.height() // 2)

    qtbot.waitUntil(lambda: color() == QColor("red"))
    capture.stream(2, bytes([0, 255, 0]))
    capture.stream(100, bytes([255, 0, 0]))
    qtbot.wait(100)
    assert color() == QColor("red")
    qtbot.waitUntil(lambda: color() == QColor("blue"), timeout=2000)
    shell.window.video.disconnect_button.click()
    assert capture.disconnects == 1
    assert color() == QColor("black")


def test_failed_settings_load_does_not_disable_active_capture_disconnect(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    shell.window.video.connect_button.click()
    capture.stream()
    shell.path.write_text("[broken", encoding="utf-8")
    shell.reload()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert not shell.ready
    assert shell.window.video.disconnect_button.isEnabled()
    shell.window.video.disconnect_button.click()
    assert capture.disconnects == 1
    assert not shell.window.video.connect_button.isEnabled()


def test_saving_settings_does_not_disable_active_capture_disconnect(
    qtbot: QtBot, tmp_path: Path
) -> None:
    entered = Event()
    release = Event()

    def slow_save(path: Path, values: Settings) -> None:
        entered.set()
        if not release.wait(5):
            message = "save was not released"
            raise TimeoutError(message)
        save_settings(path, values)

    capture = ControlledCapture()
    path = tmp_path / "settings.toml"
    save_settings(path, Settings(capture_device="path-b", capture_width=16, capture_height=16))
    shell = ApplicationShell(path, SettingsIO(load_settings, slow_save), capture_service=capture)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.operation is None)
        shell.window.video.connect_button.click()
        capture.stream()
        shell.open_settings()
        shell.save()
        qtbot.waitUntil(entered.is_set)
        assert shell.window.video.disconnect_button.isEnabled()
        shell.window.video.disconnect_button.click()
        assert capture.disconnects == 1
    finally:
        release.set()
        qtbot.waitUntil(lambda: shell.operation is None)
        shell.window.close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())


def test_shutdown_waits_for_capture_cleanup_and_keeps_gui_responsive(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    capture.finish_shutdown = False
    shell.window.close()
    assert capture.shutdowns == 1
    assert shell.window.isVisible()
    pulse = Event()
    QTimer.singleShot(0, pulse.set)
    qtbot.waitUntil(pulse.is_set)
    capture.alive = False
    capture.state = replace(
        capture.state, phase="error", cleanup="unconfirmed", error="child alive"
    )
    qtbot.waitUntil(lambda: "child alive" in shell.window.status.text())
    assert shell.window.isVisible()
    assert not shell.window.allow_close
    capture.state = replace(capture.state, phase="closed", cleanup="forced", error="")
    qtbot.waitUntil(lambda: not shell.window.isVisible())


def test_real_spawn_capture_displays_and_releases_through_application(
    qtbot: QtBot, tmp_path: Path
) -> None:
    marker = tmp_path / "release.txt"
    service = CaptureService(backend_factory=PeerBackend(str(marker)))
    path = tmp_path / "settings.toml"
    save_settings(path, Settings(capture_device="path-b", capture_width=16, capture_height=16))
    shell = ApplicationShell(path, capture_service=service)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.operation is None)
        shell.window.video.connect_button.click()
        qtbot.waitUntil(lambda: service.snapshot().frame is not None, timeout=8000)
        view = shell.window.video.view
        qtbot.waitUntil(
            lambda: (
                view.grab().toImage().pixelColor(view.width() // 2, view.height() // 2)
                != QColor("black")
            )
        )
        shell.window.video.disconnect_button.click()
        qtbot.waitUntil(lambda: service.snapshot().phase == "idle", timeout=8000)
        assert marker.read_text(encoding="utf-8") == "released\n"
        qtbot.waitUntil(shell.window.video.connect_button.isEnabled)
        shell.window.video.connect_button.click()
        qtbot.waitUntil(lambda: service.snapshot().frame is not None, timeout=8000)
    finally:
        shell.window.close()
        qtbot.waitUntil(lambda: not shell.window.isVisible(), timeout=8000)
    assert not service.is_alive()
    assert service.snapshot().cleanup == "graceful"
    assert marker.read_text(encoding="utf-8") == "released\nreleased\n"


def test_long_device_paths_and_errors_keep_video_controls_within_the_screen(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    path = "device-path/" * 200
    shell.baseline = replace(shell.baseline, capture_device=path)
    shell.window.video.connect_button.click()
    error = "<b>not markup</b> " + "diagnostic/" * 300
    capture.state = replace(capture.state, phase="error", error=error)
    qtbot.waitUntil(lambda: shell.window.video.details.toPlainText() == error)
    assert shell.window.video.source.text() == path
    assert shell.window.screen().availableGeometry().contains(shell.window.frameGeometry())
    panel = shell.window.video
    for button in (panel.connect_button, panel.disconnect_button):
        assert button.isVisible()
        assert panel.rect().contains(button.geometry())


def test_unexpected_capture_thread_exit_clears_image_and_disables_requests(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    shell.window.video.connect_button.click()
    capture.stream()
    view = shell.window.video.view
    qtbot.waitUntil(
        lambda: (
            view.grab().toImage().pixelColor(view.width() // 2, view.height() // 2) == QColor("red")
        )
    )
    capture.alive = False
    qtbot.waitUntil(lambda: not shell.window.video.disconnect_button.isEnabled())
    assert not shell.window.video.connect_button.isEnabled()
    assert view.grab().toImage().pixelColor(view.width() // 2, view.height() // 2) == QColor(
        "black"
    )
