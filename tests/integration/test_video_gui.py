from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QColor
from pytestqt.qtbot import QtBot

from gigas.app import video
from gigas.app.settings_worker import SettingsIO
from gigas.app.shell import ApplicationShell
from gigas.game_io.capture import CaptureService
from gigas.game_io.capture.models import CaptureOptions, DeviceInfo, Frame
from gigas.storage.settings import Settings, load_settings, save_settings
from gigas.ui.video_panel import VideoView
from tests.integration.peers.capture_peer import PeerBackend
from tests.integration.peers.controlled_capture import ControlledCapture


@dataclass
class PreviewClocks:
    """Replay precise timer events beside a coarse Windows elapsed clock."""

    elapsed: float = 0.0

    def monotonic(self) -> float:
        """Return elapsed time quantized to the Windows 15.625 ms tick."""
        return 1000.0 + int(self.elapsed * 64) / 64

    def perf_counter(self) -> float:
        """Return the precise elapsed time of the same event."""
        return 1000.0 + self.elapsed


@pytest.mark.parametrize(
    ("width", "height", "left", "top", "right", "bottom"),
    [(4, 3, 40, 0, 279, 179), (32, 3, 0, 75, 319, 104), (16, 9, 0, 0, 319, 179)],
    ids=["four-three", "ultrawide", "same-aspect"],
)
def test_preview_letterbox_switch_preserves_complete_source(
    qtbot: QtBot, width: int, height: int, left: int, top: int, right: int, bottom: int
) -> None:
    view = VideoView()
    qtbot.addWidget(view)
    view.resize(320, 180)
    view.show()
    colors = (bytes([0, 0, 255]), bytes([0, 255, 0]), bytes([255, 0, 0]), bytes([255] * 3))
    pixels = b"".join(
        colors[2 * (y >= height // 2) + (x >= width // 2)]
        for y in range(height)
        for x in range(width)
    )
    frame = Frame(pixels, width, height, 1, 1, datetime.now(UTC), 10)
    view.set_frame(frame)

    def assert_corners(x1: int, y1: int, x2: int, y2: int) -> None:
        image = view.grab().toImage()
        assert image.pixelColor(x1, y1) == QColor("red")
        assert image.pixelColor(x2, y1) == QColor("lime")
        assert image.pixelColor(x1, y2) == QColor("blue")
        assert image.pixelColor(x2, y2) == QColor("white")

    original = view.grab().toImage()
    assert_corners(left, top, right, bottom)
    if left:
        assert original.pixelColor(left - 1, 90) == QColor("black")
        assert original.pixelColor(right + 1, 90) == QColor("black")
    if top:
        assert original.pixelColor(160, top - 1) == QColor("black")
        assert original.pixelColor(160, bottom + 1) == QColor("black")

    view.set_letterbox(False)
    assert_corners(0, 0, 319, 179)
    view.set_letterbox(True)
    assert view.grab().toImage() == original
    assert frame.bgr == pixels
    assert (frame.width, frame.height, frame.generation, frame.sequence) == (width, height, 1, 1)


def test_preview_letterbox_mode_survives_empty_and_new_frames(qtbot: QtBot) -> None:
    view = VideoView()
    qtbot.addWidget(view)
    view.resize(160, 90)
    view.show()
    view.set_letterbox(False)
    frame = Frame(bytes([0, 0, 255]) * 12, 4, 3, 1, 1, datetime.now(UTC), 10)
    view.set_frame(frame)
    assert view.grab().toImage().pixelColor(0, 45) == QColor("red")
    view.set_frame(None)
    assert view.grab().toImage().pixelColor(0, 45) == QColor("black")
    view.set_frame(replace(frame, generation=2))
    assert view.grab().toImage().pixelColor(0, 45) == QColor("red")


@pytest.fixture
def video_shell(
    qtbot: QtBot, tmp_path: Path
) -> Iterator[tuple[ApplicationShell, ControlledCapture]]:
    capture = ControlledCapture()
    path = tmp_path / "settings.toml"
    save_settings(path, Settings(capture_device="path-b"))
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


def test_automatic_discovery_preserves_edits_and_connection_uses_saved_mode(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    assert capture.alive
    assert capture.reader_requests == 1
    assert capture.refreshes == 0
    assert not capture.connections
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    assert dialog.capture_choices.currentData() == ""
    dialog.model.setText("unsaved model")
    dialog.capture_fps.setCurrentIndex(dialog.capture_fps.findData(30))
    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData("path-b"))
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
    assert dialog.values().capture_fps == 30
    assert dialog.capture_choices.currentData() == "path-b"

    assert not shell.window.video.connect_action.isEnabled()
    dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert capture.connections == [CaptureOptions("path-b", 1920, 1080, 30)]
    assert not shell.window.video.connect_action.isEnabled()
    assert not dialog.capture_refresh_button.isEnabled()
    capture.stream()
    view = shell.window.video.view
    qtbot.waitUntil(
        lambda: (
            view.grab().toImage().pixelColor(view.width() // 2, view.height() // 2) == QColor("red")
        )
    )
    assert shell.baseline.capture_fps == 30
    assert shell.video.active_settings is not None
    assert shell.video.active_settings.capture_fps == 30
    assert len(capture.connections) == 1
    save_settings(shell.path, replace(shell.baseline, capture_fps=15))
    shell.reload()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.baseline.capture_fps == 15
    assert shell.video.active_settings.capture_fps == 30
    assert len(capture.connections) == 1


def test_slow_preview_uses_latest_frame_and_clears_disconnect_without_waiting_for_next_image(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    shell.baseline = replace(shell.baseline, preview_fps=1)
    shell.window.video.connect_action.trigger()
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
    shell.window.video.disconnect_action.trigger()
    assert capture.disconnects == 1
    assert color() == QColor("black")


@pytest.mark.parametrize(("fps", "interval_ms"), [(30, 34), (60, 17)])
def test_preview_does_not_skip_due_frames_on_coarse_clock_ticks(
    video_shell: tuple[ApplicationShell, ControlledCapture],
    monkeypatch: pytest.MonkeyPatch,
    fps: int,
    interval_ms: int,
) -> None:
    shell, capture = video_shell
    clock = PreviewClocks()
    monkeypatch.setattr(video, "time", clock)
    shell.video.timer.stop()
    shell.baseline = replace(shell.baseline, preview_fps=fps)
    shell.window.video.connect_action.trigger()
    delivered: list[int] = []
    view = shell.window.video.view
    set_frame = view.set_frame

    def record(frame: Frame | None) -> None:
        if frame is not None:
            delivered.append(frame.sequence)
        set_frame(frame)

    monkeypatch.setattr(view, "set_frame", record)
    for tick in range(1, 61):
        clock.elapsed = tick * interval_ms / 1000
        capture.stream(tick)
        shell.video.timer.timeout.emit()
        shell.video.timer.timeout.emit()
    assert delivered == list(range(1, 61))


def test_failed_settings_load_does_not_disable_active_capture_disconnect(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    shell.window.video.connect_action.trigger()
    capture.stream()
    shell.path.write_text("[broken", encoding="utf-8")
    qtbot.waitUntil(lambda: not shell.video.applying)
    shell.reload()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert not shell.ready
    assert shell.window.video.disconnect_action.isEnabled()
    shell.window.video.disconnect_action.trigger()
    assert capture.disconnects == 1
    assert not shell.window.video.connect_action.isEnabled()


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
    save_settings(path, Settings(capture_device="path-b"))
    shell = ApplicationShell(path, SettingsIO(load_settings, slow_save), capture_service=capture)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.operation is None)
        shell.window.video.connect_action.trigger()
        capture.stream()
        shell.open_settings()
        qtbot.waitUntil(lambda: not shell.video.applying)
        shell.save()
        qtbot.waitUntil(entered.is_set)
        assert shell.window.video.disconnect_action.isEnabled()
        shell.window.video.disconnect_action.trigger()
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
    qtbot.waitUntil(lambda: "shutdown" in shell.window.notifications.notices)
    assert "child alive" in shell.window.notifications.notices["shutdown"].details
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
    save_settings(path, Settings(capture_device="path-b"))
    shell = ApplicationShell(path, capture_service=service)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.operation is None)
        shell.window.video.connect_action.trigger()
        qtbot.waitUntil(lambda: service.snapshot().frame is not None, timeout=8000)
        view = shell.window.video.view
        qtbot.waitUntil(
            lambda: (
                view.grab().toImage().pixelColor(view.width() // 2, view.height() // 2)
                != QColor("black")
            )
        )
        shell.window.video.disconnect_action.trigger()
        qtbot.waitUntil(lambda: service.snapshot().phase == "idle", timeout=8000)
        assert marker.read_text(encoding="utf-8") == "released\n"
        qtbot.waitUntil(shell.window.video.connect_action.isEnabled)
        shell.window.video.connect_action.trigger()
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
    shell.window.video.connect_action.trigger()
    error = "<b>not markup</b> " + "diagnostic/" * 300
    name = "<b>Camera</b>"
    capture.state = replace(capture.state, devices=(DeviceInfo(name, path),), devices_revision=1)
    capture.stream()
    qtbot.waitUntil(lambda: shell.window.video.source.text() == name)
    capture.state = replace(capture.state, phase="error", error=error)
    qtbot.waitUntil(lambda: "video" in shell.window.notifications.notices)
    assert shell.window.notifications.notices["video"].details == error
    assert shell.window.video.source.text() == ""
    assert shell.window.screen().availableGeometry().contains(shell.window.frameGeometry())
    panel = shell.window.video
    menu = shell.window.video_menu
    menu.popup(shell.window.mapToGlobal(QPoint(20, 40)))
    qtbot.waitUntil(menu.isVisible)
    assert shell.window.screen().availableGeometry().contains(menu.frameGeometry())
    for action in (panel.connect_action, panel.disconnect_action):
        assert action.isVisible()
        assert menu.rect().contains(menu.actionGeometry(action))
    menu.hide()


def test_unexpected_capture_thread_exit_clears_image_and_disables_requests(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    shell.window.video.connect_action.trigger()
    capture.stream()
    view = shell.window.video.view
    qtbot.waitUntil(
        lambda: (
            view.grab().toImage().pixelColor(view.width() // 2, view.height() // 2) == QColor("red")
        )
    )
    capture.alive = False
    qtbot.waitUntil(lambda: not shell.window.video.disconnect_action.isEnabled())
    assert not shell.window.video.connect_action.isEnabled()
    assert view.grab().toImage().pixelColor(view.width() // 2, view.height() // 2) == QColor(
        "black"
    )
