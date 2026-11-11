from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot

from gigas.app.settings_worker import SettingsIO
from gigas.app.shell import ApplicationShell
from gigas.game_io.capture.models import DeviceInfo
from gigas.runtime.codex.models import SessionStatus
from gigas.services.codex import ServiceEvent
from gigas.storage.settings import Settings, SettingsError, load_settings, save_settings
from gigas.ui.main_window import MainWindow
from tests.integration.peers.controlled_capture import ControlledCapture
from tests.integration.test_video_gui import (
    video_shell as video_shell,  # noqa: PLC0414 -- reuse bounded application lifetime
)


def _stream(shell: ApplicationShell, capture: ControlledCapture, qtbot: QtBot) -> None:
    shell.connect_video()
    capture.state = replace(
        capture.state,
        devices=(DeviceInfo("Camera & source", "path-a"), DeviceInfo("Camera & source", "path-b")),
        devices_revision=1,
    )
    capture.stream()
    qtbot.waitUntil(lambda: not shell.video.applying)


def test_file_and_codex_menus_use_live_actions_and_exit_lifetime(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    window = shell.window
    assert window.settings_action in window.file_menu.actions()
    assert window.exit_action in window.file_menu.actions()
    assert window.view_menu.actions() == list(window.window_size_actions.values())
    panel = window.connection
    assert panel.connect_action in window.codex_menu.actions()
    assert panel.disconnect_action in window.codex_menu.actions()
    assert panel.login_action in window.codex_menu.actions()
    panel.activate(1)
    panel.handle_event(ServiceEvent(1, SessionStatus("running")))
    assert not panel.connect_action.isEnabled()
    assert panel.disconnect_action.isEnabled()
    disconnected: list[bool] = []
    panel.disconnect_requested.connect(lambda: disconnected.append(True))
    panel.disconnect_action.trigger()
    assert disconnected == [True]
    window.settings_action.trigger()
    assert shell.dialog is not None
    assert shell.dialog.isVisible()
    window.exit_action.trigger()
    qtbot.waitUntil(lambda: not window.isVisible())
    assert capture.shutdowns == 1


def test_video_menu_discovers_on_open_without_waiting_for_a_refresh_button(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    menu = shell.window.video_menu
    assert not menu.isVisible()
    menu.aboutToShow.emit()
    assert capture.refreshes == 1
    assert capture.state.phase == "enumerating"
    capture.complete_enumeration((DeviceInfo("Found camera", "path-b"),))
    qtbot.waitUntil(lambda: "path-b" in shell.window.video_device_actions)
    assert shell.window.video_device_actions["path-b"].isChecked()
    assert not capture.connections
    assert load_settings(shell.path) == shell.baseline


def test_long_duplicate_menu_names_keep_disambiguating_suffixes(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    name = "Repeated & capture " * 50
    window.set_capture_devices(
        (DeviceInfo(name, "first"), DeviceInfo(name, "second")),
        "second",
        checked=True,
        searching=False,
        error="",
        enabled=True,
    )
    for number, path in enumerate(("first", "second"), 1):
        action = window.video_device_actions[path]
        assert action.text().endswith(f" ({number})")
        assert len(action.text()) < len(name)
        assert action.toolTip() == f"<qt>{name.replace('&', '&amp;')} ({number})</qt>"
        assert action.data() == path


def test_replacing_a_duplicate_identity_catalog_removes_every_old_menu_action(
    qtbot: QtBot,
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_capture_devices(
        (DeviceInfo("First", "same"), DeviceInfo("Repeated identity", "same")),
        "same",
        checked=True,
        searching=False,
        error="",
        enabled=True,
    )
    old_actions = [action for action in window.video_menu.actions() if action.isCheckable()]
    assert len(old_actions) == 3
    window.set_capture_devices(
        (DeviceInfo("Replacement", "new"),),
        "new",
        checked=True,
        searching=False,
        error="",
        enabled=True,
    )
    assert all(action not in window.video_menu.actions() for action in old_actions)
    assert [action.data() for action in window.video_menu.actions() if action.isCheckable()] == [
        "",
        "new",
    ]


def test_streaming_menu_and_settings_share_choices_commands_and_active_identity(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    _stream(shell, capture, qtbot)
    window = shell.window
    window.video_menu.aboutToShow.emit()
    window.settings_action.trigger()
    dialog = shell.dialog
    assert dialog is not None
    assert capture.refreshes == 0
    assert dialog.capture_connect_button.defaultAction() is window.video.connect_action
    assert dialog.capture_disconnect_button.defaultAction() is window.video.disconnect_action
    for path, action in window.video_device_actions.items():
        index = dialog.capture_choices.findData(path)
        assert index >= 0
        assert action.text().replace("&&", "&") == dialog.capture_choices.itemText(index)
        assert action.data() == path
    assert window.video.source.text() == "Camera & source (2)"
    assert window.video.source.parentWidget() is window.statusBar()
    assert window.video.status.parentWidget() is window.statusBar()
    assert not window.video.findChildren(QPushButton)
    dialog.model.setText("unsaved model")
    assert all(not action.isEnabled() for action in window.video_device_actions.values())
    assert window.video.disconnect_action.isEnabled()
    window.video_device_actions["path-a"].trigger()
    assert shell.baseline.capture_device == "path-b"
    assert dialog.model.text() == "unsaved model"
    dialog.set_values(shell.baseline)
    shell._render()
    window.video.disconnect_action.trigger()
    qtbot.waitUntil(lambda: capture.refreshes == 1)
    assert window.video.source.text() == ""
    assert shell.baseline.capture_device == "path-b"
    assert window.video_device_actions["path-b"].isChecked()
    capture.complete_enumeration(capture.state.devices)
    qtbot.waitUntil(window.video.connect_action.isEnabled)
    dialog.capture_connect_button.click()
    assert len(capture.connections) == 2


@pytest.mark.parametrize(("path", "fps"), [("path-a", 60), ("path-b", 30)])
def test_selecting_saved_menu_device_applies_configuration_changed_by_reload(
    video_shell: tuple[ApplicationShell, ControlledCapture],
    qtbot: QtBot,
    path: str,
    fps: int,
) -> None:
    shell, capture = video_shell
    _stream(shell, capture, qtbot)
    save_settings(shell.path, replace(shell.baseline, capture_device=path, capture_fps=fps))
    shell.reload()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.window.video_device_actions[path].isChecked()
    assert shell.window.video.source.text() == "Camera & source (2)"
    assert len(capture.connections) == 1
    shell.window.video_device_actions[path].trigger()
    qtbot.waitUntil(lambda: len(capture.connections) == 2)
    assert capture.connections[-1].device_path == path
    assert capture.connections[-1].fps == fps
    capture.stream()
    qtbot.waitUntil(lambda: not shell.video.applying)


@pytest.mark.parametrize("fail", [False, True])
def test_menu_selection_checks_only_saved_identity_and_uses_common_application(
    qtbot: QtBot, tmp_path: Path, fail: bool
) -> None:
    entered = Event()
    release = Event()

    def save(path: Path, settings: Settings) -> None:
        entered.set()
        if not release.wait(5):
            raise TimeoutError
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
        _stream(shell, capture, qtbot)
        shell.window.video_device_actions["path-a"].trigger()
        qtbot.waitUntil(entered.is_set)
        assert shell.window.video_device_actions["path-b"].isChecked()
        assert shell.window.video.source.text() == "Camera & source (2)"
        assert capture.disconnects == 0
        release.set()
        qtbot.waitUntil(lambda: shell.operation is None)
        selected = "path-b" if fail else "path-a"
        assert shell.window.video_device_actions[selected].isChecked()
        assert load_settings(path).capture_device == selected
        assert len(capture.connections) == (1 if fail else 2)
        assert shell.window.video.source.text() == f"Camera & source ({2 if fail else 1})"
    finally:
        release.set()
        qtbot.waitUntil(lambda: shell.operation is None)
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())


@pytest.mark.parametrize("hide", [False, True])
def test_menu_open_during_initial_load_discovers_once_only_while_still_open(
    qtbot: QtBot, tmp_path: Path, hide: bool
) -> None:
    entered = Event()
    release = Event()

    def load(_path: Path) -> Settings:
        entered.set()
        if not release.wait(5):
            raise TimeoutError
        return Settings(capture_device="saved-path")

    capture = ControlledCapture()
    shell = ApplicationShell(
        tmp_path / "settings.toml", SettingsIO(load=load), capture_service=capture
    )
    shell.start()
    try:
        qtbot.waitUntil(entered.is_set)
        menu = shell.window.video_menu
        menu.popup(shell.window.mapToGlobal(QPoint(20, 40)))
        qtbot.waitUntil(menu.isVisible)
        assert capture.refreshes == 0
        if hide:
            menu.hide()
        release.set()
        qtbot.waitUntil(lambda: shell.ready)
        assert capture.refreshes == (0 if hide else 1)
        menu.hide()
    finally:
        release.set()
        qtbot.waitUntil(lambda: shell.operation is None)
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())
