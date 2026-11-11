from pathlib import Path
from threading import Event

from pytestqt.qtbot import QtBot

from gigas.app.settings_worker import SettingsIO
from gigas.app.shell import ApplicationShell
from gigas.runtime.codex.models import SessionStatus
from gigas.services.codex import ServiceEvent, ServiceResult
from gigas.storage.settings import Settings, save_settings
from tests.integration.peers.controlled_capture import ControlledCapture


def test_dialog_close_during_save_keeps_existing_stop_actions(qtbot: QtBot, tmp_path: Path) -> None:
    entered = Event()
    release = Event()

    def save(path: Path, settings: Settings) -> None:
        entered.set()
        if not release.wait(10):
            raise TimeoutError
        save_settings(path, settings)

    path = tmp_path / "settings.toml"
    save_settings(path, Settings(capture_device="camera-a"))
    capture = ControlledCapture()
    shell = ApplicationShell(path, SettingsIO(save=save), capture_service=capture)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.operation is None)
        shell.connect_video()
        capture.stream()
        qtbot.waitUntil(lambda: not shell.video.applying)
        panel = shell.window.connection
        panel.activate(1)
        panel.handle_result(ServiceResult(1, "connect"))
        panel.handle_event(ServiceEvent(1, SessionStatus("running")))
        interrupted: list[bool] = []
        disconnected: list[bool] = []
        panel.interrupt_requested.connect(lambda: interrupted.append(True))
        panel.disconnect_requested.connect(lambda: disconnected.append(True))
        shell.open_settings()
        assert shell.dialog is not None
        shell.dialog.model.setText("edited")
        shell.dialog.save_button.click()
        qtbot.waitUntil(entered.is_set)

        shell.dialog.close_button.click()

        assert shell.pending == "dialog"
        assert panel.interrupt_button.isEnabled()
        assert panel.disconnect_action.isEnabled()
        assert shell.window.video.disconnect_action.isEnabled()
        panel.interrupt_button.click()
        panel.disconnect_action.trigger()
        shell.window.video.disconnect_action.trigger()
        assert interrupted == [True]
        assert disconnected == [True]
        assert capture.disconnects == 1
        assert not release.is_set()
    finally:
        release.set()
        qtbot.waitUntil(lambda: shell.operation is None)
        shell.window.close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())
