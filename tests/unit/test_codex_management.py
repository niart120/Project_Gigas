import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from tests.unit.test_codex_connection import FakePeer

from gigas.runtime.codex.connection import CodexConnection, CodexConnectionError
from gigas.runtime.codex.management import CodexManagement, ProjectInfo
from gigas.runtime.codex.models import ConnectionOptions
from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcError


def _project(root: Path, *, name: str = "FireRed") -> JsonObject:
    return {
        "id": "project-1",
        "name": name,
        "roots": [{"path": str(root)}],
        "metadata": {"ignored": "server-owned"},
        "createdAt": 1,
        "updatedAt": 2,
        "position": 0,
    }


@asynccontextmanager
async def _client(root: Path) -> AsyncGenerator[tuple[CodexManagement, FakePeer]]:
    peer = FakePeer()
    peer.authenticated = False
    peer.responses["windowsSandbox/readiness"] = {"status": "notConfigured"}
    connection = CodexConnection(peer, ConnectionOptions(root, model="unavailable"), lambda _: None)
    await connection.open()
    try:
        yield CodexManagement(connection), peer
    finally:
        await connection.close()


def _project_calls(peer: FakePeer) -> list[tuple[str, JsonObject | None]]:
    return [(method, params) for method, params in peer.calls if method.startswith("project/")]


def test_project_creation_uses_one_absolute_root_without_preparing_execution(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        root = tmp_path / "not-created-by-client"
        async with _client(tmp_path) as (client, peer):
            peer.responses["project/create"] = {"project": _project(root)}
            project = await client.create_project("FireRed", root, idempotency_key="operation-1")
            assert project == ProjectInfo("project-1", "FireRed", root)
            assert _project_calls(peer) == [
                (
                    "project/create",
                    {
                        "name": "FireRed",
                        "roots": [{"path": str(root)}],
                        "idempotencyKey": "operation-1",
                    },
                )
            ]
            assert not root.exists()
            assert not any(method in {"thread/start", "thread/resume"} for method, _ in peer.calls)
            assert not any(method.startswith("turn/") for method, _ in peer.calls)

    asyncio.run(scenario())


def test_project_list_returns_one_page_and_passes_opaque_cursor_unchanged(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (client, peer):
            cursor = "opaque:/+?token=two"
            peer.responses["project/list"] = {
                "data": [_project(tmp_path)],
                "nextCursor": cursor,
            }
            first = await client.list_projects(limit=2)
            assert first.projects == (ProjectInfo("project-1", "FireRed", tmp_path),)
            assert first.next_cursor == cursor
            assert len(_project_calls(peer)) == 1
            peer.responses["project/list"] = {"data": []}
            last = await client.list_projects(cursor=first.next_cursor, limit=2)
            assert last.projects == ()
            assert last.next_cursor is None
            assert _project_calls(peer) == [
                ("project/list", {"limit": 2}),
                ("project/list", {"cursor": cursor, "limit": 2}),
            ]

    asyncio.run(scenario())


def test_project_read_uses_the_server_response_without_caching_or_path_probes(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (client, peer):
            missing = tmp_path / "moved-workspace"
            peer.responses["project/read"] = {"project": _project(missing)}
            first = await client.read_project("project-1")
            peer.responses["project/read"] = {"project": _project(missing, name="Renamed")}
            second = await client.read_project("project-1")
            assert first.name == "FireRed"
            assert second == ProjectInfo("project-1", "Renamed", missing)
            assert len(_project_calls(peer)) == 2
            assert not missing.exists()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "override",
    [
        {"id": None},
        {"name": 5},
        {"roots": []},
        {"roots": [{"path": "relative"}]},
        {"roots": [{"path": None}]},
        {"roots": "not-a-list"},
        {"roots": [{"path": "C:/one"}, {"path": "C:/two"}]},
    ],
)
def test_project_response_requires_supported_identity_and_one_absolute_root(
    tmp_path: Path, override: JsonObject
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (client, peer):
            peer.responses["project/read"] = {"project": {**_project(tmp_path), **override}}
            with pytest.raises(CodexConnectionError):
                await client.read_project("project-1")
            assert len(_project_calls(peer)) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "response",
    [None, {}, {"data": None}, {"data": [False]}, {"data": [], "nextCursor": 1}],
)
def test_project_page_rejects_invalid_response_without_retrying(
    tmp_path: Path, response: JsonValue
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (client, peer):
            peer.responses["project/list"] = response
            with pytest.raises(CodexConnectionError):
                await client.list_projects()
            assert len(_project_calls(peer)) == 1

    asyncio.run(scenario())


def test_relative_project_root_is_rejected_before_any_rpc(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (client, peer):
            with pytest.raises(ValueError, match="絶対パス"):
                await client.create_project("FireRed", Path("relative"), idempotency_key="one")
            assert _project_calls(peer) == []

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["project/create", "project/list", "project/read"])
def test_project_rpc_failure_is_returned_without_retry_or_execution(
    tmp_path: Path, method: str
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (client, peer):
            peer.rpc_errors[method] = -32602
            if method == "project/create":
                operation = client.create_project("FireRed", tmp_path, idempotency_key="one")
            elif method == "project/list":
                operation = client.list_projects()
            else:
                operation = client.read_project("project-1")
            with pytest.raises(RpcError, match="-32602"):
                await operation
            assert len(_project_calls(peer)) == 1
            assert not any(method.startswith("thread/") for method, _ in peer.calls)

    asyncio.run(scenario())
