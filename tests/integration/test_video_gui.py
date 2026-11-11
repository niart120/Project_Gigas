from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtGui import QColor
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.game_io.capture import CaptureService
from gigas.storage.settings import Settings, save_settings
from tests.integration.peers.capture_peer import PeerBackend
from tests.integration.peers.controlled_capture import ControlledCapture


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
        shell.apply_settings(shell.baseline)
        qtbot.waitUntil(lambda: service.snapshot().frame is not None, timeout=8000)
        view = shell.window.video.view
        qtbot.waitUntil(
            lambda: (
                view.grab().toImage().pixelColor(view.width() // 2, view.height() // 2)
                != QColor("black")
            )
        )
        shell.apply_settings(replace(shell.baseline, capture_device=""))
        qtbot.waitUntil(lambda: shell.operation is None)
        qtbot.waitUntil(lambda: service.snapshot().phase == "idle", timeout=8000)
        assert marker.read_text(encoding="utf-8") == "released\n"
        shell.apply_settings(replace(shell.baseline, capture_device="path-b"))
        qtbot.waitUntil(lambda: service.snapshot().frame is not None, timeout=8000)
    finally:
        shell.window.close()
        qtbot.waitUntil(lambda: not shell.window.isVisible(), timeout=8000)
    assert not service.is_alive()
    assert service.snapshot().cleanup == "graceful"
    assert marker.read_text(encoding="utf-8") == "released\nreleased\n"
