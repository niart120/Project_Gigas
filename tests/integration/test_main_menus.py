from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QPushButton
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


@pytest.mark.parametrize(("width", "sizes"), [(800, [300, 480]), (1100, [700, 380])])
def test_status_regions_follow_splitter_without_duplicate_conversation_labels(
    qtbot: QtBot, width: int, sizes: list[int]
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(width, 550)
    window.show()
    window.connection.activate(1)
    window.connection.handle_event(
        ServiceEvent(1, SessionStatus("running", "model/" * 200, "high"))
    )
    window.video.status.setText("終了未確認")
    window.video.source.setText("capture/" * 200)
    for allocation in (sizes, list(reversed(sizes))):
        window.splitter.setSizes(allocation)
        QApplication.processEvents()
        for pane, status, identifier in (
            (window.video, window.video.status, window.video.source),
            (window.splitter.widget(1), window.connection.status, window.connection.model),
        ):
            assert pane is not None
            region = status.parentWidget()
            assert region is not None
            assert window.statusBar().isAncestorOf(region)
            assert not window.connection.isAncestorOf(status)
            assert identifier.parentWidget() is region
            assert region.mapToGlobal(QPoint()).x() == pane.mapToGlobal(QPoint()).x()
            assert status.isVisible()
            assert identifier.isVisible()
            assert status.width() >= status.fontMetrics().horizontalAdvance(status.text())
            assert region.rect().contains(status.geometry())
            assert region.rect().contains(identifier.geometry())
        assert window.width() == width
        window.resize(width - 40, 550)
        QApplication.processEvents()
        codex_region = window.connection.status.parentWidget()
        conversation = window.splitter.widget(1)
        assert codex_region is not None
        assert conversation is not None
        assert codex_region.mapToGlobal(QPoint()).x() == conversation.mapToGlobal(QPoint()).x()
        window.resize(width, 550)
    assert window.connection.model.text() == "model/" * 200 + " · high"


def test_close_progress_appears_only_for_pending_work_and_keeps_cancel_action(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    cancelled: list[bool] = []
    window.cancel_close_requested.connect(lambda: cancelled.append(True))
    window.refresh_close_progress()
    assert not window.close_progress.isVisible()
    assert not window.statusBar().isAncestorOf(window.cancel_close_button)
    window.status.setText("終了待ち")
    window.cancel_close_button.setVisible(True)
    window.refresh_close_progress()
    assert window.close_progress.isVisible()
    assert window.cancel_close_button.isVisible()
    window.cancel_close_button.click()
    assert cancelled == [True]
    window.cancel_close_button.hide()
    window.refresh_close_progress()
    assert window.close_progress.isVisible()
    assert not window.cancel_close_button.isVisible()
    window.status.clear()
    window.refresh_close_progress()
    assert not window.close_progress.isVisible()


def test_status_region_keeps_short_state_when_splitter_is_dragged_to_the_edge(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(800, 550)
    window.show()
    window.video.status.setText("release unconfirmed")
    window.video.source.setText("long capture name/" * 100)
    window.splitter.setSizes([1, 1000])
    QApplication.processEvents()
    region = window.video.status.parentWidget()
    assert region is not None
    assert region.width() > 0
    assert region.rect().contains(window.video.status.geometry())


def test_recovery_menu_actions_can_be_bound_without_statusbar_controls(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    actions = (window.reload_action, window.settings_location_action)
    triggered: list[object] = []
    for action in actions:
        assert action in window.file_menu.actions()
        action.triggered.connect(lambda _checked=False, selected=action: triggered.append(selected))
        action.trigger()
    assert triggered == list(actions)
    assert not window.statusBar().findChildren(QPushButton)


def test_file_and_codex_menus_use_live_actions_and_exit_lifetime(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    window = shell.window
    assert window.settings_action in window.file_menu.actions()
    assert window.exit_action in window.file_menu.actions()
    assert [action for action in window.view_menu.actions() if not action.isSeparator()] == [
        *window.window_size_actions.values(),
        window.video.info_action,
        window.notifications.entry_action,
    ]
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
    assert shell.window.video_device_actions[""].isChecked()
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
    for path, action in window.video_device_actions.items():
        index = dialog.capture_choices.findData(path)
        assert index >= 0
        assert action.text().replace("&&", "&") == dialog.capture_choices.itemText(index)
        assert action.data() == path
    assert window.video.source.text() == "Camera & source (2)"
    assert window.statusBar().isAncestorOf(window.video.source)
    assert window.statusBar().isAncestorOf(window.video.status)
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
    assert window.video_device_actions[""].isChecked()
    assert dialog.capture_target == ""
    capture.complete_enumeration(capture.state.devices)
    qtbot.waitUntil(window.video.connect_action.isEnabled)
    window.video.connect_action.trigger()
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
    assert shell.window.video_device_actions["path-b"].isChecked()
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
