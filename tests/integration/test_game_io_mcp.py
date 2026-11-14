import asyncio
import base64
import json
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import httpx
import pytest
from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.game_io.input.config import get_protocol
from gigas.game_io.input.models import InputState, InputStep
from gigas.game_io.observation import Observation, ObservationError, ObservationResolution
from gigas.mcp import game_io as mcp_module
from gigas.mcp.game_io import GameIoMcpServer
from gigas.services._game_io_tools import act_and_observe
from gigas.services.input import OperationResult
from gigas.services.observation import ObservationService
from gigas.services.recording import RecordingService
from gigas.storage import project
from gigas.storage.project import ProjectWriter
from tests.integration.test_input_service import Device, Harness
from tests.integration.test_observation_storage import sample_frame

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterator

    from gigas.game_io.capture.models import Frame


async def initialize(client: httpx.AsyncClient, url: str) -> None:
    response = await client.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "gigas-test", "version": "1"},
            },
        },
    )
    response.raise_for_status()
    client.headers["MCP-Protocol-Version"] = response.json()["result"]["protocolVersion"]


def client_for(server: GameIoMcpServer) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={
            **server.config["http_headers"],
            "accept": "application/json, text/event-stream",
        },
        trust_env=False,
        timeout=5,
    )


def test_http_calls_injected_operation_without_grant() -> None:
    async def scenario() -> None:
        received: list[tuple[InputStep, ...]] = []

        async def observe_call() -> Observation:
            message = "observation is not requested by this call"
            raise AssertionError(message)

        async def input_call(
            sequence: tuple[InputStep, ...],
        ) -> tuple[OperationResult, None]:
            received.append(sequence)
            return OperationResult("BUSY", message="操作中"), None

        server = GameIoMcpServer(observe_call, input_call)
        await server.start()
        try:
            async with client_for(server) as client:
                await initialize(client, server.url)
                result = (
                    await act(client, server.url, [{"kind": "wait", "duration_ms": 1}])
                ).json()["result"]
                assert json.loads(result["content"][0]["text"])["code"] == "BUSY"
                assert len(received) == 1
                assert received[0][0].duration_ms == 1
        finally:
            await server.close()

    asyncio.run(scenario())


async def observe(client: httpx.AsyncClient, url: str) -> httpx.Response:
    response = await client.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "observe", "arguments": {}},
        },
    )
    response.raise_for_status()
    return response


async def act(client: httpx.AsyncClient, url: str, sequence: object) -> httpx.Response:
    response = await client.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "act_and_observe", "arguments": {"sequence": sequence}},
        },
    )
    response.raise_for_status()
    return response


async def input_unavailable(_sequence: tuple[InputStep, ...]) -> tuple[OperationResult, None]:
    return OperationResult("INPUT_DISABLED", message="機器未接続です"), None


def server_for(h: Harness) -> GameIoMcpServer:
    async def execute(
        sequence: tuple[InputStep, ...],
    ) -> tuple[OperationResult, Observation | ObservationError | None]:
        return await act_and_observe(
            sequence,
            start_input=h.service.start,
            observations=h.observations,
            recording=h.recording,
            thread_id="thread",
        )

    return GameIoMcpServer(h.observations.observe, execute)


@pytest.fixture
def io_harness(tmp_path: Path) -> "Iterator[Harness]":
    writer = ProjectWriter.create(tmp_path / "game")
    try:
        yield Harness(writer)
    finally:
        writer.close()


@pytest.mark.parametrize("protocol_key", ["ch552", "pokecon", "3ds"])
def test_http_act_returns_its_observation_and_operation(
    io_harness: Harness, protocol_key: str
) -> None:
    async def scenario() -> None:
        h = io_harness
        protocol = get_protocol(protocol_key)
        h.device.protocol = protocol
        await h.service.connect(h.device)
        server = server_for(h)
        await server.start()
        step: dict[str, object] = {"kind": "press", "buttons": ["A"], "duration_ms": 1}
        if protocol.touch:
            step["touch"] = {"x": 319, "y": 239}
        try:
            async with client_for(server) as client:
                await initialize(client, server.url)
                catalog = await client.post(
                    server.url, json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
                )
                tools = {tool["name"]: tool for tool in catalog.json()["result"]["tools"]}
                assert tools.keys() == {"observe", "act_and_observe"}
                tool = tools["act_and_observe"]
                assert tool["annotations"] == {
                    "readOnlyHint": False,
                    "destructiveHint": True,
                    "idempotentHint": False,
                    "openWorldHint": True,
                }
                assert tool["inputSchema"]["properties"]["sequence"]["type"] == "array"
                assert all(button in tool["description"] for button in protocol.buttons)
                result = (await act(client, server.url, [step])).json()["result"]
                assert not result["isError"]
                image, text = result["content"]
                metadata = json.loads(text["text"])
                assert h.device.calls == [
                    "open",
                    InputState(buttons=("A",), touch=(319, 239) if protocol.touch else None),
                    InputState(),
                ]
                await h.observations.drain()
                await h.recording.drain()
                rows = h.records()
                finished = next(
                    row["payload"] for row in rows if row["type"] == "input.operation_finished"
                )
                observed = next(
                    row["payload"] for row in rows if row["type"] == "observation.recorded"
                )
                assert isinstance(finished, dict)
                assert isinstance(observed, dict)
                assert finished["operation_id"] == metadata["operation_id"]
                assert finished["observation_id"] == metadata["observation_id"]
                assert observed["observation_id"] == metadata["observation_id"]
                assert (
                    base64.b64decode(image["data"])
                    == (h.writer.path / observed["image_path"]).read_bytes()
                )
        finally:
            await server.close()
            assert h.service.connected  # HTTP does not own the input connection.
            await h.close()

    asyncio.run(scenario())


def test_same_endpoint_uses_current_device_capabilities(io_harness: Harness) -> None:
    async def scenario() -> None:
        h = io_harness
        await h.service.connect(h.device)
        server = server_for(h)
        await server.start()
        url = server.url
        touch = {"kind": "press", "touch": {"x": 123, "y": 87}, "duration_ms": 1}
        three_ds = Device()
        three_ds.protocol = get_protocol("3ds")
        try:
            async with client_for(server) as client:
                await initialize(client, url)
                await h.service.connect(three_ds)
                accepted = (await act(client, url, [touch])).json()["result"]
                assert not accepted["isError"]
                assert three_ds.calls == ["open", InputState(touch=(123, 87)), InputState()]

                await h.service.connect(h.device)
                before = list(h.device.calls)
                rejected = (
                    await act(
                        client,
                        url,
                        [{"kind": "press", "buttons": ["A"], "duration_ms": 1}, touch],
                    )
                ).json()["result"]
                assert rejected["isError"]
                metadata = json.loads(rejected["content"][0]["text"])
                assert metadata["code"] == "INVALID_SEQUENCE"
                assert metadata["completed_steps"] == 0
                assert metadata["input_sent"] == "none"
                assert "operation_id" not in metadata
                assert h.device.calls == before
                assert server.url == url
        finally:
            await server.close()
            await h.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "sequence",
    [
        [
            {"kind": "press", "buttons": ["A"], "duration_ms": 1},
            {"kind": "press", "touch": {"x": 1, "y": 2}, "duration_ms": 1},
        ],
        [{"kind": "press", "buttons": ["A"], "duration_ms": True}],
    ],
)
def test_http_invalid_sequence_sends_no_input(io_harness: Harness, sequence: object) -> None:
    async def scenario() -> None:
        h = io_harness
        await h.service.connect(h.device)
        server = server_for(h)
        await server.start()
        try:
            async with client_for(server) as client:
                await initialize(client, server.url)
                result = (await act(client, server.url, sequence)).json()["result"]
                assert result["isError"]
                assert len(result["content"]) == 1
                metadata = json.loads(result["content"][0]["text"])
                assert metadata["code"] == "INVALID_SEQUENCE"
                assert metadata["phase"] == "accept"
                assert metadata["completed_steps"] == 0
                assert metadata["input_sent"] == "none"
                assert metadata["neutral"] == "not_needed"
                assert "operation_id" not in metadata
                assert h.device.calls == ["open"]
        finally:
            await server.close()
            await h.close()

    asyncio.run(scenario())


def test_http_missing_image_keeps_completed_input_and_next_call_available(
    io_harness: Harness,
) -> None:
    async def scenario() -> None:
        h = io_harness
        h.snapshot = CaptureSnapshot()
        await h.service.connect(h.device)
        server = server_for(h)
        await server.start()
        operation_ids: set[str] = set()
        try:
            async with client_for(server) as client:
                await initialize(client, server.url)
                for _ in range(2):
                    result = (
                        await act(
                            client,
                            server.url,
                            [{"kind": "press", "buttons": ["A"], "duration_ms": 1}],
                        )
                    ).json()["result"]
                    assert result["isError"]
                    assert len(result["content"]) == 1
                    metadata = json.loads(result["content"][0]["text"])
                    assert metadata["code"] == "DEVICE_UNAVAILABLE"
                    assert metadata["phase"] == "observe"
                    assert metadata["completed_steps"] == 1
                    assert metadata["input_sent"] == "sent"
                    assert metadata["neutral"] == "sent"
                    operation_ids.add(metadata["operation_id"])
                assert len(operation_ids) == 2
                assert h.device.calls == [
                    "open",
                    InputState(buttons=("A",)),
                    InputState(),
                    InputState(buttons=("A",)),
                    InputState(),
                ]
        finally:
            await server.close()
            await h.close()

    asyncio.run(scenario())


def test_http_same_endpoint_rejects_during_stop_and_accepts_after_drain(
    io_harness: Harness,
) -> None:
    async def scenario() -> None:
        h = io_harness
        stopping = False

        def start(
            sequence: tuple[InputStep, ...],
        ) -> tuple[str, asyncio.Task[OperationResult]] | OperationResult:
            if stopping:
                return OperationResult("INPUT_DISABLED", message="停止中です")
            return h.service.start(sequence)

        async def execute(
            sequence: tuple[InputStep, ...],
        ) -> tuple[OperationResult, Observation | ObservationError | None]:
            return await act_and_observe(
                sequence,
                start_input=start,
                observations=h.observations,
                recording=h.recording,
                thread_id="thread",
            )

        await h.service.connect(h.device)
        server = GameIoMcpServer(h.observations.observe, execute)
        await server.start()
        sequence = [{"kind": "press", "buttons": ["A"], "duration_ms": 1}]
        try:
            async with client_for(server) as client:
                await initialize(client, server.url)
                running = asyncio.create_task(
                    act(
                        client,
                        server.url,
                        [{"kind": "press", "buttons": ["A"], "duration_ms": 60_000}],
                    )
                )
                await asyncio.wait_for(h.device.pressed.wait(), 1)
                stopping = True
                h.service.stop()
                rejected = (await act(client, server.url, sequence)).json()["result"]
                assert json.loads(rejected["content"][0]["text"])["code"] == "INPUT_DISABLED"
                await h.service.drain_input()
                stopped = (await running).json()["result"]
                assert json.loads(stopped["content"][0]["text"])["code"] == "INTERRUPTED"
                stopping = False
                accepted = (await act(client, server.url, sequence)).json()["result"]
                assert not accepted["isError"]
                assert h.device.calls == [
                    "open",
                    InputState(buttons=("A",)),
                    InputState(),
                    InputState(buttons=("A",)),
                    InputState(),
                ]
        finally:
            h.service.stop()
            await h.service.drain_input()
            await server.close()
            await h.close()

    asyncio.run(scenario())


def test_http_deadline_returns_unknown_result_and_leaves_input_cleanup_owned(
    io_harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_module, "TOOL_TIMEOUT_SECONDS", 0.05)

    async def scenario() -> None:
        h = io_harness
        release = asyncio.Event()
        send = h.device.send

        async def delayed_send(state: InputState) -> None:
            await send(state)
            if state != InputState():
                await release.wait()

        monkeypatch.setattr(h.device, "send", delayed_send)
        await h.service.connect(h.device)
        server = server_for(h)
        await server.start()
        try:
            async with client_for(server) as client:
                await initialize(client, server.url)
                pending = asyncio.create_task(
                    act(
                        client,
                        server.url,
                        [{"kind": "press", "buttons": ["A"], "duration_ms": 600_000}],
                    )
                )
                await asyncio.wait_for(h.device.pressed.wait(), 1)
                await asyncio.sleep(0.1)  # The deadline cancels the request while write is held.
                assert not pending.done()
                assert h.device.calls == ["open", InputState(buttons=("A",))]
                release.set()
                response = await asyncio.wait_for(pending, 1)
                result = response.json()["result"]
                assert result["isError"]
                assert len(result["content"]) == 1
                metadata = json.loads(result["content"][0]["text"])
                assert metadata["code"] == "TOOL_TIMEOUT"
                assert metadata["input_sent"] == "unknown"
                assert metadata["neutral"] == "unconfirmed"
                assert "completed_steps" not in metadata
                assert "operation_id" not in metadata
                assert h.device.calls == ["open", InputState(buttons=("A",)), InputState()]
                accepted = (
                    await act(client, server.url, [{"kind": "wait", "duration_ms": 1}])
                ).json()["result"]
                assert not accepted["isError"]
        finally:
            release.set()
            h.service.stop()
            await h.service.drain_input()
            await server.close()
            await h.close()

    asyncio.run(scenario())


def test_http_observations_return_same_images_before_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = ProjectWriter.create(tmp_path / "project")
    frame = sample_frame()
    entered, release = Event(), Event()
    publish = project._publish_image

    def delayed_publish(path: Path, data: bytes) -> None:
        entered.set()
        if not release.wait(10):
            message = "saving release deadline"
            raise TimeoutError(message)
        publish(path, data)

    monkeypatch.setattr(project, "_publish_image", delayed_publish)

    async def scenario() -> None:
        recording = RecordingService(writer)
        service = ObservationService(
            CaptureReader(lambda: CaptureSnapshot(phase="streaming", frame=frame)), recording
        )
        server = GameIoMcpServer(service.observe, input_unavailable)
        await server.start()
        try:
            async with client_for(server) as client:
                await initialize(client, server.url)
                catalog = await client.post(
                    server.url, json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
                )
                tool = catalog.json()["result"]["tools"][0]
                assert tool["name"] == "observe"
                assert tool["annotations"]["destructiveHint"] is False
                assert tool["annotations"]["openWorldHint"] is False
                first = (await observe(client, server.url)).json()["result"]
                assert await asyncio.to_thread(entered.wait, 3)
                second = (await observe(client, server.url)).json()["result"]
                assert not first["isError"]
                assert not second["isError"]
                assert (writer.path / "events.jsonl").read_bytes() == b""
                release.set()
                await service.drain()
                await recording.drain()
                rows = [
                    json.loads(row)
                    for row in (writer.path / "events.jsonl").read_text().splitlines()
                ]
                for result, row in zip((first, second), rows, strict=True):
                    image, text = result["content"]
                    metadata = json.loads(text["text"])
                    assert image["type"] == "image"
                    assert image["mimeType"] == "image/png"
                    assert (
                        base64.b64decode(image["data"])
                        == (writer.path / row["payload"]["image_path"]).read_bytes()
                    )
                    assert metadata == {
                        key: value for key, value in row["payload"].items() if key != "image_path"
                    }
                assert rows[0]["payload"]["observation_id"] != rows[1]["payload"]["observation_id"]
        finally:
            release.set()
            await server.close()
            await service.drain()
            await recording.drain()

    try:
        asyncio.run(scenario())
    finally:
        writer.close()


def test_http_authentication_host_origin_and_old_connection(tmp_path: Path) -> None:
    writer = ProjectWriter.create(tmp_path / "project")

    async def scenario() -> None:
        recording = RecordingService(writer)
        service = ObservationService(CaptureReader(CaptureSnapshot), recording)
        old = GameIoMcpServer(service.observe, input_unavailable)
        await old.start()
        old_headers = old.config["http_headers"]
        await old.close()
        current = GameIoMcpServer(service.observe, input_unavailable)
        await current.start()
        try:
            assert current.server_name != old.server_name
            async with client_for(current) as client:
                for headers in ({"Authorization": ""}, old_headers):
                    response = await client.post(current.url, json={}, headers=headers)
                    assert response.status_code == 401
                for headers, status in (
                    ({"Host": "untrusted.example"}, 421),
                    ({"Origin": "https://untrusted.example"}, 403),
                ):
                    response = await client.post(current.url, json={}, headers=headers)
                    assert response.status_code == status
                await initialize(client, current.url)
                result = (await observe(client, current.url)).json()["result"]
                assert result["isError"]
                assert len(result["content"]) == 1
                assert json.loads(result["content"][0]["text"])["code"] == "DEVICE_UNAVAILABLE"
        finally:
            await current.close()
            await service.drain()
            await recording.drain()

    try:
        asyncio.run(scenario())
    finally:
        writer.close()


def test_failed_lifespan_is_an_error_without_exiting_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def failed_lifespan(_app: Starlette) -> "AsyncGenerator[None]":
        message = "injected lifespan startup failure"
        raise RuntimeError(message)
        yield  # pragma: no cover - creates an async context manager that cannot enter.

    def failed_app(_self: object, **_kwargs: object) -> Starlette:
        return Starlette(lifespan=failed_lifespan)

    monkeypatch.setattr(MCPServer, "streamable_http_app", failed_app)
    writer = ProjectWriter.create(tmp_path / "project")

    async def scenario() -> None:
        recording = RecordingService(writer)
        service = ObservationService(CaptureReader(CaptureSnapshot), recording)
        server = GameIoMcpServer(service.observe, input_unavailable)
        with pytest.raises(RuntimeError, match="MCP"):
            await server.start()
        await server.close()
        await service.drain()
        await recording.drain()

    try:
        asyncio.run(scenario())
    finally:
        writer.close()


def test_cancelled_close_keeps_http_and_observation_work_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = ProjectWriter.create(tmp_path / "project")
    frame = sample_frame()
    entered, release = Event(), Event()
    encode = Observation.from_frame

    def delayed_encode(
        _cls: type[Observation], frame: "Frame", *, resolution: ObservationResolution = "full_hd"
    ) -> Observation:
        entered.set()
        if not release.wait(10):
            message = "encoding release deadline"
            raise TimeoutError(message)
        return encode(frame, resolution=resolution)

    monkeypatch.setattr(Observation, "from_frame", classmethod(delayed_encode))

    async def scenario() -> None:
        recording = RecordingService(writer)
        service = ObservationService(
            CaptureReader(lambda: CaptureSnapshot(phase="streaming", frame=frame)), recording
        )
        server = GameIoMcpServer(service.observe, input_unavailable)
        await server.start()
        url = server.url
        try:
            async with client_for(server) as client:
                await initialize(client, url)
                request = asyncio.create_task(observe(client, url))
                assert await asyncio.to_thread(entered.wait, 3)
                closing = asyncio.create_task(server.close())
                await asyncio.sleep(0)
                closing.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await closing
                with pytest.raises(RuntimeError, match="MCP"):
                    _ = server.config
                try:
                    rejected = await client.post(url, json={}, timeout=1)
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    pass  # The listening socket may already have closed.
                else:
                    assert rejected.status_code == 503
                release.set()
                result = (await request).json()["result"]
                assert not result["isError"]
                await asyncio.wait_for(server.close(), 3)
                await service.drain()
                await recording.drain()
                records = (writer.path / "events.jsonl").read_text().splitlines()
                assert len(records) == 1
                payload = json.loads(records[0])["payload"]
                assert (writer.path / payload["image_path"]).exists()
        finally:
            release.set()
            await server.close()
            await service.drain()
            await recording.drain()

    try:
        asyncio.run(scenario())
    finally:
        writer.close()
