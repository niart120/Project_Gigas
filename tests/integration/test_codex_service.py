import asyncio
import base64
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import httpx
import pytest
from PySide6.QtCore import QThread, QTimer
from pytestqt.qtbot import QtBot

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.game_io.input.config import resolve_serial_config
from gigas.game_io.input.models import InputState
from gigas.game_io.observation import Observation
from gigas.mcp.game_io import GameIoMcpServer
from gigas.runtime.codex.connection import (
    CodexConnection,
    CodexConnectionError,
    RuntimeEvent,
)
from gigas.runtime.codex.models import (
    ConnectionOptions,
    ConversationStatus,
    GoalEvent,
    HumanRequest,
    SandboxEvent,
)
from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcNotification
from gigas.services.codex import AnswerResult, CodexServiceThread, MessageResult
from gigas.storage import project
from gigas.storage.project import ProjectWriter
from tests.integration.test_input_service import Device
from tests.integration.test_observation_storage import sample_frame
from tests.unit.test_codex_connection import FakePeer, _question, _terminal

if TYPE_CHECKING:
    from gigas.game_io.capture.models import Frame
    from gigas.game_io.observation import ObservationResolution
    from gigas.services.codex import (
        InputConnectionResult,
        ServiceEvent,
        ServiceResult,
        _OperationCall,
        _OperationContext,
    )


class ControlledConnection(CodexConnection):
    """A real connection with worker-lifetime observations and a scripted peer."""

    def __init__(self, emit: Callable[[RuntimeEvent], None]) -> None:
        """Capture worker identity and use a scripted transport."""
        self.peer = FakePeer()
        super().__init__(self.peer, ConnectionOptions(Path.cwd()), emit)
        self.emit = emit
        self.thread = QThread.currentThread()
        self.opened = Event()
        self.closed = Event()

    async def open(self) -> None:
        """Publish actual connection facts before exposing the open observation."""
        await super().open()
        self.opened.set()

    async def close(self) -> None:
        """Observe physical cleanup after the transport finishes."""
        await super().close()
        self.closed.set()


def _mcp_endpoint(params: JsonObject) -> tuple[str, dict[str, str]]:
    config = params["config"]
    assert isinstance(config, dict)
    url = next(value for key, value in config.items() if key.endswith(".url"))
    headers = next(value for key, value in config.items() if key.endswith(".http_headers"))
    assert isinstance(url, str)
    assert isinstance(headers, dict)
    result: dict[str, str] = {"Accept": "application/json, text/event-stream"}
    for key, value in headers.items():
        assert isinstance(value, str)
        result[key] = value
    return url, result


def _call_game_tool(
    client: httpx.Client, url: str, name: str, arguments: JsonObject | None = None
) -> httpx.Response:
    response = client.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
    )
    response.raise_for_status()
    return response


@pytest.mark.parametrize("outcome", ["success", "failure", "disconnect"])
def test_sandbox_setup_awaits_completion_without_blocking_disconnect(
    qtbot: QtBot, tmp_path: Path, outcome: str
) -> None:
    entered, finish = Event(), Event()
    connections: list[ControlledConnection] = []
    results: list[ServiceResult] = []
    events: list[ServiceEvent] = []

    class SetupConnection(ControlledConnection):
        async def setup_windows_sandbox(self) -> None:
            entered.set()
            self.emit(SandboxEvent("notConfigured", True))
            assert await asyncio.to_thread(finish.wait, 5)
            if self.closed.is_set() or outcome == "failure":
                raise CodexConnectionError("setup-failed-fixture")
            self.emit(SandboxEvent("ready"))

        async def close(self) -> None:
            await super().close()
            finish.set()

    def factory(
        _options: ConnectionOptions,
        emit: Callable[[RuntimeEvent], None],
    ) -> SetupConnection:
        connection = SetupConnection(emit)
        connections.append(connection)
        return connection

    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot), factory)
    service.completed.connect(results.append)
    service.received.connect(events.append)
    service.start()
    try:
        service.connect_runtime(ConnectionOptions(tmp_path))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        service.setup_windows_sandbox()
        qtbot.waitUntil(entered.is_set)
        qtbot.waitUntil(lambda: any(isinstance(event.event, SandboxEvent) for event in events))
        assert not any(result.operation == "setup_windows_sandbox" for result in results)
        if outcome == "disconnect":
            service.disconnect_runtime()
            qtbot.waitUntil(lambda: any(result.operation == "disconnect" for result in results))
            assert connections[0].closed.is_set()
        else:
            finish.set()
            qtbot.waitUntil(
                lambda: any(result.operation == "setup_windows_sandbox" for result in results)
            )
            result = next(item for item in results if item.operation == "setup_windows_sandbox")
            assert bool(result.error) == (outcome == "failure")
    finally:
        finish.set()
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))


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
    close = GameIoMcpServer.close

    async def fail_after_http_close(server: GameIoMcpServer) -> None:
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
        emit: Callable[[RuntimeEvent], None],
    ) -> CodexConnection:
        peer = FakePeer()
        peers.append(peer)
        return CodexConnection(peer, options, emit)

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
        archive = Path(str(thread["cwd"])).parent
        assert archive.parent == (tmp_path if selected else tmp_path / "chats")
        if selected:
            assert archive.name == "selected"
        rows = [
            json.loads(line)
            for line in (archive / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
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
        setting_applied = Event()
        service._enqueue(setting_applied.set)
        qtbot.waitUntil(setting_applied.is_set)
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
            monkeypatch.setattr(GameIoMcpServer, "close", fail_after_http_close)
        service.shutdown()
        pulse = Event()
        QTimer.singleShot(0, pulse.set)
        qtbot.waitUntil(pulse.is_set)
        if close_failure:
            qtbot.waitUntil(http_closed.is_set)
        assert not service.wait(0)
        release.set()
        qtbot.waitUntil(lambda: service.wait(0))
        rows = [
            json.loads(line)
            for line in (archive / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        observed = next(row["payload"] for row in rows if row["type"] == "observation.recorded")
        assert observed["thread_id"] == "thread-1"
        assert observed["observation_id"] == metadata["observation_id"]
        image = archive / observed["image_path"]
        assert image.read_bytes() == base64.b64decode(result["content"][0]["data"])
        ProjectWriter.open(archive).close()
        assert bool(service.shutdown_error) == close_failure
    finally:
        release.set()
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0), timeout=10000)


@pytest.mark.parametrize("selected", [False, True])
def test_disconnect_drains_old_results_before_starting_next_conversation(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: bool
) -> None:
    entered, release = Event(), Event()
    encode = Observation.from_frame
    peers: list[FakePeer] = []
    results: list[ServiceResult] = []
    projects = []
    inputs: list[InputConnectionResult] = []
    device = Device()

    def delayed_observation(
        _cls: type[Observation], frame: "Frame", *, resolution: "ObservationResolution" = "full_hd"
    ) -> Observation:
        entered.set()
        if not release.wait(10):
            raise TimeoutError
        return encode(frame, resolution=resolution)

    class ArchivePeer(FakePeer):
        def __init__(self, thread_id: str) -> None:
            super().__init__()
            self.thread_id = thread_id
            self.auto_turn_started = False

        async def request(self, method: str, params: JsonObject | None) -> JsonValue:
            result = await super().request(method, params)
            if method == "thread/start":
                assert isinstance(result, dict)
                result["thread"] = {"id": self.thread_id}
            elif method == "turn/start":
                self.events.put_nowait(
                    RpcNotification(
                        "turn/started",
                        {"threadId": self.thread_id, "turn": {"id": "turn-1"}},
                    )
                )
            return result

    def factory(
        options: ConnectionOptions, emit: Callable[[RuntimeEvent], None]
    ) -> CodexConnection:
        peer = ArchivePeer(f"thread-{len(peers) + 1}")
        peers.append(peer)
        return CodexConnection(peer, options, emit)

    def records(archive: Path) -> list[JsonObject]:
        return [json.loads(line) for line in (archive / "events.jsonl").read_bytes().splitlines()]

    frame = sample_frame(64, 48)
    service = CodexServiceThread(
        tmp_path,
        CaptureReader(lambda: CaptureSnapshot(phase="streaming", frame=frame)),
        factory,
        input_factory=lambda _config: device,
    )
    service.completed.connect(results.append)
    service.project_completed.connect(projects.append)
    service.input_changed.connect(inputs.append)
    service.start()
    try:
        if selected:
            service.create_project(tmp_path / "selected")
            qtbot.waitUntil(lambda: bool(projects))
            assert not projects[-1].error
        service.apply_input(resolve_serial_config("COM3", "ch552", None))
        qtbot.waitUntil(lambda: bool(inputs))
        assert inputs[-1].connected
        service.connect_runtime(ConnectionOptions(tmp_path / "runtime"))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        service.send("old conversation")
        qtbot.waitUntil(lambda: any(result.operation == "send" for result in results))
        old_thread = next(params for method, params in peers[0].calls if method == "thread/start")
        assert old_thread is not None
        old_archive = Path(str(old_thread["cwd"])).parent
        old_url, old_headers = _mcp_endpoint(old_thread)
        monkeypatch.setattr(Observation, "from_frame", classmethod(delayed_observation))
        with (
            httpx.Client(headers=old_headers, trust_env=False, timeout=10) as client,
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            try:
                pending = pool.submit(
                    _call_game_tool,
                    client,
                    old_url,
                    "act_and_observe",
                    {"sequence": [{"kind": "press", "buttons": ["A"], "duration_ms": 1}]},
                )
                qtbot.waitUntil(entered.is_set)
                service.disconnect_runtime()
                qtbot.waitUntil(lambda: peers[0].closed)
                assert not pending.done()
                assert not any(result.operation == "disconnect" for result in results)
                assert not any(
                    row["type"] in {"observation.recorded", "input.operation_finished"}
                    for row in records(old_archive)
                )
                release.set()
                qtbot.waitUntil(pending.done)
                old_result = pending.result().json()["result"]
            finally:
                release.set()
        assert not old_result["isError"]
        old_metadata = json.loads(old_result["content"][1]["text"])
        qtbot.waitUntil(lambda: any(result.operation == "disconnect" for result in results))
        assert not next(result.error for result in results if result.operation == "disconnect")
        old_rows = records(old_archive)
        old_observation = next(
            row["payload"] for row in old_rows if row["type"] == "observation.recorded"
        )
        old_started = next(
            row["payload"] for row in old_rows if row["type"] == "input.operation_started"
        )
        old_finished = next(
            row["payload"] for row in old_rows if row["type"] == "input.operation_finished"
        )
        assert isinstance(old_observation, dict)
        assert isinstance(old_started, dict)
        assert isinstance(old_finished, dict)
        assert old_observation["thread_id"] == old_started["thread_id"] == "thread-1"
        assert (
            old_finished["operation_id"]
            == old_started["operation_id"]
            == old_metadata["operation_id"]
        )
        assert (
            old_finished["observation_id"]
            == old_observation["observation_id"]
            == old_metadata["observation_id"]
        )
        old_image = old_archive / str(old_observation["image_path"])
        assert old_image.read_bytes() == base64.b64decode(old_result["content"][0]["data"])
        old_log = (old_archive / "events.jsonl").read_bytes()
        if selected:
            with pytest.raises(project.ProjectError):
                ProjectWriter.open(old_archive)
        else:
            ProjectWriter.open(old_archive).close()

        service.connect_runtime(ConnectionOptions(tmp_path / "runtime"))
        qtbot.waitUntil(lambda: sum(result.operation == "connect" for result in results) == 2)
        service.send("new conversation")
        qtbot.waitUntil(lambda: sum(result.operation == "send" for result in results) == 2)
        new_thread = next(params for method, params in peers[1].calls if method == "thread/start")
        assert new_thread is not None
        new_archive = Path(str(new_thread["cwd"])).parent
        assert (new_archive == old_archive) == selected
        new_url, new_headers = _mcp_endpoint(new_thread)
        with httpx.Client(headers=new_headers, trust_env=False, timeout=5) as client:
            new_result = _call_game_tool(
                client,
                new_url,
                "act_and_observe",
                {"sequence": [{"kind": "press", "buttons": ["B"], "duration_ms": 1}]},
            ).json()["result"]
        assert not new_result["isError"]
        new_metadata = json.loads(new_result["content"][1]["text"])
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))
        new_rows = records(new_archive)
        if selected:
            assert new_rows[: len(old_rows)] == old_rows
            new_rows = new_rows[len(old_rows) :]
        else:
            assert (old_archive / "events.jsonl").read_bytes() == old_log
        assert old_metadata["operation_id"] not in json.dumps(new_rows)
        assert old_metadata["observation_id"] not in json.dumps(new_rows)
        new_observation = next(
            row["payload"] for row in new_rows if row["type"] == "observation.recorded"
        )
        new_started = next(
            row["payload"] for row in new_rows if row["type"] == "input.operation_started"
        )
        new_finished = next(
            row["payload"] for row in new_rows if row["type"] == "input.operation_finished"
        )
        assert isinstance(new_observation, dict)
        assert isinstance(new_started, dict)
        assert isinstance(new_finished, dict)
        assert new_observation["thread_id"] == new_started["thread_id"] == "thread-2"
        assert (
            new_finished["operation_id"]
            == new_started["operation_id"]
            == new_metadata["operation_id"]
        )
        assert (
            new_finished["observation_id"]
            == new_observation["observation_id"]
            == new_metadata["observation_id"]
        )
        assert (new_archive / str(new_observation["image_path"])).read_bytes() == base64.b64decode(
            new_result["content"][0]["data"]
        )
        ProjectWriter.open(new_archive).close()
        assert not service.shutdown_error
    finally:
        release.set()
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
        emit: Callable[[RuntimeEvent], None],
    ) -> CodexConnection:
        peer = FakePeer()
        peers.append(peer)
        return CodexConnection(peer, options, emit)

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


def test_shutdown_closes_without_submitting_an_additional_stop(
    qtbot: QtBot, tmp_path: Path
) -> None:
    connections: list[ControlledConnection] = []
    results: list[ServiceResult] = []

    def factory(
        _options: ConnectionOptions, emit: Callable[[RuntimeEvent], None]
    ) -> ControlledConnection:
        connection = ControlledConnection(emit)
        connections.append(connection)
        return connection

    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot), factory)
    service.completed.connect(results.append)
    service.start()
    try:
        service.connect_runtime(ConnectionOptions(tmp_path))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))
    assert connections[0].closed.is_set()
    assert not any(method == "turn/interrupt" for method, _ in connections[0].peer.calls)
    assert not any(result.operation == "stop" for result in results)


def _submit_delivery(service: CodexServiceThread, operation: str, text: str) -> None:
    if operation == "answer":
        service.answer(text, decision="accept")
    else:
        service.send(text)


def _not_sent(result: "ServiceResult", token: str) -> bool:
    return (
        result.answer == AnswerResult(token, "not_sent")
        if result.operation == "answer"
        else result.message == MessageResult("not_sent")
    )


@pytest.mark.parametrize("operation", ["answer", "send"])
def test_submission_without_connection_reports_not_sent(
    qtbot: QtBot, tmp_path: Path, operation: str
) -> None:
    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot))
    results: list[ServiceResult] = []
    service.completed.connect(results.append)
    service.start()
    try:
        _submit_delivery(service, operation, "missing")
        qtbot.waitUntil(lambda: bool(results))
        result = results[0]
        assert result.error
        assert _not_sent(result, "missing")
        assert result.failure_id is None
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))


@pytest.mark.parametrize("operation", ["answer", "send"])
def test_submission_discarded_before_start_reports_not_sent(
    qtbot: QtBot, tmp_path: Path, operation: str
) -> None:
    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot))
    results: list[ServiceResult] = []
    service.completed.connect(results.append)
    _submit_delivery(service, operation, "queued")
    service.shutdown()
    service.start()
    qtbot.waitUntil(lambda: service.wait(0))
    assert len(results) == 1
    result = results[0]
    assert _not_sent(result, "queued")


@pytest.mark.parametrize("operation", ["answer", "send"])
def test_submission_cancelled_before_execution_reports_not_sent(
    qtbot: QtBot, tmp_path: Path, operation: str
) -> None:
    class CancelBeforeRunService(CodexServiceThread):
        """Inject cancellation after task creation but before its first execution."""

        def _start_operation(self, context: "_OperationContext", call: "_OperationCall") -> None:
            super()._start_operation(context, call)
            for task in self._tasks:
                task.cancel()

    service = CancelBeforeRunService(tmp_path, CaptureReader(CaptureSnapshot))
    results: list[ServiceResult] = []
    service.completed.connect(results.append)
    service.start()
    try:
        _submit_delivery(service, operation, "before-run")
        qtbot.waitUntil(lambda: bool(results))
        assert len(results) == 1
        assert _not_sent(results[0], "before-run")
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))


@pytest.mark.parametrize("operation", ["answer", "send"])
def test_submission_after_service_shutdown_is_reported_not_sent(
    qtbot: QtBot, tmp_path: Path, operation: str
) -> None:
    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot))
    results: list[ServiceResult] = []
    service.completed.connect(results.append)
    service.start()
    service.shutdown()
    qtbot.waitUntil(lambda: service.wait(0))
    _submit_delivery(service, operation, "late")
    assert len(results) == 1
    assert _not_sent(results[0], "late")


def test_shutdown_before_start_and_factory_failure_leave_no_worker(
    qtbot: QtBot, tmp_path: Path
) -> None:
    called = Event()

    def failed_factory(
        _options: ConnectionOptions, _emit: Callable[[RuntimeEvent], None]
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


def test_shutdown_still_closes_when_input_close_fails(qtbot: QtBot, tmp_path: Path) -> None:
    connections: list[ControlledConnection] = []

    def factory(
        _options: ConnectionOptions, emit: Callable[[RuntimeEvent], None]
    ) -> ControlledConnection:
        connection = ControlledConnection(emit)
        connections.append(connection)
        return connection

    device = Device()
    device.fail_close = True
    service = CodexServiceThread(
        tmp_path, CaptureReader(CaptureSnapshot), factory, input_factory=lambda _config: device
    )
    applied = []
    service.input_changed.connect(applied.append)
    service.start()
    service.connect_runtime(ConnectionOptions(tmp_path))
    qtbot.waitUntil(lambda: bool(connections) and connections[0].opened.is_set())
    service.apply_input(resolve_serial_config("COM3", "ch552", None))
    qtbot.waitUntil(lambda: bool(applied))
    service.shutdown()
    qtbot.waitUntil(lambda: service.wait(0))
    assert connections[0].closed.is_set()
    assert not any(method == "turn/interrupt" for method, _ in connections[0].peer.calls)
    assert service.shutdown_error


def test_input_settings_apply_without_codex_or_chat_and_close_on_shutdown(
    qtbot: QtBot, tmp_path: Path
) -> None:

    devices: list[Device] = []
    applied = []

    def device_factory(_config: object) -> Device:
        device = Device()
        devices.append(device)
        return device

    service = CodexServiceThread(
        tmp_path, CaptureReader(CaptureSnapshot), input_factory=device_factory
    )
    service.input_changed.connect(applied.append)
    config = resolve_serial_config("COM3", "ch552", None)
    service.start()
    try:
        service.apply_input(config)
        qtbot.waitUntil(lambda: len(applied) == 1)
        assert applied[-1].connected
        assert not (tmp_path / "chats").exists()
        service.apply_input(config)
        qtbot.waitUntil(lambda: len(applied) == 2)
        assert len(devices) == 1
        service.interrupt()
        service.apply_input(None)
        qtbot.waitUntil(lambda: len(applied) == 3)
        assert not applied[-1].connected
        assert devices[0].calls == ["open", "close"]
        service.apply_input(config)
        qtbot.waitUntil(lambda: len(applied) == 4)
        assert len(devices) == 2
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))
    assert devices[1].calls == ["open", "close"]
    assert service.shutdown_error == ""


def test_offline_apply_retries_close_after_initial_open_cleanup_fails(
    qtbot: QtBot, tmp_path: Path
) -> None:
    class FailedOpen(Device):
        async def open(self) -> None:
            await super().open()
            message = "initial neutral failed"
            raise OSError(message)

    device = FailedOpen()
    device.fail_close = True
    service = CodexServiceThread(
        tmp_path, CaptureReader(CaptureSnapshot), input_factory=lambda _config: device
    )
    applied = []
    service.input_changed.connect(applied.append)
    service.start()
    try:
        service.apply_input(resolve_serial_config("COM3", "ch552", None))
        qtbot.waitUntil(lambda: len(applied) == 1)
        assert applied[-1].error
        assert not applied[-1].connected
        assert device.calls == ["open", "close"]

        device.fail_close = False
        service.apply_input(None)
        qtbot.waitUntil(lambda: len(applied) == 2)

        assert not applied[-1].error
        assert not applied[-1].connected
        assert device.calls == ["open", "close", "close"]
    finally:
        device.fail_close = False
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))
    assert service.shutdown_error == ""


@pytest.mark.parametrize("stop", [False, True])
def test_input_switch_keeps_latest_target_even_after_stop(
    qtbot: QtBot, tmp_path: Path, *, stop: bool
) -> None:
    entered, release = Event(), Event()
    devices: list[Device] = []
    applied = []

    class SlowDevice(Device):
        async def open(self) -> None:
            entered.set()
            if not await asyncio.to_thread(release.wait, 5):
                raise TimeoutError
            await super().open()

    def factory(_config: object) -> Device:
        device = SlowDevice() if not devices else Device()
        devices.append(device)
        return device

    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot), input_factory=factory)
    service.input_changed.connect(applied.append)
    service.start()
    try:
        service.apply_input(resolve_serial_config("COM1", "ch552", None))
        qtbot.waitUntil(entered.is_set)
        service.apply_input(resolve_serial_config("COM2", "ch552", None))
        service.apply_input(resolve_serial_config("COM3", "ch552", None))
        if stop:
            service.interrupt()
        release.set()
        qtbot.waitUntil(lambda: len(applied) == 2)
        assert devices[0].calls == ["open", "close"]
        assert len(devices) == 2
        assert applied[-1].config.port == "COM3"
        assert applied[-1].connected
    finally:
        release.set()
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))
    assert service.shutdown_error == ""


def test_thread_save_failure_does_not_prevent_send(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_writer: ProjectWriter, thread_id: str) -> None:
        raise OSError(thread_id)

    monkeypatch.setattr(ProjectWriter, "record_thread", fail)
    results: list[ServiceResult] = []
    failures: list[str] = []

    def factory(
        options: ConnectionOptions,
        emit: Callable[[RuntimeEvent], None],
    ) -> CodexConnection:
        return CodexConnection(FakePeer(), options, emit)

    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot), factory)
    service.completed.connect(results.append)
    service.recording_failed.connect(lambda _reference, _path, reason: failures.append(reason))
    service.start()
    try:
        service.connect_runtime(ConnectionOptions(tmp_path / "runtime"))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        service.send("input")
        qtbot.waitUntil(lambda: any(result.operation == "send" for result in results))
        assert not results[-1].error
        qtbot.waitUntil(lambda: bool(failures))
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))
    archive = next((tmp_path / "chats").iterdir())
    rows = [
        json.loads(line)
        for line in (archive / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        row["type"] == "dialogue.delivery" and row["payload"]["thread_id"] == "thread-1"
        for row in rows
    )


def test_service_selects_model_without_interrupting_turn(qtbot: QtBot, tmp_path: Path) -> None:
    peers: list[FakePeer] = []
    results: list[ServiceResult] = []

    def factory(
        options: ConnectionOptions,
        emit: Callable[[RuntimeEvent], None],
    ) -> CodexConnection:
        peer = FakePeer()
        peers.append(peer)
        return CodexConnection(peer, options, emit)

    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot), factory)
    service.completed.connect(results.append)
    service.start()
    try:
        service.connect_runtime(ConnectionOptions(tmp_path))
        qtbot.waitUntil(lambda: any(r.operation == "connect" for r in results))
        service.send("first")
        qtbot.waitUntil(lambda: any(r.operation == "send" for r in results))
        service.select_model("other", "low")
        qtbot.waitUntil(lambda: any(r.operation == "select_model" for r in results))
        assert not next(r.error for r in results if r.operation == "select_model")
        calls = peers[0].calls
        assert not any(method == "turn/interrupt" for method, _ in calls)
        start = next(params for method, params in calls if method == "turn/start")
        assert start is not None
        assert start["model"] == "catalog-default"
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))


def test_common_stop_reuses_endpoint_after_input_release_and_turn_terminal(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = FakePeer()
    frame = sample_frame(64, 48)
    device = Device()
    results: list[ServiceResult] = []
    events: list[ServiceEvent] = []
    inputs: list[InputConnectionResult] = []
    service = CodexServiceThread(
        tmp_path,
        CaptureReader(lambda: CaptureSnapshot(phase="streaming", frame=frame)),
        lambda options, emit: CodexConnection(peer, options, emit),
        input_factory=lambda _config: device,
    )
    service.completed.connect(results.append)
    service.received.connect(events.append)
    service.input_changed.connect(inputs.append)
    service.start()
    try:
        service.apply_input(resolve_serial_config("COM3", "ch552", None))
        qtbot.waitUntil(lambda: bool(inputs))
        service.connect_runtime(ConnectionOptions(tmp_path / "runtime"))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        service.send("first")
        qtbot.waitUntil(lambda: any(result.operation == "send" for result in results))
        initial = next(params for method, params in peer.calls if method == "thread/start")
        assert initial is not None
        url, headers = _mcp_endpoint(initial)
        qtbot.waitUntil(
            lambda: any(
                isinstance(event.event, ConversationStatus) and event.event.turn_id == "turn-1"
                for event in events
            )
        )
        peer.auto_turn_started = False
        service.send("additional")
        qtbot.waitUntil(lambda: sum(result.operation == "send" for result in results) == 2)
        assert [
            params["input"]
            for method, params in peer.calls
            if method == "turn/start" and params is not None
        ] == [
            [{"type": "text", "text": "first"}],
            [{"type": "text", "text": "additional"}],
        ]
        assert not any(method == "turn/steer" for method, _ in peer.calls)
        with (
            httpx.Client(headers=headers, trust_env=False, timeout=5) as client,
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            operation = pool.submit(
                _call_game_tool,
                client,
                url,
                "act_and_observe",
                {"sequence": [{"kind": "press", "buttons": ["A"], "duration_ms": 10000}]},
            )
            qtbot.waitUntil(lambda: InputState(buttons=("A",)) in device.calls)
            service.interrupt()
            qtbot.waitUntil(lambda: any(method == "turn/interrupt" for method, _ in peer.calls))
            qtbot.waitUntil(operation.done)
            interrupted = operation.result().json()["result"]
            assert json.loads(interrupted["content"][0]["text"])["code"] == "INTERRUPTED"
            assert device.calls[-1] == InputState()
            assert not any(result.operation == "stop" for result in results)
            refused = _call_game_tool(
                client,
                url,
                "act_and_observe",
                {"sequence": [{"kind": "press", "buttons": ["B"], "duration_ms": 1}]},
            ).json()["result"]
            assert json.loads(refused["content"][0]["text"])["code"] == "INPUT_DISABLED"
            service._enqueue(lambda: peer.events.put_nowait(_terminal("interrupted")))
            qtbot.waitUntil(lambda: any(result.operation == "stop" for result in results))
            assert not next(result.error for result in results if result.operation == "stop")
            resumed = _call_game_tool(
                client,
                url,
                "act_and_observe",
                {"sequence": [{"kind": "press", "buttons": ["B"], "duration_ms": 1}]},
            ).json()["result"]
            assert not resumed["isError"]
        peer.auto_turn_started = True
        service.send("next")
        qtbot.waitUntil(lambda: sum(result.operation == "send" for result in results) == 3)
        assert sum(method == "thread/start" for method, _ in peer.calls) == 1
        assert not any(
            method in {"thread/unsubscribe", "thread/resume"} for method, _ in peer.calls
        )
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0), timeout=10000)
    assert not service.shutdown_error


def test_stop_waiting_for_terminal_can_disconnect_without_circular_wait(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = FakePeer()
    results: list[ServiceResult] = []
    events: list[ServiceEvent] = []
    service = CodexServiceThread(
        tmp_path,
        CaptureReader(CaptureSnapshot),
        lambda options, emit: CodexConnection(peer, options, emit),
    )
    service.completed.connect(results.append)
    service.received.connect(events.append)
    service.start()
    try:
        service.connect_runtime(ConnectionOptions(tmp_path / "runtime"))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        service.send("first")
        qtbot.waitUntil(lambda: any(result.operation == "send" for result in results))
        qtbot.waitUntil(
            lambda: any(
                isinstance(event.event, ConversationStatus) and event.event.turn_id == "turn-1"
                for event in events
            )
        )
        service.interrupt()
        qtbot.waitUntil(lambda: any(method == "turn/interrupt" for method, _ in peer.calls))
        service.disconnect_runtime()
        qtbot.waitUntil(lambda: any(result.operation == "disconnect" for result in results))
        qtbot.waitUntil(lambda: any(result.operation == "stop" for result in results))
        assert next(result.error for result in results if result.operation == "stop")
        assert peer.closed
        assert peer.close_calls == 1
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))
    assert not service.shutdown_error


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "unknown"])
def test_send_result_collects_turn_notification_and_preserves_delivery_classification(
    qtbot: QtBot, tmp_path: Path, outcome: str
) -> None:
    peer = FakePeer()
    peer.auto_turn_started = False
    if outcome == "rejected":
        peer.rpc_errors["turn/start"] = -32000
    elif outcome == "unknown":
        peer.failure = "turn/start"
    results: list[ServiceResult] = []
    events: list[ServiceEvent] = []
    service = CodexServiceThread(
        tmp_path,
        CaptureReader(CaptureSnapshot),
        lambda options, emit: CodexConnection(peer, options, emit),
    )
    service.completed.connect(results.append)
    service.received.connect(events.append)
    service.start()
    try:
        service.connect_runtime(ConnectionOptions(tmp_path / "runtime"))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        service.send("first")
        if outcome == "accepted":
            qtbot.waitUntil(lambda: any(method == "turn/start" for method, _ in peer.calls))
            service.send("while the start notification is pending")
            qtbot.waitUntil(lambda: any(result.operation == "send" for result in results))
            assert next(result.message for result in results if result.operation == "send") == (
                MessageResult("not_sent")
            )
            assert not any(
                isinstance(event.event, ConversationStatus) and event.event.turn_id is not None
                for event in events
            )
            service._enqueue(
                lambda: peer.events.put_nowait(
                    RpcNotification(
                        "turn/started", {"threadId": "thread-1", "turn": {"id": "turn-1"}}
                    )
                )
            )
            qtbot.waitUntil(lambda: sum(result.operation == "send" for result in results) == 2)
        qtbot.waitUntil(lambda: any(result.operation == "send" for result in results))
        result = next(result for result in reversed(results) if result.operation == "send")
        assert (
            result.message
            == {
                "accepted": MessageResult("accepted", "turn-1"),
                "rejected": MessageResult("not_sent"),
                "unknown": MessageResult("unknown"),
            }[outcome]
        )
        assert "PRIVATE" not in result.error
        assert any(
            isinstance(event.event, ConversationStatus) and event.event.turn_id is not None
            for event in events
        ) is (outcome == "accepted")
        assert sum(method == "turn/start" for method, _ in peer.calls) == 1
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))


def test_question_and_answer_delivery_do_not_suspend_input_and_stop_joins_delivery(
    qtbot: QtBot, tmp_path: Path
) -> None:
    peer = FakePeer()
    frame = sample_frame(64, 48)
    device = Device()
    results: list[ServiceResult] = []
    events: list[ServiceEvent] = []
    inputs: list[InputConnectionResult] = []
    service = CodexServiceThread(
        tmp_path,
        CaptureReader(lambda: CaptureSnapshot(phase="streaming", frame=frame)),
        lambda options, emit: CodexConnection(peer, options, emit),
        input_factory=lambda _config: device,
    )
    service.completed.connect(results.append)
    service.received.connect(events.append)
    service.input_changed.connect(inputs.append)
    service.start()
    try:
        service.apply_input(resolve_serial_config("COM3", "ch552", None))
        qtbot.waitUntil(lambda: bool(inputs))
        service.connect_runtime(ConnectionOptions(tmp_path / "runtime"))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        service.send("first")
        qtbot.waitUntil(lambda: any(result.operation == "send" for result in results))
        thread = next(params for method, params in peer.calls if method == "thread/start")
        assert thread is not None
        url, headers = _mcp_endpoint(thread)

        def deliver_question() -> None:
            peer.respond_gate = asyncio.Event()
            peer.events.put_nowait(_question())

        service._enqueue(deliver_question)
        qtbot.waitUntil(lambda: any(isinstance(event.event, HumanRequest) for event in events))
        question = next(event.event for event in events if isinstance(event.event, HumanRequest))
        with httpx.Client(headers=headers, trust_env=False, timeout=5) as client:
            result = _call_game_tool(
                client,
                url,
                "act_and_observe",
                {"sequence": [{"kind": "press", "buttons": ["A"], "duration_ms": 1}]},
            ).json()["result"]
            assert not result["isError"]
        answers = {"route": "西"}
        service.answer(question.token, answers=answers)
        answers["route"] = "変更後"
        qtbot.waitUntil(lambda: bool(peer.replies))
        assert peer.replies == [("question-1", {"answers": {"route": {"answers": ["西"]}}})]
        service._enqueue(
            lambda: peer.events.put_nowait(
                RpcNotification(
                    "serverRequest/resolved", {"threadId": "thread-1", "requestId": "question-1"}
                )
            )
        )
        service.interrupt()
        qtbot.waitUntil(lambda: any(method == "turn/interrupt" for method, _ in peer.calls))
        service._enqueue(lambda: peer.events.put_nowait(_terminal("interrupted")))
        qtbot.waitUntil(
            lambda: any(
                isinstance(event.event, ConversationStatus)
                and event.event.turn_id is None
                and event.event.stopping
                for event in events
            )
        )
        assert not any(result.operation in {"stop", "answer"} for result in results)
        gate = peer.respond_gate
        assert gate is not None
        service._enqueue(gate.set)
        qtbot.waitUntil(lambda: any(result.operation == "stop" for result in results))
        qtbot.waitUntil(lambda: any(result.operation == "answer" for result in results))
        answer = next(result for result in results if result.operation == "answer")
        assert answer.answer == AnswerResult(question.token, "sent")
        assert not next(result.error for result in results if result.operation == "stop")
        service.answer(question.token, answers={"route": "東"})
        qtbot.waitUntil(lambda: sum(result.operation == "answer" for result in results) == 2)
        assert results[-1].answer == AnswerResult(question.token, "not_sent")
        assert len(peer.replies) == 1
    finally:
        if peer.respond_gate is not None:
            service._enqueue(peer.respond_gate.set)
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0), timeout=10000)
    assert not service.shutdown_error


def test_goal_delete_preserves_turn_and_common_stop_preserves_objective(
    qtbot: QtBot, tmp_path: Path
) -> None:
    class GoalPeer(FakePeer):
        async def request(self, method: str, params: JsonObject | None) -> JsonValue:
            result = await super().request(method, params)
            if method == "thread/goal/set":
                assert isinstance(result, dict)
                self.events.put_nowait(
                    RpcNotification(
                        "thread/goal/updated", {"threadId": "thread-1", "goal": result["goal"]}
                    )
                )
            elif method == "thread/goal/clear":
                self.events.put_nowait(
                    RpcNotification("thread/goal/cleared", {"threadId": "thread-1"})
                )
            return result

    peer = GoalPeer()
    results: list[ServiceResult] = []
    events: list[ServiceEvent] = []
    service = CodexServiceThread(
        tmp_path,
        CaptureReader(CaptureSnapshot),
        lambda options, emit: CodexConnection(peer, options, emit),
    )
    service.completed.connect(results.append)
    service.received.connect(events.append)
    service.start()
    try:
        service.connect_runtime(ConnectionOptions(tmp_path / "runtime"))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        service.set_goal("クチバシティへ向かう")
        qtbot.waitUntil(lambda: any(result.operation == "goal_set" for result in results))
        assert not results[-1].error
        assert not any(method == "turn/start" for method, _ in peer.calls)
        service._enqueue(
            lambda: peer.events.put_nowait(
                RpcNotification("turn/started", {"threadId": "thread-1", "turn": {"id": "turn-1"}})
            )
        )
        qtbot.waitUntil(
            lambda: any(
                isinstance(event.event, ConversationStatus) and event.event.turn_id == "turn-1"
                for event in events
            )
        )
        service.clear_goal()
        qtbot.waitUntil(lambda: any(result.operation == "goal_clear" for result in results))
        assert not next(result.error for result in results if result.operation == "goal_clear")
        assert not any(method == "turn/interrupt" for method, _ in peer.calls)
        snapshots = [event.event for event in events if isinstance(event.event, ConversationStatus)]
        assert snapshots[-1].turn_id == "turn-1"
        assert snapshots[-1].goal is None
        service.set_goal("クチバシティへ向かう")
        qtbot.waitUntil(lambda: sum(result.operation == "goal_set" for result in results) == 2)
        service.interrupt()
        qtbot.waitUntil(lambda: any(method == "turn/interrupt" for method, _ in peer.calls))
        qtbot.waitUntil(
            lambda: any(
                isinstance(event.event, GoalEvent)
                and event.event.goal is not None
                and event.event.goal.status == "paused"
                for event in events
            )
        )
        service._enqueue(lambda: peer.events.put_nowait(_terminal("interrupted")))
        qtbot.waitUntil(lambda: any(result.operation == "stop" for result in results))
        assert not next(result.error for result in results if result.operation == "stop")
        service.resume_goal()
        qtbot.waitUntil(lambda: any(result.operation == "goal_resume" for result in results))
        assert not next(result.error for result in results if result.operation == "goal_resume")
        resumed = next(
            params for method, params in reversed(peer.calls) if method == "thread/goal/set"
        )
        assert resumed == {"threadId": "thread-1", "status": "active", "origin": "user"}
        assert peer.goal is not None
        assert peer.goal["objective"] == "クチバシティへ向かう"
        assert not any(method == "turn/start" for method, _ in peer.calls)
    finally:
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0), timeout=10000)
    assert not service.shutdown_error
