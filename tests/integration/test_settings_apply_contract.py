from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.game_io.capture.models import DeviceInfo
from gigas.game_io.input.config import resolve_serial_config
from gigas.game_io.input.serial import SerialPort
from gigas.storage.settings import Settings, load_settings, save_settings
from gigas.ui.main_window import MainWindow
from gigas.ui.settings_dialog import SettingsDialog
from tests.integration.peers.controlled_capture import ControlledCapture

if TYPE_CHECKING:
    from gigas.game_io.input.config import SerialConfig


@pytest.fixture
def app(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[ApplicationShell, ControlledCapture]]:
    path = tmp_path / "settings.toml"
    save_settings(
        path, Settings(capture_device="path-a", serial_port="COM3", serial_protocol="pokecon")
    )
    capture = ControlledCapture()
    shell = ApplicationShell(path, capture_service=capture)
    monkeypatch.setattr(shell.service, "apply_input", lambda _config: None)
    shell.start()
    qtbot.waitUntil(lambda: shell.ready)
    capture.state = replace(
        capture.state,
        devices=(DeviceInfo("Camera A", "path-a"), DeviceInfo("Camera B", "path-b")),
        devices_revision=1,
    )
    shell.video.timer.timeout.emit()
    try:
        yield shell, capture
    finally:
        capture.finish_disconnect = True
        capture.finish_shutdown = True
        capture.state = replace(capture.state, cleanup="graceful")
        qtbot.waitUntil(lambda: shell.operation is None)
        if shell.dialog is not None:
            shell.dialog.hide()
            shell.dialog = None
        if shell.confirmation is not None:
            shell.confirmation.reject()
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())


def open_editor(shell: ApplicationShell, capture: ControlledCapture) -> SettingsDialog:
    """Open settings and complete the explicitly controlled discovery request."""
    shell.open_settings()
    if capture.snapshot().phase == "enumerating":
        capture.complete_enumeration(
            (DeviceInfo("Camera A", "path-a"), DeviceInfo("Camera B", "path-b"))
        )
        shell.video.timer.timeout.emit()
    dialog = shell.dialog
    assert dialog is not None
    return dialog


def test_editor_keeps_project_path_when_editing_visible_settings(
    qtbot: QtBot, tmp_path: Path
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    dialog = SettingsDialog(tmp_path / "settings.toml", window)
    original = Settings(last_project_path=str(tmp_path / "project"))
    dialog.set_values(original)
    dialog.model.setText("changed")

    assert dialog.values() == replace(original, model="changed", serial_baudrate=9600)


def test_explicit_offline_apply_clears_saved_device_after_same_device_can_start(
    app: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = app
    dialog = open_editor(shell, capture)
    assert dialog.capture_choices.currentData() == "path-a"
    assert dialog.values().capture_device == "path-a"
    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData("path-a"))
    assert not shell.dirty
    assert dialog.save_button.isEnabled()
    dialog.save_button.click()
    qtbot.waitUntil(lambda: len(capture.connections) == 1)
    capture.stream()
    qtbot.waitUntil(lambda: not shell.video.applying)
    dialog.capture_choices.setCurrentIndex(0)
    dialog.save_button.click()
    qtbot.waitUntil(lambda: capture.snapshot().phase == "idle")
    assert shell.baseline.capture_device == ""
    assert load_settings(shell.path).capture_device == ""


def test_explicit_offline_choice_clears_device_even_when_already_stopped(
    app: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = app
    dialog = open_editor(shell, capture)
    assert not shell.dirty
    dialog.capture_choices.setCurrentIndex(0)
    assert shell.dirty
    dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert load_settings(shell.path).capture_device == ""
    assert not capture.connections


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize(
    ("changed", "capture_applied", "input_applied"),
    [
        (("port",), False, True),
        (("protocol",), False, True),
        (("baud",), False, True),
        (("capture",), True, False),
        (("observation",), False, False),
        (("general",), True, False),
        ((), True, True),
        (("port", "observation"), False, True),
        (("capture", "observation"), True, False),
        (("port", "capture"), True, True),
        (("port", "capture", "observation"), True, True),
        (("general", "observation"), True, False),
        (("general", "port"), True, True),
    ],
)
def test_apply_effect_depends_on_changed_settings(
    app: tuple[ApplicationShell, ControlledCapture],
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    changed: tuple[str, ...],
    *,
    running: bool,
    capture_applied: bool,
    input_applied: bool,
) -> None:
    shell, capture = app
    dialog = open_editor(shell, capture)
    codex_connects: list[object] = []
    applied_resolutions: list[str] = []
    input_configs: list[SerialConfig | None] = []
    monkeypatch.setattr(shell.service, "connect_runtime", codex_connects.append)
    monkeypatch.setattr(shell.service, "set_observation_resolution", applied_resolutions.append)
    monkeypatch.setattr(shell.service, "apply_input", input_configs.append)
    assert shell.baseline.serial_baudrate is None
    assert dialog.values().serial_baudrate == 9600
    assert not shell.dirty
    if running:
        dialog.save_button.click()
        qtbot.waitUntil(lambda: len(capture.connections) == 1)
        capture.stream()
        qtbot.waitUntil(lambda: not shell.video.applying)
    input_configs.clear()
    frame = capture.snapshot().frame
    for change in changed:
        if change == "port":
            dialog.set_serial_ports((SerialPort("COM7", "Controller"),))
            dialog.serial_port.setCurrentIndex(dialog.serial_port.findData("COM7"))
        elif change == "protocol":
            dialog.serial_protocol.setCurrentIndex(dialog.serial_protocol.findData("3ds"))
        elif change == "baud":
            dialog.serial_baudrate.setCurrentIndex(dialog.serial_baudrate.findData(57600))
        elif change == "capture":
            dialog.capture_fps.setCurrentIndex(dialog.capture_fps.findData(30))
        elif change == "observation":
            dialog.observation_resolution.setCurrentIndex(
                dialog.observation_resolution.findData("hd")
            )
        elif change == "general":
            dialog.preview_mode.setCurrentIndex(dialog.preview_mode.findData(False))
    assert shell.dirty == bool(changed)
    target = dialog.values()
    dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    expected_connections = int(running) + int(
        capture_applied and (not running or "capture" in changed)
    )
    qtbot.waitUntil(lambda: len(capture.connections) == expected_connections)
    assert capture.disconnects == int(running and "capture" in changed)
    if not capture_applied:
        assert capture.snapshot().frame is frame
    assert input_configs == (
        [resolve_serial_config(target.serial_port, target.serial_protocol, target.serial_baudrate)]
        if input_applied
        else []
    )
    assert load_settings(shell.path) == target
    assert shell.baseline == target
    assert not shell.dirty
    assert codex_connects == []
    assert applied_resolutions[-1] == target.observation_resolution
    assert dialog.save_button.isEnabled()


def test_failed_target_can_be_retried_without_an_edit(
    app: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = app
    dialog = open_editor(shell, capture)
    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData("path-b"))
    dialog.save_button.click()
    qtbot.waitUntil(lambda: len(capture.connections) == 1)
    capture.state = replace(capture.state, phase="error", cleanup="graceful", error="open failed")
    qtbot.waitUntil(lambda: shell.video.apply_state == "failed")
    assert not shell.dirty
    assert dialog.capture_choices.currentData() == "path-b"
    assert dialog.save_button.isEnabled()
    dialog.save_button.click()
    qtbot.waitUntil(lambda: len(capture.connections) == 2)
    capture.stream()
    qtbot.waitUntil(lambda: not shell.video.applying)
