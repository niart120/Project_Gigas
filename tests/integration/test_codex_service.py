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
from gigas.game_io.input.config import resolve_serial_config
from gigas.game_io.input.models import InputState
from gigas.mcp.game_io import GameIoMcpServer
from gigas.runtime.codex.connection import (
    AnswerDeliveryError,
    CodexConnection,
    CodexConnectionError,
)
from gigas.runtime.codex.models import (
    ConnectionEvent,
    ConnectionOptions,
    HumanRequest,
    SessionStatus,
)
from gigas.runtime.codex.transport import JsonObject, RpcNotification
from gigas.services.codex import AnswerResult, CodexServiceThread
from gigas.storage import project
from gigas.storage.project import ProjectWriter
from tests.integration.test_input_service import Device
from tests.integration.test_observation_storage import sample_frame
from tests.unit.test_codex_connection import FakePeer, _question, _terminal

if TYPE_CHECKING:
    from gigas.game_io.input.config import SerialConfig
    from gigas.runtime.codex.models import ThreadConfiguration
    from gigas.services.codex import InputConnectionResult, ServiceEvent, ServiceResult, _Command


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
        emit: Callable[[ConnectionEvent], None],
        prepare: Callable[[str, str | None], Awaitable["ThreadConfiguration"]],
        started: Callable[[str], Awaitable[None]],
    ) -> CodexConnection:
        peer = FakePeer()
        peers.append(peer)
        return CodexConnection(peer, options, emit, prepare_turn=prepare, on_thread_started=started)

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
        rows = [json.loads(line) for line in (archive / "events.jsonl").read_text().splitlines()]
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


def test_runtime_failure_revokes_old_mcp_endpoint(qtbot: QtBot, tmp_path: Path) -> None:
    peer = FakePeer()
    peer.failure = "turn/start"
    results: list[ServiceResult] = []

    def factory(
        options: ConnectionOptions,
        emit: Callable[[ConnectionEvent], None],
        prepare: Callable[[str, str | None], Awaitable["ThreadConfiguration"]],
        started: Callable[[str], Awaitable[None]],
    ) -> CodexConnection:
        return CodexConnection(peer, options, emit, prepare_turn=prepare, on_thread_started=started)

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


@pytest.mark.parametrize("next_protocol", ["ch552", "3ds"])
@pytest.mark.parametrize("switch_while_starting", [False, True])
def test_turn_completion_revokes_input_and_next_send_republishes_same_thread(
    qtbot: QtBot, tmp_path: Path, next_protocol: str, *, switch_while_starting: bool
) -> None:
    peer = FakePeer()
    loops: list[asyncio.AbstractEventLoop] = []
    devices: list[Device] = []
    results: list[ServiceResult] = []
    events: list[ServiceEvent] = []
    applied: list[InputConnectionResult] = []

    def factory(
        options: ConnectionOptions,
        emit: Callable[[ConnectionEvent], None],
        prepare: Callable[[str, str | None], Awaitable["ThreadConfiguration"]],
        started: Callable[[str], Awaitable[None]],
    ) -> CodexConnection:
        loops.append(asyncio.get_running_loop())
        if switch_while_starting:
            peer.gates["thread/start"] = asyncio.Event()
        return CodexConnection(peer, options, emit, prepare_turn=prepare, on_thread_started=started)

    def input_factory(config: "SerialConfig") -> Device:
        device = Device()
        device.protocol = config.protocol
        devices.append(device)
        return device

    frame = sample_frame(64, 48)
    service = CodexServiceThread(
        tmp_path,
        CaptureReader(lambda: CaptureSnapshot(phase="streaming", frame=frame)),
        factory,
        input_factory=input_factory,
    )
    service.completed.connect(results.append)
    service.received.connect(events.append)
    service.input_changed.connect(applied.append)
    service.start()
    try:
        service.apply_input(resolve_serial_config("COM3", "ch552", None))
        qtbot.waitUntil(lambda: len(applied) == 1)
        assert applied[0].connected
        service.connect_runtime(ConnectionOptions(tmp_path / "runtime"))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        service.send("first input")
        if switch_while_starting:
            qtbot.waitUntil(lambda: any(method == "thread/start" for method, _ in peer.calls))
            service.apply_input(resolve_serial_config("COM3", next_protocol, None))
            qtbot.waitUntil(lambda: len(applied) == 2)
            assert applied[-1].connected
            loops[0].call_soon_threadsafe(peer.gates["thread/start"].set)
        qtbot.waitUntil(lambda: any(result.operation == "send" for result in results))
        assert not results[-1].error
        initial = next(params for method, params in peer.calls if method == "thread/start")
        assert initial is not None
        archive = Path(str(initial["cwd"]))
        old_url, old_headers = _mcp_endpoint(initial)
        with httpx.Client(headers=old_headers, trust_env=False, timeout=3) as client:
            result = _call_game_tool(
                client,
                old_url,
                "act_and_observe",
                {"sequence": [{"kind": "press", "buttons": ["A"], "duration_ms": 1}]},
            ).json()["result"]
            first = None
            if switch_while_starting and next_protocol == "3ds":
                assert result["isError"]
                assert json.loads(result["content"][0]["text"])["code"] == "INPUT_DISABLED"
                assert devices[0].calls == ["open", "close"]
                assert devices[1].calls == ["open"]
            else:
                assert not result["isError"]
                first = json.loads(result["content"][1]["text"])
                assert devices[0].calls == ["open", InputState(buttons=("A",)), InputState()]

            events.clear()
            loops[0].call_soon_threadsafe(peer.events.put_nowait, _terminal())
            qtbot.waitUntil(
                lambda: any(
                    isinstance(event.event, SessionStatus) and event.event.phase == "ready"
                    for event in events
                )
            )
            rejected = _call_game_tool(
                client,
                old_url,
                "act_and_observe",
                {"sequence": [{"kind": "press", "buttons": ["B"], "duration_ms": 1}]},
            ).json()["result"]
            assert rejected["isError"]
            assert json.loads(rejected["content"][0]["text"])["code"] == "INPUT_DISABLED"
            assert not _call_game_tool(client, old_url, "observe").json()["result"]["isError"]

            applied_count = len(applied)
            service.apply_input(resolve_serial_config("COM3", next_protocol, None))
            qtbot.waitUntil(lambda: len(applied) == applied_count + 1)
            assert applied[-1].connected
            loops[0].call_soon_threadsafe(
                peer.responses.__setitem__,
                "turn/start",
                {"turn": {"id": "turn-2", "status": "inProgress", "items": []}},
            )
            service.send("second input")
            qtbot.waitUntil(lambda: sum(result.operation == "send" for result in results) == 2)
            assert not results[-1].error
            resumed = next(params for method, params in peer.calls if method == "thread/resume")
            assert resumed is not None
            assert resumed["threadId"] == "thread-1"
            assert resumed["cwd"] == initial["cwd"]
            methods = [method for method, _ in peer.calls]
            assert methods.index("thread/unsubscribe") < methods.index("thread/resume")
            url, headers = _mcp_endpoint(resumed)
            assert headers != old_headers
            client.headers.update(headers)
            catalog = client.post(url, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tools = catalog.json()["result"]["tools"]
            description = next(
                tool["description"] for tool in tools if tool["name"] == "act_and_observe"
            )
            assert devices[-1].protocol.label in description
            step: JsonObject = {"kind": "press", "buttons": ["A"], "duration_ms": 1}
            if next_protocol == "3ds":
                step["touch"] = {"x": 319, "y": 239}
            result = _call_game_tool(client, url, "act_and_observe", {"sequence": [step]}).json()[
                "result"
            ]
            assert not result["isError"]
            second = json.loads(result["content"][1]["text"])
            if first is not None:
                assert first["operation_id"] != second["operation_id"]
            if next_protocol == "3ds":
                assert devices[0].calls[-1] == "close"
                assert devices[-1].calls == [
                    "open",
                    InputState(buttons=("A",), touch=(319, 239)),
                    InputState(),
                ]
            else:
                assert len(devices) == 1
    finally:
        if loops and "thread/start" in peer.gates:
            loops[0].call_soon_threadsafe(peer.gates["thread/start"].set)
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0), timeout=10000)
    assert not service.shutdown_error
    rows = [json.loads(line) for line in (archive / "events.jsonl").read_text().splitlines()]
    started = [row["payload"] for row in rows if row["type"] == "input.operation_started"]
    expected = [(second["operation_id"], "turn-2")]
    if first is not None:
        expected.insert(0, (first["operation_id"], "turn-1"))
    assert [(row["operation_id"], row["turn_id"]) for row in started] == expected
    finished = [row["payload"] for row in rows if row["type"] == "input.operation_finished"]
    expected_observations = [second["observation_id"]]
    if first is not None:
        expected_observations.insert(0, first["observation_id"])
    assert [row["observation_id"] for row in finished] == expected_observations


@pytest.mark.parametrize("blocking", [False, True])
@pytest.mark.parametrize(
    ("stop_during_answer", "external_resolution"),
    [(False, False), (True, False), (False, True)],
)
def test_human_answer_keeps_input_suspended_until_delivery_without_reviving_stopped_run(
    qtbot: QtBot,
    tmp_path: Path,
    *,
    blocking: bool,
    stop_during_answer: bool,
    external_resolution: bool,
) -> None:
    peer = FakePeer()
    loops: list[asyncio.AbstractEventLoop] = []
    callbacks: list[Callable[[ConnectionEvent], None]] = []
    device = Device()
    results: list[ServiceResult] = []
    events: list[ServiceEvent] = []
    applied: list[InputConnectionResult] = []

    def factory(
        options: ConnectionOptions,
        emit: Callable[[ConnectionEvent], None],
        prepare: Callable[[str, str | None], Awaitable["ThreadConfiguration"]],
        started: Callable[[str], Awaitable[None]],
    ) -> CodexConnection:
        loops.append(asyncio.get_running_loop())
        callbacks.append(emit)
        peer.respond_gate = asyncio.Event()
        return CodexConnection(peer, options, emit, prepare_turn=prepare, on_thread_started=started)

    frame = sample_frame(64, 48)
    service = CodexServiceThread(
        tmp_path,
        CaptureReader(lambda: CaptureSnapshot(phase="streaming", frame=frame)),
        factory,
        input_factory=lambda _config: device,
    )
    service.completed.connect(results.append)
    service.received.connect(events.append)
    service.input_changed.connect(applied.append)
    service.start()
    try:
        service.apply_input(resolve_serial_config("COM3", "ch552", None))
        qtbot.waitUntil(lambda: bool(applied))
        service.connect_runtime(ConnectionOptions(tmp_path / "runtime"))
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        service.send("input after answer")
        qtbot.waitUntil(lambda: any(result.operation == "send" for result in results))
        initial = next(params for method, params in peer.calls if method == "thread/start")
        assert initial is not None
        url, headers = _mcp_endpoint(initial)
        arguments: JsonObject = {
            "sequence": [{"kind": "press", "buttons": ["A"], "duration_ms": 1}]
        }
        with httpx.Client(headers=headers, trust_env=False, timeout=3) as client:
            loops[0].call_soon_threadsafe(peer.events.put_nowait, _question(blocking=blocking))
            qtbot.waitUntil(lambda: any(isinstance(event.event, HumanRequest) for event in events))
            request = next(event.event for event in events if isinstance(event.event, HumanRequest))
            suspended = _call_game_tool(client, url, "act_and_observe", arguments).json()["result"]
            assert suspended["isError"]
            assert json.loads(suspended["content"][0]["text"])["code"] == "INPUT_DISABLED"

            if external_resolution:
                events.clear()
                loops[0].call_soon_threadsafe(
                    peer.events.put_nowait,
                    RpcNotification(
                        "serverRequest/resolved",
                        {"threadId": "thread-1", "requestId": request.rpc_id},
                    ),
                )
                qtbot.waitUntil(
                    lambda: any(
                        isinstance(event.event, SessionStatus) and event.event.phase == "running"
                        for event in events
                    )
                )
                result = _call_game_tool(client, url, "act_and_observe", arguments).json()["result"]
                assert not result["isError"]
                assert device.calls == ["open", InputState(buttons=("A",)), InputState()]
                assert not peer.replies
                return

            service.answer(request.token, answers={"route": "東"})
            qtbot.waitUntil(lambda: bool(peer.replies))
            assert not any(result.operation == "answer" for result in results)
            # A runtime running notification can arrive after the request was
            # resolved locally but while the answer's transport write still waits.
            events.clear()
            loops[0].call_soon_threadsafe(
                peer.events.put_nowait,
                RpcNotification(
                    "turn/started",
                    {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "inProgress"}},
                ),
            )
            qtbot.waitUntil(
                lambda: any(
                    isinstance(event.event, SessionStatus) and event.event.phase == "running"
                    for event in events
                )
            )
            pending = _call_game_tool(client, url, "act_and_observe", arguments).json()["result"]
            assert pending["isError"]
            assert json.loads(pending["content"][0]["text"])["code"] == "INPUT_DISABLED"
            assert device.calls == ["open"]

            if stop_during_answer:
                service.interrupt()
                qtbot.waitUntil(lambda: any(result.operation == "stop" for result in results))
                assert any(method == "turn/interrupt" for method, _ in peer.calls)
                events.clear()
                # Test the service's stale callback boundary as well as the
                # connection's own suppression of late turn notifications.
                loops[0].call_soon_threadsafe(callbacks[0], SessionStatus("running"))
                qtbot.waitUntil(
                    lambda: any(
                        isinstance(event.event, SessionStatus) and event.event.phase == "running"
                        for event in events
                    )
                )
            assert peer.respond_gate is not None
            loops[0].call_soon_threadsafe(peer.respond_gate.set)
            qtbot.waitUntil(lambda: any(result.operation == "answer" for result in results))
            answer = next(result for result in results if result.operation == "answer")
            assert not answer.error
            assert answer.answer == AnswerResult(request.token, "sent")
            result = _call_game_tool(client, url, "act_and_observe", arguments).json()["result"]
            if stop_during_answer:
                assert result["isError"]
                assert json.loads(result["content"][0]["text"])["code"] == "INPUT_DISABLED"
                assert device.calls == ["open"]
            else:
                assert not result["isError"]
                assert device.calls == ["open", InputState(buttons=("A",)), InputState()]
            assert not _call_game_tool(client, url, "observe").json()["result"]["isError"]
    finally:
        if loops and peer.respond_gate is not None:
            loops[0].call_soon_threadsafe(peer.respond_gate.set)
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0), timeout=10000)
        assert not service.shutdown_error


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
        prepare: Callable[[str, str | None], Awaitable["ThreadConfiguration"]],
        started: Callable[[str], Awaitable[None]],
    ) -> CodexConnection:
        peer = FakePeer()
        peers.append(peer)
        return CodexConnection(peer, options, emit, prepare_turn=prepare, on_thread_started=started)

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


@pytest.mark.parametrize("failure", ["runtime_stop", "input_close"])
def test_shutdown_still_closes_when_stop_fails(qtbot: QtBot, tmp_path: Path, failure: str) -> None:
    class FailingStop(ControlledConnection):
        """Fail interruption without changing close cleanup."""

        async def stop(self) -> None:
            """Inject an unexpected operation failure."""
            if failure == "input_close":
                await super().stop()
                return
            message = "secret stop error"
            raise OSError(message)

    connections: list[FailingStop] = []

    def factory(
        _options: ConnectionOptions, emit: Callable[[ConnectionEvent], None], *_callbacks: object
    ) -> FailingStop:
        connection = FailingStop(emit)
        connections.append(connection)
        return connection

    device = Device()
    device.fail_close = failure == "input_close"
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
    assert bool(service.shutdown_error) == (failure == "input_close")


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
def test_input_switch_keeps_only_latest_target_or_explicit_stop(
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
        qtbot.waitUntil(lambda: len(applied) == (1 if stop else 2))
        assert devices[0].calls == ["open", "close"]
        assert len(devices) == (1 if stop else 2)
        if not stop:
            assert applied[-1].config.port == "COM3"
            assert applied[-1].connected
    finally:
        release.set()
        service.shutdown()
        qtbot.waitUntil(lambda: service.wait(0))
    assert service.shutdown_error == ""


def test_thread_save_failure_does_not_prevent_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot))
        await service._prepare_turn("input", None)
        assert service._writer is not None

        def fail(thread_id: str) -> None:
            raise OSError(thread_id)

        monkeypatch.setattr(service._writer, "record_thread", fail)
        await service._thread_started("thread-1")
        assert service._grant is not None
        assert service._grant.thread_id == "thread-1"
        await service._input.close()
        await service._close_recording()
        service._writer.close()

    asyncio.run(check())
