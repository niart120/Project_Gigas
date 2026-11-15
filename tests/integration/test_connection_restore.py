from pathlib import Path
from threading import Event

import pytest
from pytestqt.qtbot import QtBot

from gigas.app.settings_worker import SettingsIO
from gigas.game_io.input.config import resolve_serial_config
from gigas.services.codex import ServiceResult
from gigas.storage.settings import Settings, load_settings, save_settings
from tests.integration.test_project_gui import close, connect, make_shell


def test_start_applies_saved_devices_once(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.toml"
    settings = Settings(capture_device="camera", serial_port="COM3")
    save_settings(path, settings)
    shell, peer, capture = make_shell(path)
    applied = []
    monkeypatch.setattr(shell.service, "apply_input", applied.append)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        assert len(capture.connections) == 1
        assert applied == [resolve_serial_config("COM3", "ch552", None)]
        assert peer.calls == []
        shell.change_window_size("hd")
        qtbot.waitUntil(lambda: shell.operation is None)
        assert len(capture.connections) == 1
        assert len(applied) == 1
    finally:
        close(qtbot, shell)


def test_successful_codex_connection_is_restored_until_explicit_disconnect(
    qtbot: QtBot, tmp_path: Path
) -> None:
    path = tmp_path / "settings.toml"
    shell, _, _ = make_shell(path)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        connect(qtbot, shell)
        qtbot.waitUntil(lambda: shell.operation is None)
        assert load_settings(path).codex_auto_connect
    finally:
        close(qtbot, shell)

    restored, peer, _ = make_shell(path)
    restored.start()
    try:
        qtbot.waitUntil(restored.window.connection.disconnect_action.isEnabled)
        assert any(method == "initialize" for method, _ in peer.calls)
        assert not any(
            method in {"thread/start", "thread/resume", "turn/start"} for method, _ in peer.calls
        )
        restored.window.connection.disconnect_action.trigger()
        qtbot.waitUntil(lambda: not restored.baseline.codex_auto_connect)
        assert not load_settings(path).codex_auto_connect
    finally:
        close(qtbot, restored)


def test_invalid_settings_do_not_start_connections(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('[agent]\nauto_connect = "invalid"', encoding="utf-8")
    shell, peer, capture = make_shell(path)
    applied = []
    monkeypatch.setattr(shell.service, "apply_input", applied.append)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.operation is None)
        assert not shell.ready
        assert shell.error
        assert not peer.calls
        assert not capture.connections
        assert not applied
    finally:
        close(qtbot, shell)


def test_connection_memory_waits_for_save_and_preserves_editor_draft(
    qtbot: QtBot, tmp_path: Path
) -> None:
    entered, release = Event(), Event()
    path = tmp_path / "settings.toml"

    def save(target: Path, settings: Settings) -> None:
        if settings.window_size_preset == "hd" and not settings.codex_auto_connect:
            entered.set()
            assert release.wait(5)
        save_settings(target, settings)

    shell, _, _ = make_shell(path, io=SettingsIO(load_settings, save))
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        shell.change_window_size("hd")
        qtbot.waitUntil(entered.is_set)
        shell.service.completed.emit(ServiceResult(shell.conversations.generation, "connect"))
        release.set()
        qtbot.waitUntil(lambda: shell.operation is None)
        assert load_settings(path).window_size_preset == "hd"
        assert load_settings(path).codex_auto_connect
        shell.open_settings()
        assert shell.dialog is not None
        shell.dialog.preview_fps.setCurrentIndex(shell.dialog.preview_fps.findData(60))
        shell.service.completed.emit(ServiceResult(shell.conversations.generation, "disconnect"))
        qtbot.waitUntil(lambda: shell.operation is None)
        assert shell.dialog.preview_fps.currentData() == 60
        assert shell.dirty
        assert not shell.dialog.values().codex_auto_connect
        assert load_settings(path).model == ""
        shell.dialog.close_button.click()
    finally:
        release.set()
        close(qtbot, shell)


def test_failed_or_stale_connection_result_keeps_saved_preference(
    qtbot: QtBot, tmp_path: Path
) -> None:
    path = tmp_path / "settings.toml"
    shell, _, _ = make_shell(path)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        generation = shell.conversations.generation
        shell.service.completed.emit(ServiceResult(generation, "connect", "failed"))
        shell.service.completed.emit(ServiceResult(generation - 1, "connect"))
        assert not shell.baseline.codex_auto_connect
        assert shell.operation is None
    finally:
        close(qtbot, shell)


def test_failed_startup_connection_is_not_retried_or_forgotten(
    qtbot: QtBot, tmp_path: Path
) -> None:
    path = tmp_path / "settings.toml"
    save_settings(path, Settings(codex_auto_connect=True))
    shell, peer, _ = make_shell(path)
    peer.failure = "initialize"
    results = []
    shell.service.completed.connect(results.append)
    shell.start()
    try:
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        assert next(result for result in results if result.operation == "connect").error
        shell.change_window_size("hd")
        qtbot.waitUntil(lambda: shell.operation is None)
        assert len([result for result in results if result.operation == "connect"]) == 1
        assert load_settings(path).codex_auto_connect
        assert shell.window.notifications.notices
    finally:
        close(qtbot, shell)
