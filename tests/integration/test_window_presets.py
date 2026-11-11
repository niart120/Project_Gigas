from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QPoint, QRect
from pytestqt.qtbot import QtBot

from gigas.app import video
from gigas.app.settings_worker import SettingsIO
from gigas.app.shell import ApplicationShell
from gigas.game_io.capture import CaptureService
from gigas.game_io.capture.models import Frame
from gigas.storage.settings import Settings, SettingsError, load_settings, save_settings
from tests.integration.peers.capture_peer import PeerBackend
from tests.integration.test_video_gui import ControlledCapture, PreviewClocks
from tests.integration.test_video_gui import (
    video_shell as video_shell,  # noqa: PLC0414 -- expose the shared pytest fixture
)


@pytest.mark.parametrize("fail", [False, True])
def test_menu_applies_only_successfully_saved_size_without_changing_capture(
    qtbot: QtBot, tmp_path: Path, fail: bool
) -> None:
    entered = Event()
    release = Event()

    def save(path: Path, settings: Settings) -> None:
        entered.set()
        if not release.wait(5):
            message = "save was not released"
            raise TimeoutError(message)
        if fail:
            message = "injected save failure"
            raise SettingsError(message)
        save_settings(path, settings)

    path = tmp_path / "settings.toml"
    save_settings(path, Settings(capture_device="path-b"))
    capture = ControlledCapture()
    shell = ApplicationShell(path, SettingsIO(load_settings, save), capture_service=capture)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        shell.window.video.connect_button.click()
        capture.stream()
        original = capture.snapshot().frame
        original_limit = shell.window.video.view.maximumSize()
        actions = shell.window.window_size_actions

        actions["hd"].trigger()

        qtbot.waitUntil(entered.is_set)
        assert actions["full_hd"].isChecked()
        assert shell.window.video.view.maximumSize() == original_limit
        assert all(not action.isEnabled() for action in actions.values())
        assert shell.window.video.disconnect_button.isEnabled()
        capture.stream(2)
        release.set()
        qtbot.waitUntil(lambda: shell.operation is None)
        expected = "full_hd" if fail else "hd"
        assert shell.baseline.window_size_preset == expected
        assert load_settings(path).window_size_preset == expected
        assert actions[expected].isChecked()
        assert (shell.window.video.view.maximumSize() == original_limit) == fail
        assert len(capture.connections) == 1
        assert original is not None
        current = capture.snapshot().frame
        assert current is not None
        assert (current.generation, current.sequence) == (original.generation, 2)
    finally:
        release.set()
        qtbot.waitUntil(lambda: shell.operation is None)
        shell.window.close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())


def test_all_presets_share_settings_selection_and_fit_available_screen(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, _capture = video_shell
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    actions = shell.window.window_size_actions
    assert set(actions) == {"hd", "full_hd", "wqhd", "four_k"}
    for key, action in actions.items():
        action.trigger()
        qtbot.waitUntil(lambda: shell.operation is None)
        assert shell.baseline.window_size_preset == key
        assert dialog.window_size_preset.currentData() == key
        assert [name for name, candidate in actions.items() if candidate.isChecked()] == [key]
        available = shell.window.screen().availableGeometry()
        assert available.contains(shell.window.frameGeometry())
        for widget in (
            shell.window.video.connect_button,
            shell.window.video.disconnect_button,
            shell.window.connection.connect_button,
            shell.window.connection.send_button,
            shell.window.settings_button,
        ):
            assert widget.isVisible()
            assert available.contains(QRect(widget.mapToGlobal(QPoint()), widget.size()))


def test_saved_display_fps_changes_without_reconnecting_and_dirty_edits_block_menu(
    video_shell: tuple[ApplicationShell, ControlledCapture],
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell, capture = video_shell
    clock = PreviewClocks()
    monkeypatch.setattr(video, "time", clock)
    shell.window.video.connect_button.click()
    shell.video.timer.stop()
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    dialog.preview_fps.setCurrentIndex(dialog.preview_fps.findData(15))
    dialog.window_size_preset.setCurrentIndex(dialog.window_size_preset.findData("hd"))
    dialog.model.setText("unsaved")
    assert all(not action.isEnabled() for action in shell.window.window_size_actions.values())
    shell.window.window_size_actions["four_k"].trigger()
    assert dialog.values().model == "unsaved"
    assert shell.operation is None
    assert shell.baseline.window_size_preset == "full_hd"
    dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.baseline.window_size_preset == "hd"
    delivered: list[int] = []
    real_set_frame = shell.window.video.view.set_frame

    def record(frame: Frame | None) -> None:
        if frame is not None:
            delivered.append(frame.sequence)
        real_set_frame(frame)

    monkeypatch.setattr(shell.window.video.view, "set_frame", record)
    clock.elapsed = 1
    capture.stream(1)
    shell.video.timer.timeout.emit()
    clock.elapsed = 1.034
    capture.stream(2)
    shell.video.timer.timeout.emit()
    assert delivered == [1]
    clock.elapsed = 1.068
    shell.video.timer.timeout.emit()
    assert delivered == [1, 2]
    assert len(capture.connections) == 1
    save_settings(shell.path, replace(shell.baseline, window_size_preset="wqhd"))
    shell.reload()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.window.window_size_actions["wqhd"].isChecked()
    assert dialog.window_size_preset.currentData() == "wqhd"


def test_real_capture_readers_continue_through_display_resize(qtbot: QtBot, tmp_path: Path) -> None:
    marker = tmp_path / "release.txt"
    service = CaptureService(backend_factory=PeerBackend(str(marker)))
    observer = service.reader()
    path = tmp_path / "settings.toml"
    save_settings(path, Settings(capture_device="path-b"))
    shell = ApplicationShell(path, capture_service=service)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        shell.window.video.connect_button.click()
        qtbot.waitUntil(lambda: observer.snapshot().frame is not None, timeout=8000)
        held = observer.snapshot().frame
        assert held is not None
        qtbot.waitUntil(lambda: shell.window.video.source.text() == "Capture")
        shell.open_settings()
        assert shell.dialog is not None
        assert shell.dialog.capture_choices.currentData() == "path-b"
        assert shell.dialog.capture_choices.findData("path-a") > 0
        assert shell.dialog.capture_choices.count() == 3
        pixels = held.bgr
        shell.window.window_size_actions["hd"].trigger()
        qtbot.waitUntil(lambda: shell.operation is None)
        qtbot.waitUntil(
            lambda: (
                (latest := shell.video.reader.snapshot().frame) is not None
                and latest.sequence > held.sequence
            )
        )
        current = shell.video.reader.snapshot().frame
        assert current is not None
        assert current.generation == held.generation
        assert (current.width, current.height) == (1920, 1080)
        assert held.bgr == pixels
        assert Path(str(marker) + ".opened").read_text(encoding="utf-8") == "opened\n"
    finally:
        shell.window.close()
        qtbot.waitUntil(lambda: not shell.window.isVisible(), timeout=8000)
    assert not service.is_alive()
    assert marker.read_text(encoding="utf-8") == "released\n"
