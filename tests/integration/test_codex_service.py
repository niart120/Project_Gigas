import asyncio
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, QTimer
from pytestqt.qtbot import QtBot

from gigas.runtime.codex.models import ConnectionEvent, ConnectionOptions, SessionStatus
from gigas.services.codex import CodexServiceThread

if TYPE_CHECKING:
    from gigas.services.codex import ServiceEvent, ServiceResult


class ControlledConnection:
    """Expose observable async operations without a runtime process."""

    def __init__(self, emit: Callable[[ConnectionEvent], None]) -> None:
        """Capture worker identity and connection events."""
        self.emit = emit
        self.thread = QThread.currentThread()
        self.opened = Event()
        self.sending = Event()
        self.stopped = Event()
        self.closed = Event()
        self.cancelled = Event()

    async def open(self) -> None:
        """Publish readiness from the worker loop."""
        self.opened.set()
        self.emit(SessionStatus("ready", "model", "high", True))

    async def login(self) -> None:
        """Provide the login surface."""

    async def send(self, prompt: str) -> None:
        """Hold one operation until shutdown cancels it."""
        assert prompt == "hello"
        self.sending.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()

    async def answer(
        self, token: str, *, decision: str | None = None, answers: Mapping[str, str] | None = None
    ) -> None:
        """Provide the answer surface."""

    async def stop(self) -> None:
        """Observe interruption while send is pending."""
        self.stopped.set()

    async def close(self) -> None:
        """Observe physical service cleanup."""
        self.closed.set()


def test_worker_owns_connection_and_interrupts_while_send_waits(
    qtbot: QtBot, tmp_path: Path
) -> None:
    connections: list[ControlledConnection] = []
    events: list[ServiceEvent] = []
    results: list[ServiceResult] = []

    def factory(
        options: ConnectionOptions, emit: Callable[[ConnectionEvent], None]
    ) -> ControlledConnection:
        assert options.cwd.is_dir()
        connection = ControlledConnection(emit)
        connections.append(connection)
        return connection

    service = CodexServiceThread(factory)
    service.received.connect(events.append)
    service.completed.connect(results.append)
    service.start()
    try:
        pulse = Event()
        QTimer.singleShot(0, pulse.set)
        qtbot.waitUntil(pulse.is_set)
        assert not connections
        generation = service.connect_runtime(ConnectionOptions(tmp_path / "runtime"))
        qtbot.waitUntil(lambda: len(events) == 1)
        connection = connections[0]
        assert connection.thread != QThread.currentThread()
        assert events[0].generation == generation
        service.send("hello")
        qtbot.waitUntil(connection.sending.is_set)
        service.interrupt()
        qtbot.waitUntil(connection.stopped.is_set)
        pulse.clear()
        QTimer.singleShot(0, pulse.set)
        qtbot.waitUntil(pulse.is_set)
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))
    assert connection.closed.is_set()
    assert connection.cancelled.is_set()
    assert any(result.operation == "stop" and not result.error for result in results)


def test_shutdown_before_start_and_factory_failure_leave_no_worker(
    qtbot: QtBot, tmp_path: Path
) -> None:
    called = Event()

    def failed_factory(
        _options: ConnectionOptions, _emit: Callable[[ConnectionEvent], None]
    ) -> ControlledConnection:
        called.set()
        message = "private credential"
        raise OSError(message)

    service = CodexServiceThread(failed_factory)
    results: list[ServiceResult] = []
    service.completed.connect(results.append)
    service.connect_runtime(ConnectionOptions(tmp_path))
    service.shutdown()
    service.start()
    qtbot.waitUntil(lambda: service.wait(0))
    assert not called.is_set()

    service = CodexServiceThread(failed_factory)
    service.completed.connect(results.append)
    service.start()
    try:
        service.connect_runtime(ConnectionOptions(tmp_path))
        qtbot.waitUntil(lambda: bool(results))
        assert results[0].error
        assert "private credential" not in results[0].error
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))
