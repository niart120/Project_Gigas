import asyncio
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QThread, QTimer
from pytestqt.qtbot import QtBot

from gigas.runtime.codex.connection import AnswerDeliveryError, CodexConnectionError
from gigas.runtime.codex.models import ConnectionEvent, ConnectionOptions, SessionStatus
from gigas.services.codex import AnswerResult, CodexServiceThread

if TYPE_CHECKING:
    from gigas.services.codex import ServiceEvent, ServiceResult, _Command


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


class WaitingAnswerConnection(ControlledConnection):
    """Hold an answer until service shutdown cancels its operation."""

    async def answer(
        self, token: str, *, decision: str | None = None, answers: Mapping[str, str] | None = None
    ) -> None:
        """Record arrival without claiming transport completion."""
        assert (token, decision, answers) == ("pending", "accept", None)
        self.sending.set()
        await asyncio.Event().wait()


def test_answer_without_connection_reports_not_sent(qtbot: QtBot) -> None:
    service = CodexServiceThread()
    results: list[ServiceResult] = []
    service.completed.connect(results.append)
    service.start()
    try:
        service.answer("missing", decision="accept")
        qtbot.waitUntil(lambda: bool(results))
        result = results[0]
        assert result.error
        assert result.answer == AnswerResult("missing", "not_sent")
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))


def test_answer_discarded_before_start_reports_not_sent(qtbot: QtBot) -> None:
    service = CodexServiceThread()
    results: list[ServiceResult] = []
    service.completed.connect(results.append)
    service.answer("queued", decision="accept")
    service.shutdown()
    service.start()
    qtbot.waitUntil(lambda: service.wait(0))
    assert len(results) == 1
    result = results[0]
    assert result.answer == AnswerResult("queued", "not_sent")


def test_cancelled_answer_reports_unknown_once(qtbot: QtBot, tmp_path: Path) -> None:
    connections: list[WaitingAnswerConnection] = []
    results: list[ServiceResult] = []

    def factory(
        _options: ConnectionOptions, emit: Callable[[ConnectionEvent], None]
    ) -> WaitingAnswerConnection:
        connection = WaitingAnswerConnection(emit)
        connections.append(connection)
        return connection

    service = CodexServiceThread(factory)
    service.completed.connect(results.append)
    service.start()
    try:
        service.connect_runtime(ConnectionOptions(tmp_path))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        service.answer("pending", decision="accept")
        qtbot.waitUntil(connections[0].sending.is_set)
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))
    answers = [result for result in results if result.operation == "answer"]
    assert len(answers) == 1
    assert answers[0].answer == AnswerResult("pending", "unknown")


def test_answer_cancelled_before_execution_reports_not_sent(qtbot: QtBot) -> None:
    class CancelBeforeRunService(CodexServiceThread):
        """Inject cancellation after task creation but before its first execution."""

        def _dispatch(self, command: "_Command") -> None:
            super()._dispatch(command)
            for task in self._tasks:
                task.cancel()

    service = CancelBeforeRunService()
    results: list[ServiceResult] = []
    service.completed.connect(results.append)
    service.start()
    try:
        service.answer("before-run", decision="accept")
        qtbot.waitUntil(lambda: bool(results))
        assert len(results) == 1
        assert results[0].answer == AnswerResult("before-run", "not_sent")
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))


@pytest.mark.parametrize("case", ["sent", "rejected", "delivery", "unexpected"])
def test_answer_completion_preserves_delivery_evidence(
    qtbot: QtBot, tmp_path: Path, case: str
) -> None:
    class AnswerConnection(ControlledConnection):
        """Model validation, uncertain delivery, and successful transport return."""

        async def answer(
            self,
            token: str,
            *,
            decision: str | None = None,
            answers: Mapping[str, str] | None = None,
        ) -> None:
            """Expose the requested outcome without an external process."""
            assert (token, decision, answers) == ("token", "accept", None)
            if case == "rejected":
                message = "無効な回答"
                raise CodexConnectionError(message)
            if case == "delivery":
                message = "回答の送信結果を確認できない"
                raise AnswerDeliveryError(message)
            if case == "unexpected":
                message = "PRIVATE-ANSWER-ERROR"
                raise OSError(message)

    results: list[ServiceResult] = []
    service = CodexServiceThread(lambda _options, emit: AnswerConnection(emit))
    service.completed.connect(results.append)
    service.start()
    try:
        generation = service.connect_runtime(ConnectionOptions(tmp_path))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        service.answer("token", decision="accept")
        qtbot.waitUntil(lambda: any(result.operation == "answer" for result in results))
        result = next(result for result in results if result.operation == "answer")
        assert result.generation == generation
        assert (
            result.answer
            == {
                "sent": AnswerResult("token", "sent"),
                "rejected": AnswerResult("token", "not_sent"),
                "delivery": AnswerResult("token", "unknown"),
                "unexpected": AnswerResult("token", "unknown"),
            }[case]
        )
        assert bool(result.error) == (case != "sent")
        assert "PRIVATE-ANSWER-ERROR" not in result.error
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))


def test_answer_for_inactive_generation_is_not_success(qtbot: QtBot, tmp_path: Path) -> None:
    results: list[ServiceResult] = []
    service = CodexServiceThread(lambda _options, emit: ControlledConnection(emit))
    service.completed.connect(results.append)
    service.start()
    try:
        first = service.connect_runtime(ConnectionOptions(tmp_path))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        second = service.connect_runtime(ConnectionOptions(tmp_path))
        qtbot.waitUntil(lambda: any(result.generation == second for result in results))
        service.answer("wrong-generation", decision="accept")
        qtbot.waitUntil(lambda: any(result.operation == "answer" for result in results))
        result = next(result for result in results if result.operation == "answer")
        assert result.generation == second != first
        assert result.error
        assert result.answer is not None
        assert result.answer.outcome == "not_sent"
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))


def test_answer_after_service_shutdown_is_reported_not_sent(qtbot: QtBot) -> None:
    service = CodexServiceThread()
    results: list[ServiceResult] = []
    service.completed.connect(results.append)
    service.start()
    service.shutdown()
    qtbot.waitUntil(lambda: service.wait(0))
    service.answer("late", decision="accept")
    assert len(results) == 1
    assert results[0].answer == AnswerResult("late", "not_sent")


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


def test_shutdown_still_closes_when_stop_fails(qtbot: QtBot, tmp_path: Path) -> None:
    class FailingStop(ControlledConnection):
        """Fail interruption without changing close cleanup."""

        async def stop(self) -> None:
            """Inject an unexpected operation failure."""
            message = "secret stop error"
            raise OSError(message)

    connections: list[FailingStop] = []

    def factory(
        _options: ConnectionOptions, emit: Callable[[ConnectionEvent], None]
    ) -> FailingStop:
        connection = FailingStop(emit)
        connections.append(connection)
        return connection

    service = CodexServiceThread(factory)
    service.start()
    service.connect_runtime(ConnectionOptions(tmp_path))
    qtbot.waitUntil(lambda: bool(connections) and connections[0].opened.is_set())
    service.shutdown()
    qtbot.waitUntil(lambda: service.wait(0))
    assert connections[0].closed.is_set()
    assert not service.shutdown_error
