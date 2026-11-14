import asyncio
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from gigas.app.settings_worker import SettingsIO
from gigas.app.shell import ApplicationShell
from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.runtime.codex.connection import CodexConnection, RuntimeEvent
from gigas.runtime.codex.models import ConnectionOptions, ConversationStatus
from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcNotification
from gigas.services.codex import CodexServiceThread
from gigas.storage.settings import Settings, SettingsError, load_settings
from tests.unit.test_codex_connection import FakePeer, _terminal

if TYPE_CHECKING:
    from gigas.services.codex import ServiceEvent


class LifetimePeer(FakePeer):
    """Provide explicit turn notifications for the shutdown scenarios."""

    async def request(self, method: str, params: JsonObject | None) -> JsonValue:
        """Start and terminate one turn through the public notification boundary."""
        result = await super().request(method, params)
        if method == "turn/start":
            self.events.put_nowait(
                RpcNotification("turn/started", {"threadId": "thread-1", "turn": {"id": "turn-1"}})
            )
        elif method == "turn/interrupt":
            self.events.put_nowait(_terminal("interrupted"))
        return result


class LifetimeConnection(CodexConnection):
    """Control connection cleanup while the real execution layer owns stopping."""

    def __init__(self, options: ConnectionOptions, emit: Callable[[RuntimeEvent], None]) -> None:
        """Prepare a scripted transport and an observable close barrier."""
        self.peer = LifetimePeer()
        super().__init__(self.peer, options, emit)
        self.closing = Event()
        self.release = Event()

    @property
    def stops(self) -> int:
        """Count actual interrupt requests, excluding connection cleanup."""
        return sum(method == "turn/interrupt" for method, _ in self.peer.calls)

    @property
    def sends(self) -> int:
        """Count actual user message submissions."""
        return sum(method == "turn/start" for method, _ in self.peer.calls)

    async def close(self) -> None:
        """Keep the owned process alive until the test releases cleanup."""
        self.closing.set()
        assert await asyncio.to_thread(self.release.wait, 10)
        await super().close()


def _start(
    qtbot: QtBot, tmp_path: Path, io: SettingsIO | None = None
) -> tuple[ApplicationShell, LifetimeConnection]:
    connections: list[LifetimeConnection] = []

    def factory(
        options: ConnectionOptions,
        emit: Callable[[RuntimeEvent], None],
    ) -> LifetimeConnection:
        connection = LifetimeConnection(options, emit)
        connections.append(connection)
        return connection

    shell = ApplicationShell(
        tmp_path / "settings.toml",
        io,
        service=CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot), factory),
    )
    events: list[ServiceEvent] = []
    shell.service.received.connect(events.append)
    shell.start()
    qtbot.waitUntil(lambda: shell.ready)
    panel = shell.window.connection
    panel.connect_action.trigger()
    panel.prompt.setPlainText("hello")
    qtbot.waitUntil(panel.send_button.isEnabled)
    panel.send_button.click()
    qtbot.waitUntil(
        lambda: any(
            isinstance(item.event, ConversationStatus) and item.event.turn_id == "turn-1"
            for item in events
        )
    )
    return shell, connections[0]


def test_close_interrupts_before_settings_finish_and_never_resumes(
    qtbot: QtBot, tmp_path: Path
) -> None:
    saving = Event()
    release_save = Event()

    def save(_path: Path, _settings: Settings) -> None:
        saving.set()
        if not release_save.wait(5):
            message = "test did not release save"
            raise TimeoutError(message)
        message = "injected save failure"
        raise SettingsError(message)

    shell, connection = _start(qtbot, tmp_path, SettingsIO(load_settings, save))
    try:
        shell.open_settings()
        assert shell.dialog is not None
        shell.dialog.model.setText("changed")
        shell.apply_settings()
        qtbot.waitUntil(saving.is_set)
        shell.request_close()
        qtbot.waitUntil(lambda: connection.stops == 1)
        assert shell.window.isVisible()
        assert not connection.closing.is_set()
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
