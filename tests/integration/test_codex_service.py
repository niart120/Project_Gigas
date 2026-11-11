import asyncio
import base64
import json
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import httpx
import pytest
from PySide6.QtCore import QThread, QTimer
from pytestqt.qtbot import QtBot

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.mcp.observation import ObservationMcpServer
from gigas.runtime.codex.connection import (
    AnswerDeliveryError,
    CodexConnection,
    CodexConnectionError,
)
from gigas.runtime.codex.models import ConnectionEvent, ConnectionOptions, SessionStatus
from gigas.services.codex import AnswerResult, CodexServiceThread
from gigas.storage import project
from gigas.storage.project import ProjectWriter
from tests.integration.test_observation_storage import sample_frame
from tests.unit.test_codex_connection import FakePeer

if TYPE_CHECKING:
    from gigas.runtime.codex.models import ThreadConfiguration
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


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("close_failure", [False, True])
def test_chat_archive_mcp_settings_and_shutdown_share_one_lifetime(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selected: bool,
    close_failure: bool,
) -> None:
    peers: list[FakePeer] = []
    results: list[ServiceResult] = []
    projects = []
    entered, release = Event(), Event()
    http_closed = Event()
    publish = project._publish_image
    close = ObservationMcpServer.close

    async def fail_after_http_close(server: ObservationMcpServer) -> None:
        await close(server)
        http_closed.set()
        message = "HTTP shutdown failed after closing its listener"
        raise RuntimeError(message)

    def hold_image(path: Path, data: bytes) -> None:
        entered.set()
        if not release.wait(15):
            raise TimeoutError
        publish(path, data)

    def factory(
        options: ConnectionOptions,
        emit: Callable[[ConnectionEvent], None],
        prepare: Callable[[], Awaitable["ThreadConfiguration"]],
        started: Callable[[str], Awaitable[None]],
    ) -> CodexConnection:
        peer = FakePeer()
        peers.append(peer)
        return CodexConnection(
            peer, options, emit, prepare_thread=prepare, on_thread_started=started
        )

    frame = sample_frame(2048, 1536)
    service = CodexServiceThread(
        tmp_path, CaptureReader(lambda: CaptureSnapshot(phase="streaming", frame=frame)), factory
    )
    service.completed.connect(results.append)
    service.project_completed.connect(projects.append)
    service.start()
    try:
        if selected:
            service.create_project(tmp_path / "selected")
            qtbot.waitUntil(lambda: bool(projects))
            assert not projects[-1].error
            # Failed switching preserves the current archive and its OS ownership.
            service.open_project(tmp_path / "missing")
            qtbot.waitUntil(lambda: len(projects) == 2)
            assert projects[-1].error
        service.connect_runtime(ConnectionOptions(tmp_path / "runtime"))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        assert not (tmp_path / "chats").exists()
        service.send("observe")
        qtbot.waitUntil(lambda: any(result.operation == "send" for result in results))
        assert not results[-1].error
        thread = next(params for method, params in peers[0].calls if method == "thread/start")
        assert thread is not None
        archive = Path(str(thread["cwd"]))
        assert archive.parent == (tmp_path if selected else tmp_path / "chats")
        if selected:
            assert archive.name == "selected"
        rows = [json.loads(line) for line in (archive / "events.jsonl").read_text().splitlines()]
        assert rows[0]["type"] == "thread.started"
        assert rows[0]["payload"]["thread_id"] == "thread-1"
        config = thread["config"]
        assert isinstance(config, dict)
        url = next(value for key, value in config.items() if key.endswith(".url"))
        headers = next(value for key, value in config.items() if key.endswith(".http_headers"))
        assert isinstance(url, str)
        assert isinstance(headers, dict)
        service.set_observation_resolution("hd")
        # A later loop command completes only after the setting command was handled.
        service.interrupt()
        qtbot.waitUntil(lambda: any(result.operation == "stop" for result in results))
        monkeypatch.setattr(project, "_publish_image", hold_image)
        with httpx.Client(
            headers={
                **{key: str(value) for key, value in headers.items()},
                "Accept": "application/json, text/event-stream",
            },
            trust_env=False,
            timeout=5,
        ) as client:
            response = client.post(
                url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "observe", "arguments": {}},
                },
            )
        result = response.json()["result"]
        assert not result["isError"]
        metadata = json.loads(result["content"][1]["text"])
        assert metadata["image"] == {"mime_type": "image/png", "width": 960, "height": 720}
        qtbot.waitUntil(entered.is_set)
        if close_failure:
            monkeypatch.setattr(ObservationMcpServer, "close", fail_after_http_close)
        service.shutdown()
        pulse = Event()
        QTimer.singleShot(0, pulse.set)
        qtbot.waitUntil(pulse.is_set)
        if close_failure:
            qtbot.waitUntil(http_closed.is_set)
        assert not service.wait(0)
        release.set()
        qtbot.waitUntil(lambda: service.wait(0))
        rows = [json.loads(line) for line in (archive / "events.jsonl").read_text().splitlines()]
        assert rows[-1]["payload"]["thread_id"] == "thread-1"
        assert rows[-1]["payload"]["observation_id"] == metadata["observation_id"]
        image = archive / rows[-1]["payload"]["image_path"]
        assert image.read_bytes() == base64.b64decode(result["content"][0]["data"])
        ProjectWriter.open(archive).close()
        assert bool(service.shutdown_error) == close_failure
    finally:
        release.set()
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0), timeout=10000)


def test_runtime_failure_revokes_old_mcp_endpoint(qtbot: QtBot, tmp_path: Path) -> None:
    peer = FakePeer()
    peer.failure = "turn/start"
    results: list[ServiceResult] = []

    def factory(
        options: ConnectionOptions,
        emit: Callable[[ConnectionEvent], None],
        prepare: Callable[[], Awaitable["ThreadConfiguration"]],
        started: Callable[[str], Awaitable[None]],
    ) -> CodexConnection:
        return CodexConnection(
            peer, options, emit, prepare_thread=prepare, on_thread_started=started
        )

    frame = sample_frame(64, 48)
    service = CodexServiceThread(
        tmp_path, CaptureReader(lambda: CaptureSnapshot(phase="streaming", frame=frame)), factory
    )
    service.completed.connect(results.append)
    service.start()
    try:
        service.connect_runtime(ConnectionOptions(tmp_path / "runtime"))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        service.send("observe")
        qtbot.waitUntil(
            lambda: any(result.operation == "send" and result.error for result in results)
        )
        assert peer.closed
        thread = next(params for method, params in peer.calls if method == "thread/start")
        assert thread is not None
        config = thread["config"]
        assert isinstance(config, dict)
        url = next(value for key, value in config.items() if key.endswith(".url"))
        headers = next(value for key, value in config.items() if key.endswith(".http_headers"))
        assert isinstance(url, str)
        assert isinstance(headers, dict)
        with httpx.Client(trust_env=False, timeout=1) as client:
            try:
                response = client.post(
                    url,
                    headers={
                        **{key: str(value) for key, value in headers.items()},
                        "Accept": "application/json, text/event-stream",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "observe", "arguments": {}},
                    },
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError):
                response = None
        assert response is None or response.status_code in {401, 503}
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0), timeout=10000)


def test_disconnect_during_archive_creation_recovers_writer_without_starting_turn(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    peers: list[FakePeer] = []
    results: list[ServiceResult] = []
    create = ProjectWriter.create

    def delayed_create(_cls: type[ProjectWriter], path: Path) -> ProjectWriter:
        entered.set()
        if not release.wait(10):
            raise TimeoutError
        return create(path)

    def factory(
        options: ConnectionOptions,
        emit: Callable[[ConnectionEvent], None],
        prepare: Callable[[], Awaitable["ThreadConfiguration"]],
        started: Callable[[str], Awaitable[None]],
    ) -> CodexConnection:
        peer = FakePeer()
        peers.append(peer)
        return CodexConnection(
            peer, options, emit, prepare_thread=prepare, on_thread_started=started
        )

    monkeypatch.setattr(ProjectWriter, "create", classmethod(delayed_create))
    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot), factory)
    service.completed.connect(results.append)
    service.start()
    try:
        service.connect_runtime(ConnectionOptions(tmp_path / "runtime"))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        service.send("observe")
        qtbot.waitUntil(entered.is_set)
        service.disconnect_runtime()
        release.set()
        qtbot.waitUntil(lambda: any(result.operation == "disconnect" for result in results))
        assert not any(method == "turn/start" for method, _ in peers[0].calls)
        archives = list((tmp_path / "chats").iterdir())
        assert len(archives) == 1
        ProjectWriter.open(archives[0]).close()
    finally:
        release.set()
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0), timeout=10000)


@pytest.mark.parametrize("delivery", [False, True])
def test_failure_id_is_shared_by_worker_status_and_operation_result(
    qtbot: QtBot, tmp_path: Path, delivery: bool
) -> None:
    class FailedAnswerConnection(ControlledConnection):
        """Publish one failure before returning its associated operation error."""

        async def answer(
            self,
            token: str,
            *,
            decision: str | None = None,
            answers: Mapping[str, str] | None = None,
        ) -> None:
            """Attach correlation without exposing any transport implementation."""
            assert (token, decision, answers) == ("token", "accept", None)
            message = "回答失敗"
            self.emit(SessionStatus("error", detail=message, failure_id=17))
            error_type = AnswerDeliveryError if delivery else CodexConnectionError
            raise error_type(message, failure_id=17)

    events: list[ServiceEvent] = []
    results: list[ServiceResult] = []
    service = CodexServiceThread(
        tmp_path,
        CaptureReader(CaptureSnapshot),
        lambda _options, emit, *_callbacks: FailedAnswerConnection(emit),
    )
    service.received.connect(events.append)
    service.completed.connect(results.append)
    service.start()
    try:
        generation = service.connect_runtime(ConnectionOptions(tmp_path))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        service.answer("token", decision="accept")
        qtbot.waitUntil(lambda: any(result.operation == "answer" for result in results))
        result = next(result for result in results if result.operation == "answer")
        status = events[-1].event
        assert isinstance(status, SessionStatus)
        assert status.failure_id == result.failure_id == 17
        assert result.generation == events[-1].generation == generation
        assert result.answer == AnswerResult("token", "unknown" if delivery else "not_sent")
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))


@pytest.mark.parametrize("correlated", [False, True])
def test_shutdown_stop_failure_preserves_correlation_and_still_closes(
    qtbot: QtBot, tmp_path: Path, correlated: bool
) -> None:
    class FailedStopConnection(ControlledConnection):
        """Keep cleanup observable when the shutdown interrupt fails."""

        async def stop(self) -> None:
            """Model a known failure and an unexpected exception independently."""
            if correlated:
                message = "中断結果を確認できない"
                self.emit(SessionStatus("error", detail=message, failure_id=23))
                raise CodexConnectionError(message, failure_id=23)
            message = "PRIVATE-SHUTDOWN-ERROR"
            raise OSError(message)

    connections: list[FailedStopConnection] = []
    events: list[ServiceEvent] = []
    results: list[ServiceResult] = []

    def factory(
        _options: ConnectionOptions, emit: Callable[[ConnectionEvent], None], *_callbacks: object
    ) -> FailedStopConnection:
        connection = FailedStopConnection(emit)
        connections.append(connection)
        return connection

    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot), factory)
    service.received.connect(events.append)
    service.completed.connect(results.append)
    service.start()
    try:
        generation = service.connect_runtime(ConnectionOptions(tmp_path))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))
    qtbot.waitUntil(lambda: any(result.operation == "stop" for result in results))
    stopped = [result for result in results if result.operation == "stop"]
    assert len(stopped) == 1
    assert stopped[0].generation == generation
    assert stopped[0].failure_id == (23 if correlated else None)
    assert stopped[0].error
    assert "PRIVATE" not in stopped[0].error
    assert connections[0].closed.is_set()
    if correlated:
        status = events[-1].event
        assert isinstance(status, SessionStatus)
        assert status.failure_id == stopped[0].failure_id


def test_answer_without_connection_reports_not_sent(qtbot: QtBot, tmp_path: Path) -> None:
    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot))
    results: list[ServiceResult] = []
    service.completed.connect(results.append)
    service.start()
    try:
        service.answer("missing", decision="accept")
        qtbot.waitUntil(lambda: bool(results))
        result = results[0]
        assert result.error
        assert result.answer == AnswerResult("missing", "not_sent")
        assert result.failure_id is None
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))


def test_answer_discarded_before_start_reports_not_sent(qtbot: QtBot, tmp_path: Path) -> None:
    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot))
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
        _options: ConnectionOptions, emit: Callable[[ConnectionEvent], None], *_callbacks: object
    ) -> WaitingAnswerConnection:
        connection = WaitingAnswerConnection(emit)
        connections.append(connection)
        return connection

    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot), factory)
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


def test_answer_cancelled_before_execution_reports_not_sent(qtbot: QtBot, tmp_path: Path) -> None:
    class CancelBeforeRunService(CodexServiceThread):
        """Inject cancellation after task creation but before its first execution."""

        def _dispatch(self, command: "_Command") -> None:
            super()._dispatch(command)
            for task in self._tasks:
                task.cancel()

    service = CancelBeforeRunService(tmp_path, CaptureReader(CaptureSnapshot))
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
    service = CodexServiceThread(
        tmp_path,
        CaptureReader(CaptureSnapshot),
        lambda _options, emit, *_callbacks: AnswerConnection(emit),
    )
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
    service = CodexServiceThread(
        tmp_path,
        CaptureReader(CaptureSnapshot),
        lambda _options, emit, *_callbacks: ControlledConnection(emit),
    )
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


def test_answer_after_service_shutdown_is_reported_not_sent(qtbot: QtBot, tmp_path: Path) -> None:
    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot))
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
        options: ConnectionOptions, emit: Callable[[ConnectionEvent], None], *_callbacks: object
    ) -> ControlledConnection:
        assert options.cwd.is_dir()
        connection = ControlledConnection(emit)
        connections.append(connection)
        return connection

    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot), factory)
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
        _options: ConnectionOptions, _emit: Callable[[ConnectionEvent], None], *_callbacks: object
    ) -> ControlledConnection:
        called.set()
        message = "private credential"
        raise OSError(message)

    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot), failed_factory)
    results: list[ServiceResult] = []
    service.completed.connect(results.append)
    service.connect_runtime(ConnectionOptions(tmp_path))
    service.shutdown()
    service.start()
    qtbot.waitUntil(lambda: service.wait(0))
    assert not called.is_set()

    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot), failed_factory)
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
        _options: ConnectionOptions, emit: Callable[[ConnectionEvent], None], *_callbacks: object
    ) -> FailingStop:
        connection = FailingStop(emit)
        connections.append(connection)
        return connection

    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot), factory)
    service.start()
    service.connect_runtime(ConnectionOptions(tmp_path))
    qtbot.waitUntil(lambda: bool(connections) and connections[0].opened.is_set())
    service.shutdown()
    qtbot.waitUntil(lambda: service.wait(0))
    assert connections[0].closed.is_set()
    assert not service.shutdown_error
