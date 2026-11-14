import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest

from gigas.runtime.codex.connection import CodexConnection
from gigas.runtime.codex.management import CodexManagement, ProjectInfo
from gigas.runtime.codex.models import ConnectionOptions
from gigas.runtime.codex.transport import RpcError
from gigas.services._workspaces import resolve_workspace, select_project
from tests.unit.test_codex_connection import FakePeer

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from gigas.runtime.codex.transport import JsonObject, JsonValue


class ProjectsPeer(FakePeer):
    """Supply Project pages at the existing runtime boundary."""

    def __init__(self) -> None:
        """Prepare an empty server-owned Project list."""
        super().__init__()
        self.pages: dict[str | None, JsonObject] = {None: {"data": []}}

    async def request(self, method: str, params: "JsonObject | None") -> "JsonValue":
        """Return the page selected by the opaque request cursor."""
        if method == "project/list":
            cursor = params.get("cursor") if params is not None else None
            assert cursor is None or isinstance(cursor, str)
            self.responses[method] = self.pages[cursor]
        return await super().request(method, params)


def project_data(root: Path, identifier: str = "project-1") -> "JsonObject":
    return {"id": identifier, "name": root.name, "roots": [{"path": str(root)}]}


@asynccontextmanager
async def management(root: Path) -> "AsyncGenerator[tuple[CodexManagement, ProjectsPeer]]":
    peer = ProjectsPeer()
    peer.authenticated = False
    connection = CodexConnection(peer, ConnectionOptions(root), lambda _event: None)
    await connection.open()
    try:
        peer.calls.clear()
        yield CodexManagement(connection), peer
    finally:
        await connection.close()


def test_open_reuses_first_matching_project_across_pages_without_changing_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "existing"
    root.mkdir()
    notes = root / "攻略.md"
    notes.write_text("元の資料", encoding="utf-8")

    async def scenario() -> None:
        async with management(tmp_path) as (client, peer):
            cursor = "opaque:/+?page=two"
            peer.pages = {
                None: {"data": [project_data(tmp_path / "other")], "nextCursor": cursor},
                cursor: {
                    "data": [project_data(root), project_data(root, "duplicate-root")],
                    "nextCursor": "unused-page",
                },
            }
            selected = await select_project(client, root, create=False)
            assert selected == ProjectInfo("project-1", root.name, root)
            assert peer.calls == [("project/list", {}), ("project/list", {"cursor": cursor})]

    asyncio.run(scenario())
    assert notes.read_text(encoding="utf-8") == "元の資料"
    assert list(root.iterdir()) == [notes]


@pytest.mark.parametrize("create", [True, False])
def test_selection_registers_one_folder_without_creating_recording_files(
    tmp_path: Path, *, create: bool
) -> None:
    root = tmp_path / "FireRed"
    if not create:
        root.mkdir()

    async def scenario() -> None:
        async with management(tmp_path) as (client, peer):
            peer.responses["project/create"] = {"project": project_data(root)}
            assert await select_project(client, root, create=create) == ProjectInfo(
                "project-1", "FireRed", root
            )
            methods = [method for method, _params in peer.calls]
            assert methods == (["project/create"] if create else ["project/list", "project/create"])
            params = peer.calls[-1][1]
            assert params is not None
            assert params["name"] == "FireRed"
            assert params["roots"] == [{"path": str(root)}]
            assert isinstance(params["idempotencyKey"], str)
            assert params["idempotencyKey"]

    asyncio.run(scenario())
    assert root.is_dir()
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("existing", ["directory", "file"])
def test_creation_rejects_existing_paths_before_registering(tmp_path: Path, existing: str) -> None:
    root = tmp_path / "existing"
    if existing == "directory":
        root.mkdir()
    else:
        root.write_bytes(b"keep")

    async def scenario() -> None:
        async with management(tmp_path) as (client, peer):
            with pytest.raises(FileExistsError):
                await select_project(client, root, create=True)
            assert peer.calls == []

    asyncio.run(scenario())
    if existing == "file":
        assert root.read_bytes() == b"keep"


def test_failed_registration_keeps_new_folder_without_retrying(tmp_path: Path) -> None:
    root = tmp_path / "new"

    async def scenario() -> None:
        async with management(tmp_path) as (client, peer):
            peer.rpc_errors["project/create"] = -32000
            with pytest.raises(RpcError):
                await select_project(client, root, create=True)
            assert [method for method, _params in peer.calls] == ["project/create"]

    asyncio.run(scenario())
    assert root.is_dir()
    assert list(root.iterdir()) == []


def test_workspace_uses_each_public_read_without_a_local_path_cache(tmp_path: Path) -> None:
    first, moved = tmp_path / "first", tmp_path / "moved"
    first.mkdir()
    moved.mkdir()

    async def scenario() -> None:
        async with management(tmp_path) as (client, peer):
            for root in (first, moved):
                peer.responses["project/read"] = {"project": project_data(root)}
                assert await resolve_workspace(client, tmp_path, project_id="project-1") == root
            assert peer.calls == [("project/read", {"projectId": "project-1"})] * 2

    asyncio.run(scenario())
    assert list(first.iterdir()) == list(moved.iterdir()) == []


@pytest.mark.parametrize("source", ["selection", "public-root"])
def test_missing_workspace_is_not_recreated(tmp_path: Path, source: str) -> None:
    missing = tmp_path / "missing"

    async def scenario() -> None:
        async with management(tmp_path) as (client, peer):
            peer.responses["project/read"] = {"project": project_data(missing)}
            operation = (
                select_project(client, missing, create=False)
                if source == "selection"
                else resolve_workspace(client, tmp_path, project_id="project-1")
            )
            with pytest.raises(FileNotFoundError):
                await operation
            assert [method for method, _params in peer.calls] == (
                [] if source == "selection" else ["project/read"]
            )

    asyncio.run(scenario())
    assert not missing.exists()


def test_unassigned_workspaces_are_independent_without_server_or_recording_state(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with management(tmp_path) as (client, peer):
            first = await resolve_workspace(client, tmp_path, project_id=None)
            second = await resolve_workspace(client, tmp_path, project_id=None)
            assert first != second
            for root in (first, second):
                assert root.is_absolute()
                assert root.parent == tmp_path / "unassigned"
                assert root.is_dir()
                assert list(root.iterdir()) == []
            assert peer.calls == []

    asyncio.run(scenario())


def test_slow_directory_creation_does_not_block_the_service_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "slow"
    entered, release = Event(), Event()
    mkdir = Path.mkdir

    def delayed(
        path: Path, mode: int = 0o777, *, parents: bool = False, exist_ok: bool = False
    ) -> None:
        if path == root:
            entered.set()
            if not release.wait(5):
                raise TimeoutError
        mkdir(path, mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", delayed)

    async def scenario() -> None:
        async with management(tmp_path) as (client, peer):
            peer.responses["project/create"] = {"project": project_data(root)}
            selecting = asyncio.create_task(select_project(client, root, create=True))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                assert not selecting.done()
            finally:
                release.set()
                await selecting

    asyncio.run(scenario())
