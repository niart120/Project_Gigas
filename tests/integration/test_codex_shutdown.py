import asyncio
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from gigas.app.settings_worker import SettingsIO
from gigas.app.shell import ApplicationShell
from gigas.runtime.codex.models import ConnectionEvent, ConnectionOptions, SessionStatus
from gigas.services.codex import CodexServiceThread
from gigas.storage.settings import Settings, SettingsError, load_settings, save_settings


class LifetimeConnection:
    """Control cleanup while exposing turn acceptance and stopping."""

    def __init__(self, emit: Callable[[ConnectionEvent], None]) -> None:
        """Create lifecycle observations."""
        self.emit = emit
        self.closing = Event()
        self.release = Event()
        self.stops = 0
        self.sends = 0

    async def open(self) -> None:
        """Publish a ready authenticated connection."""
        self.emit(SessionStatus("ready", "model", "high", True))

    async def login(self) -> None:
        """Provide the connection interface."""

    async def send(self, prompt: str) -> None:
        """Accept a turn without completing it."""
        assert prompt
        self.sends += 1
        self.emit(SessionStatus("running", "model", "high", True))

    async def answer(
        self, token: str, *, decision: str | None = None, answers: Mapping[str, str] | None = None
    ) -> None:
        """Provide the answer interface."""
        assert token
        assert decision is not None or answers is not None

    async def stop(self) -> None:
        """Count explicit interruption and report the terminal state."""
        self.stops += 1
        self.emit(SessionStatus("stopped", "model", "high", True))

    async def close(self) -> None:
        """Remain alive until the test permits cleanup."""
        self.closing.set()
        assert await asyncio.to_thread(self.release.wait, 10)
        self.emit(SessionStatus("closed"))


def _start(
    qtbot: QtBot, tmp_path: Path, io: SettingsIO | None = None
) -> tuple[ApplicationShell, LifetimeConnection]:
    connections: list[LifetimeConnection] = []

    def factory(
        _options: ConnectionOptions, emit: Callable[[ConnectionEvent], None]
    ) -> LifetimeConnection:
        connection = LifetimeConnection(emit)
        connections.append(connection)
        return connection

    shell = ApplicationShell(tmp_path / "settings.toml", io, service=CodexServiceThread(factory))
    shell.start()
    qtbot.waitUntil(lambda: shell.ready)
    panel = shell.window.connection
    panel.connect_action.trigger()
    panel.prompt.setPlainText("hello")
    qtbot.waitUntil(panel.send_button.isEnabled)
    panel.send_button.click()
    qtbot.waitUntil(panel.interrupt_button.isEnabled)
    return shell, connections[0]


@pytest.mark.parametrize("outcome", ["cancel", "save_failure"])
def test_close_interrupts_before_settings_finish_and_never_resumes(
    qtbot: QtBot, tmp_path: Path, outcome: str
) -> None:
    saving = Event()
    release_save = Event()

    def save(path: Path, settings: Settings) -> None:
        saving.set()
        if not release_save.wait(5):
            message = "test did not release save"
            raise TimeoutError(message)
        if outcome == "save_failure":
            message = "injected save failure"
            raise SettingsError(message)
        save_settings(path, settings)

    shell, connection = _start(qtbot, tmp_path, SettingsIO(load_settings, save))
    try:
        shell.open_settings()
        assert shell.dialog is not None
        shell.dialog.model.setText("changed")
        shell.save()
        qtbot.waitUntil(saving.is_set)
        shell.request_close()
        qtbot.waitUntil(lambda: connection.stops == 1)
        assert shell.window.isVisible()
        assert not connection.closing.is_set()
        if outcome == "cancel":
            shell.cancel_close()
        release_save.set()
        qtbot.waitUntil(lambda: shell.operation is None)
        assert shell.window.isVisible()
        assert not shell.committed
        assert (connection.sends, connection.stops) == (1, 1)
    finally:
        release_save.set()
        connection.release.set()
        qtbot.waitUntil(lambda: shell.operation is None)
        assert shell.dialog is not None
        shell.dialog.set_values(shell.baseline)
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())


def test_cancel_dirty_confirmation_does_not_resume_turn(qtbot: QtBot, tmp_path: Path) -> None:
    shell, connection = _start(qtbot, tmp_path)
    try:
        shell.open_settings()
        assert shell.dialog is not None
        shell.dialog.model.setText("unsaved")
        shell.request_close()
        assert shell.confirmation is not None
        shell.confirmation.button(QMessageBox.StandardButton.Cancel).click()
        qtbot.waitUntil(lambda: connection.stops == 1)
        assert shell.window.isVisible()
        assert not shell.committed
        assert connection.sends == 1
    finally:
        connection.release.set()
        assert shell.dialog is not None
        shell.dialog.set_values(shell.baseline)
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())


@pytest.mark.parametrize("last", ["settings", "service"])
def test_shutdown_joins_both_threads_without_blocking_gui(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, last: str
) -> None:
    shell, connection = _start(qtbot, tmp_path)
    completed: list[bool] = []
    shell.stopped.connect(lambda: completed.append(True))
    settings_done = Event()
    actual_wait = shell.io_thread.wait
    monkeypatch.setattr(
        shell.io_thread, "wait", lambda timeout: settings_done.is_set() and actual_wait(timeout)
    )
    try:
        shell.request_close()
        qtbot.waitUntil(connection.closing.is_set)
        if last == "settings":
            connection.release.set()
            qtbot.waitUntil(lambda: shell.service.wait(0))
        else:
            settings_done.set()
            qtbot.waitUntil(lambda: actual_wait(0))
        pulse = Event()
        QTimer.singleShot(0, pulse.set)
        qtbot.waitUntil(pulse.is_set)
        assert shell.window.isVisible()
        assert not completed
        shell.wait_timer.timeout.emit()
        assert ("設定" if last == "settings" else "Codex") in shell.window.status.text()
    finally:
        settings_done.set()
        connection.release.set()
        qtbot.waitUntil(lambda: not shell.window.isVisible())
    assert completed == [True]
    assert shell.service.wait(0)
    assert actual_wait(0)


def test_premature_service_exit_does_not_close_application(qtbot: QtBot, tmp_path: Path) -> None:
    shell = ApplicationShell(tmp_path / "settings.toml")
    shell.start()
    qtbot.waitUntil(lambda: shell.ready)
    shell.service.shutdown()
    qtbot.waitUntil(lambda: shell.service.wait(0))
    qtbot.waitUntil(lambda: not shell.window.connection.connect_action.isEnabled())
    assert shell.window.isVisible()
    assert not shell.committed
    shell.request_close()
    qtbot.waitUntil(lambda: not shell.window.isVisible())
