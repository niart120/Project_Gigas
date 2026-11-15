import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from gigas.runtime.codex.connection import CodexConnection, CodexConnectionError
from gigas.runtime.codex.management import ProjectInfo
from gigas.runtime.codex.models import ConnectionOptions, ExecutionSettings
from gigas.runtime.codex.transport import JsonObject, JsonValue, TransportError
from gigas.services._project_management import ProjectManagement
from tests.unit.test_codex_connection import FakePeer
from tests.unit.test_codex_history import _thread


class ProjectPeer(FakePeer):
    """Keep server metadata through interrupted requests and new coordinators."""

    def __init__(self, root: Path, *, count: int = 3) -> None:
        """Create paginated members with independent stored workspace identities."""
        super().__init__()
        self.project = ProjectInfo("project-1", "Original", root)
        self.members = {
            f"thread-{i}": {**_thread(root, "project-1"), "id": f"thread-{i}"} for i in range(count)
        }
        self.archived: set[str] = set()
        self.active_goal: str | None = None
        self.reject_thread: str | None = None
        self.drop_response: str | None = None
        self.deleted = False
        self.drop_delete_response = False
        self.fail_second_page = False
        self.responses["thread/turns/list"] = {"data": []}

    def metadata(self) -> JsonObject:
        """Return the current public project response."""
        return {
            "id": self.project.id,
            "name": self.project.name,
            "roots": [{"path": str(self.project.root)}],
        }

    async def request(self, method: str, params: JsonObject | None) -> JsonValue:
        """Apply requests to persistent fixture metadata before optional delivery failure."""
        if params is None or method in self.gates or method in self.rpc_errors:
            return await super().request(method, params)
        if method == "project/read":
            self.responses[method] = {"project": self.metadata()}
        elif method == "project/update":
            roots = params.get("roots")
            root = self.project.root
            if isinstance(roots, list) and isinstance(roots[0], dict):
                root = Path(str(roots[0]["path"]))
            self.project = replace(self.project, name=str(params["name"]), root=root)
            self.responses[method] = {"project": self.metadata()}
        elif method == "project/delete":
            self.deleted = True
            for member in self.members.values():
                member["projectId"] = None
            self.responses[method] = {}
            if self.drop_delete_response:
                self.calls.append((method, params))
                message = "Delete committed but response lost"
                raise TransportError(message)
        elif method == "project/list":
            self.responses[method] = {"data": [] if self.deleted else [self.metadata()]}
        elif method == "thread/list":
            rows = [
                m
                for key, m in self.members.items()
                if (key in self.archived) == bool(params.get("archived"))
                and m.get("projectId") == params.get("projectId")
            ]
            index = int(str(params.get("cursor", "0")))
            if index and self.fail_second_page:
                message = "Private upstream text"
                raise TransportError(message)
            data: list[JsonValue] = list(rows[index : index + 1])
            self.responses[method] = {
                "data": data,
                "nextCursor": str(index + 1) if index + 1 < len(rows) else None,
            }
        elif method == "thread/read":
            self.responses[method] = {"thread": dict(self.members[str(params["threadId"])])}
        elif method == "thread/goal/get":
            self.responses[method] = {
                "goal": {
                    "objective": "Keep playing",
                    "status": "active",
                    "tokensUsed": 4,
                    "timeUsedSeconds": 3,
                    "tokenBudget": 100,
                }
                if params["threadId"] == self.active_goal
                else None
            }
        elif method == "thread/unsubscribe":
            self.responses[method] = {"status": "unsubscribed"}
        elif method == "thread/resume":
            tid = str(params["threadId"])
            if tid == self.reject_thread:
                message = "Private upstream text"
                raise TransportError(message)
            self.members[tid]["cwd"] = params["cwd"]
            self.responses[method] = {
                "thread": dict(self.members[tid]),
                "cwd": params["cwd"],
                "runtimeWorkspaceRoots": params["runtimeWorkspaceRoots"],
                "sandbox": {"type": "readOnly", "networkAccess": False},
            }
            if tid == self.drop_response:
                self.calls.append((method, params))
                message = "Response lost after server commit"
                raise TransportError(message)
        return await super().request(method, params)


async def coordinator(
    peer: ProjectPeer, root: Path
) -> tuple[ProjectManagement, CodexConnection, list[str]]:
    connection = CodexConnection(peer, ConnectionOptions(root), lambda _: None)
    await connection.open()
    released = []

    async def release() -> None:
        released.append("released")

    return (
        ProjectManagement(
            connection, check=lambda: None, release=release, settings=ExecutionSettings()
        ),
        connection,
        released,
    )


def test_folder_update_preserves_ids_and_moves_all_pages_without_turns(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path / "new"
        root.mkdir()
        peer = ProjectPeer(tmp_path)
        manager, connection, released = await coordinator(peer, tmp_path)
        try:
            details = await manager.update(peer.project, name="Changed", root=root)
            assert details.project == ProjectInfo("project-1", "Changed", root)
            assert len(details.threads) == 3
            assert not details.pending
            assert {t.id for t in details.threads} == set(peer.members)
            assert released == ["released"]
            assert not any(method.startswith("turn/") for method, _ in peer.calls)
            resumes = [params for method, params in peer.calls if method == "thread/resume"]
            assert len(resumes) == 3
            assert all(p is not None and p["sandbox"] == "read-only" for p in resumes)
            assert all(p is not None and p["runtimeWorkspaceRoots"] == [str(root)] for p in resumes)
            assert not (root / "records").exists()
        finally:
            await connection.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("lost_response", [False, True])
def test_partial_update_can_be_reconciled_by_a_new_coordinator(
    tmp_path: Path, lost_response: bool
) -> None:
    async def scenario() -> None:
        root = tmp_path / "new"
        root.mkdir()
        peer = ProjectPeer(tmp_path)
        if lost_response:
            peer.drop_response = "thread-1"
        else:
            peer.reject_thread = "thread-1"
        manager, connection, _ = await coordinator(peer, tmp_path)
        try:
            with pytest.raises(CodexConnectionError, match="完了を確認"):
                await manager.update(peer.project, name="Original", root=root)
            assert peer.project.root == root
            assert peer.members["thread-0"]["cwd"] == str(root)
            assert peer.members["thread-2"]["cwd"] == str(tmp_path)
            before = len(peer.calls)
            peer.drop_response = peer.reject_thread = None
            fresh = ProjectManagement(
                connection,
                check=lambda: None,
                release=manager.release,
                settings=ExecutionSettings(),
            )
            final = await fresh.update(peer.project, name="Original", root=root)
            assert not final.pending
            resumed = [
                p["threadId"]
                for method, p in peer.calls[before:]
                if method == "thread/resume" and p
            ]
            assert resumed == (["thread-2"] if lost_response else ["thread-1", "thread-2"])
        finally:
            await connection.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["archived", "active_goal", "second_page"])
def test_preflight_failure_does_not_change_registration_or_release_resources(
    tmp_path: Path, reason: str
) -> None:
    async def scenario() -> None:
        root = tmp_path / "new"
        root.mkdir()
        peer = ProjectPeer(tmp_path)
        if reason == "archived":
            peer.archived.add("thread-1")
        elif reason == "active_goal":
            peer.active_goal = "thread-1"
        else:
            peer.fail_second_page = True
        original = peer.project
        manager, connection, released = await coordinator(peer, tmp_path)
        try:
            with pytest.raises((CodexConnectionError, TransportError)):
                await manager.update(original, name="Changed", root=root)
            assert peer.project == original
            assert not released
            assert not any(
                method in {"thread/resume", "project/update"} for method, _ in peer.calls
            )
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_retry_with_a_name_change_finishes_both_name_and_workspaces(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path / "new"
        root.mkdir()
        peer = ProjectPeer(tmp_path)
        peer.project = replace(peer.project, root=root)
        manager, connection, _ = await coordinator(peer, tmp_path)
        try:
            result = await manager.update(peer.project, name="New name", root=root)
            assert result.project.name == "New name"
            assert not result.pending
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_pending_update_rejects_another_destination_before_writing(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = ProjectPeer(tmp_path / "first-destination")
        peer.members["thread-0"]["cwd"] = str(tmp_path / "original")
        root = tmp_path / "another-destination"
        root.mkdir()
        manager, connection, released = await coordinator(peer, tmp_path)
        try:
            with pytest.raises(CodexConnectionError):
                await manager.update(peer.project, name="Changed", root=root)
            assert not released
            assert not any(method == "project/update" for method, _ in peer.calls)
        finally:
            await connection.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["release", "registration_ack", "final_check"])
def test_failure_stops_mutations_without_rolling_back(tmp_path: Path, stage: str) -> None:
    async def scenario() -> None:
        peer = ProjectPeer(tmp_path)
        root = tmp_path / "new"
        root.mkdir()
        manager, connection, _ = await coordinator(peer, tmp_path)
        original = peer.request
        written = False

        async def request(method: str, params: JsonObject | None) -> JsonValue:
            nonlocal written
            if stage == "final_check" and written and method == "project/read":
                message = "Final read unavailable"
                raise TransportError(message)
            value = await original(method, params)
            if method == "project/update":
                written = True
                if stage == "registration_ack":
                    message = "Committed response lost"
                    raise TransportError(message)
            return value

        async def release() -> None:
            message = "Writer could not be recovered"
            raise CodexConnectionError(message)

        peer.request = request
        if stage == "release":
            manager.release = release
        try:
            with pytest.raises(CodexConnectionError):
                await manager.update(peer.project, name="Changed", root=root)
            assert sum(method == "project/update" for method, _ in peer.calls) == (
                0 if stage == "release" else 1
            )
            assert (peer.project.root == root) == (stage != "release")
            assert all(member["cwd"] == str(root) for member in peer.members.values()) == (
                stage == "final_check"
            )
            assert not any(method.startswith("turn/") for method, _ in peer.calls)
        finally:
            await connection.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("count", [0, 3])
@pytest.mark.parametrize("lost_response", [False, True])
def test_delete_missing_root_keeps_threads_without_loading_them(
    tmp_path: Path, lost_response: bool, count: int
) -> None:
    async def scenario() -> None:
        peer = ProjectPeer(tmp_path / "missing", count=count)
        peer.drop_delete_response = lost_response
        manager, connection, released = await coordinator(peer, tmp_path)
        try:
            result = await manager.delete(peer.project)
            assert result.project_id == "project-1"
            assert peer.deleted
            assert all(t["projectId"] is None for t in peer.members.values())
            assert released == ["released"]
            assert not any(method == "thread/resume" for method, _ in peer.calls)
            assert sum(method == "project/delete" for method, _ in peer.calls) == 1
        finally:
            await connection.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("archived", [False, True])
def test_rename_missing_folder_does_not_load_members_or_release_resources(
    tmp_path: Path, archived: bool
) -> None:
    async def scenario() -> None:
        peer = ProjectPeer(tmp_path / "missing")
        if archived:
            peer.archived.add("thread-1")
        manager, connection, released = await coordinator(peer, tmp_path)
        try:
            result = await manager.update(peer.project, name="Renamed", root=peer.project.root)
            assert result.project.name == "Renamed"
            assert result.directory_status == "missing"
            assert not released
            assert not any(method == "thread/resume" for method, _ in peer.calls)
            assert bool(result.archived) == archived
        finally:
            await connection.close()

    asyncio.run(scenario())
