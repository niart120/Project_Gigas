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
from gigas.game_io.observation import Observation, ObservationResolution
from gigas.mcp.observation import ObservationMcpServer
from gigas.services.observation import ObservationService
from gigas.services.recording import RecordingService
from gigas.storage import project
from gigas.storage.project import ProjectWriter
from tests.integration.test_observation_storage import sample_frame

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

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


def client_for(server: ObservationMcpServer) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={
            **server.config["http_headers"],
            "accept": "application/json, text/event-stream",
        },
        trust_env=False,
        timeout=5,
    )


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
        server = ObservationMcpServer(service)
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
        old = ObservationMcpServer(service)
        await old.start()
        old_headers = old.config["http_headers"]
        await old.close()
        current = ObservationMcpServer(service)
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
        server = ObservationMcpServer(service)
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
        server = ObservationMcpServer(service)
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
