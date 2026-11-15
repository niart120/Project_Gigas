import asyncio
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from gigas.app.settings_worker import SettingsIO
from gigas.app.shell import ApplicationShell
from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.game_io.input.models import InputState
from gigas.runtime.codex.connection import CodexConnection
from gigas.services.application import ApplicationServiceThread
from gigas.storage.settings import Settings, SettingsError, load_settings, save_settings
from tests.integration.peers.codex_resume import ResumePeer
from tests.integration.test_input_service import Device


def _start(
    qtbot: QtBot, path: Path, device: Device, io: SettingsIO | None = None
) -> tuple[ApplicationShell, ResumePeer]:
    peer = ResumePeer()
    save_settings(path / "settings.toml", Settings(serial_port="COM8"))
    service = ApplicationServiceThread(
        path,
        CaptureReader(CaptureSnapshot),
        lambda options, emit: CodexConnection(peer, options, emit),
        input_factory=lambda _config: device,
    )
    shell = ApplicationShell(path / "settings.toml", io, service=service)
    shell.start()
    qtbot.waitUntil(lambda: shell.ready and service.inputs.connected)
    return shell, peer


def _close(qtbot: QtBot, shell: ApplicationShell) -> None:
    if shell.dialog is not None:
        shell.dialog.set_values(shell.baseline)
    shell.request_close()
    qtbot.waitUntil(lambda: not shell.window.isVisible(), timeout=10000)


def test_one_window_retains_position_and_open_close_during_goal_is_display_only(
    qtbot: QtBot, tmp_path: Path
) -> None:
    device = Device()
    shell, peer = _start(qtbot, tmp_path, device)
    try:
        shell.window.controller_action.trigger()
        controller = shell.controller
        assert controller is not None
        qtbot.waitUntil(controller.controls["A"].isEnabled)
        assert controller.screen() == shell.window.screen()
        assert controller.screen().availableGeometry().contains(controller.frameGeometry())
        controller.move(controller.pos() + QPoint(15, 15))
        position = controller.pos()
        shell.window.controller_action.trigger()
        assert shell.controller is controller
        controller.close()
        shell.window.controller_action.trigger()
        assert controller.pos() == position
        shell.window.connection.connect_action.trigger()
        shell.window.connection.prompt.setPlainText("ready")
        qtbot.waitUntil(shell.window.connection.send_button.isEnabled)
        shell.service.set_goal("run")
        qtbot.waitUntil(lambda: not controller.controls["A"].isEnabled())
        qtbot.waitUntil(lambda: any(method == "thread/goal/set" for method, _ in peer.calls))
        calls = list(device.calls)
        requests = [call for call in peer.calls if call[0].startswith(("turn/", "thread/goal/"))]
        for _ in range(3):
            controller.close()
            shell.window.controller_action.trigger()
            qtbot.mouseClick(controller.controls["A"], Qt.MouseButton.LeftButton)
        qtbot.wait(30)
        assert device.calls == calls
        assert [
            call for call in peer.calls if call[0].startswith(("turn/", "thread/goal/"))
        ] == requests
        assert shell.service.inputs.connected
    finally:
        _close(qtbot, shell)


@pytest.mark.parametrize("failure", [False, True])
def test_exit_cancel_or_save_failure_reenables_only_fresh_input(
    qtbot: QtBot, tmp_path: Path, *, failure: bool
) -> None:
    saving, finish = Event(), Event()

    def save(path: Path, settings: Settings) -> None:
        saving.set()
        assert finish.wait(5)
        if failure:
            message = "injected settings failure"
            raise SettingsError(message)
        save_settings(path, settings)

    device = Device()
    shell, _peer = _start(qtbot, tmp_path, device, SettingsIO(load_settings, save))
    try:
        shell.open_settings()
        assert shell.dialog is not None
        shell.dialog.preview_fps.setCurrentIndex(shell.dialog.preview_fps.findData(60))
        shell.window.controller_action.trigger()
        controller = shell.controller
        assert controller is not None
        qtbot.waitUntil(controller.controls["A"].isEnabled)
        qtbot.mousePress(controller.controls["A"], Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: InputState(buttons=("A",)) in device.calls)
        if failure:
            shell.apply_settings()
            qtbot.waitUntil(saving.is_set)
        shell.request_close()
        assert not controller.controls["A"].isEnabled()
        if failure:
            finish.set()
            qtbot.waitUntil(lambda: shell.operation is None)
        else:
            assert shell.confirmation is not None
            shell.confirmation.button(QMessageBox.StandardButton.Cancel).click()
        qtbot.waitUntil(controller.controls["A"].isEnabled)
        assert device.calls[-1] == InputState()
        assert not controller.model.held
        count = len(device.calls)
        qtbot.mouseRelease(controller.controls["A"], Qt.MouseButton.LeftButton)
        qtbot.wait(30)
        assert len(device.calls) == count
        qtbot.mouseClick(controller.controls["B"], Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: InputState(buttons=("B",)) in device.calls)
        assert not shell.committed
    finally:
        finish.set()
        _close(qtbot, shell)


def test_shutdown_keeps_windows_and_gui_responsive_until_manual_release(
    qtbot: QtBot, tmp_path: Path
) -> None:
    entered, finish = Event(), Event()

    class SlowNeutral(Device):
        async def send(self, state: InputState) -> None:
            if state == InputState():
                entered.set()
                assert await asyncio.to_thread(finish.wait, 5)
            await super().send(state)

    device = SlowNeutral()
    shell, _peer = _start(qtbot, tmp_path, device)
    try:
        shell.window.controller_action.trigger()
        controller = shell.controller
        assert controller is not None
        qtbot.waitUntil(controller.controls["A"].isEnabled)
        qtbot.mousePress(controller.controls["A"], Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: InputState(buttons=("A",)) in device.calls)
        shell.request_close()
        qtbot.waitUntil(entered.is_set)
        ticked: list[bool] = []
        QTimer.singleShot(0, lambda: ticked.append(True))
        qtbot.waitUntil(lambda: bool(ticked))
        assert shell.window.isVisible()
        assert controller.isVisible()
        assert not controller.controls["A"].isEnabled()
        assert "close" not in device.calls
        finish.set()
        qtbot.waitUntil(lambda: not shell.window.isVisible())
        assert not controller.isVisible()
        assert device.calls[-2:] == [InputState(), "close"]
    finally:
        finish.set()
        _close(qtbot, shell)


def test_input_failure_is_retained_in_notification_history(qtbot: QtBot, tmp_path: Path) -> None:
    device = Device()
    shell, _peer = _start(qtbot, tmp_path, device)
    try:
        shell.window.controller_action.trigger()
        controller = shell.controller
        assert controller is not None
        qtbot.waitUntil(controller.controls["A"].isEnabled)
        device.fail_send = True
        qtbot.mouseClick(controller.controls["A"], Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: "input" in shell.window.notifications.notices)
        notice = shell.window.notifications.notices["input"]
        assert notice.details
        assert not controller.controls["A"].isEnabled()
        controller.close()
        assert shell.window.notifications.notices["input"] is notice
    finally:
        device.fail_send = False
        _close(qtbot, shell)


def test_premature_service_exit_disables_controller_without_closing_windows(
    qtbot: QtBot, tmp_path: Path
) -> None:
    shell, _peer = _start(qtbot, tmp_path, Device())
    try:
        shell.window.controller_action.trigger()
        controller = shell.controller
        assert controller is not None
        qtbot.waitUntil(controller.controls["A"].isEnabled)
        shell.service.shutdown()
        qtbot.waitUntil(lambda: shell.service.wait(0))
        qtbot.waitUntil(lambda: not controller.controls["A"].isEnabled())
        assert controller.isVisible()
        assert shell.window.isVisible()
        assert not shell.committed
    finally:
        _close(qtbot, shell)
