import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pytest

from gigas.runtime.codex.connection import CodexConnectionError
from gigas.runtime.codex.models import GoalState
from gigas.runtime.codex.transport import RpcError
from gigas.services._conversation_selection import ConversationSelection
from gigas.services.conversations import NewConversationTarget, StoredConversationTarget
from tests.unit.test_codex_connection import _run
from tests.unit.test_codex_history import _items, _thread
from tests.unit.test_codex_management import _client

if TYPE_CHECKING:
    from tests.unit.test_codex_connection import FakePeer


def _saved(peer: "FakePeer", cwd: Path, thread_id: str = "stored-1") -> None:
    peer.responses["thread/read"] = {"thread": {**_thread(cwd, "project-1"), "id": thread_id}}
    peer.responses.setdefault(
        "project/read",
        {"project": {"id": "project-1", "name": "Project", "roots": [{"path": str(cwd)}]}},
    )
    peer.responses["thread/turns/list"] = {
        "data": [
            {
                "id": "turn-1",
                "status": "completed",
                "items": _items(),
                "itemsView": "full",
            }
        ],
        "nextCursor": "next:/+?",
    }
    peer.goal = {
        "objective": "ラッキーを捕獲する",
        "status": "active",
        "tokensUsed": 30,
        "timeUsedSeconds": 2,
        "tokenBudget": 500,
    }


async def _released() -> None:
    pass


class _RequestGate(asyncio.Event):
    """Hold an RPC and expose the exact point where it starts waiting."""

    def __init__(self) -> None:
        """Create independent entry and response events."""
        super().__init__()
        self.entered = asyncio.Event()

    async def wait(self) -> Literal[True]:
        """Notify the test when the peer reaches its response gate."""
        self.entered.set()
        return await super().wait()


@pytest.mark.parametrize("project_id", [None, "project-1"])
def test_new_selection_retains_only_assignment_without_creating_workspace(
    tmp_path: Path, project_id: str | None
) -> None:
    async def scenario() -> None:
        selection = ConversationSelection()
        assert selection.target == NewConversationTarget()
        releases = []

        async def release() -> None:
            releases.append(selection.target)

        target = await selection.new(project_id, release=release)
        assert target == NewConversationTarget(project_id)
        assert selection.target == target
        assert selection.thread is None
        assert selection.goal is None
        assert releases == [NewConversationTarget()]

    _run(scenario())
    assert list(tmp_path.iterdir()) == []


def test_selection_reads_full_first_page_and_active_goal_without_execution(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (management, peer):
            cwd = tmp_path / "missing-workspace"
            _saved(peer, cwd)
            selection = ConversationSelection()
            releases = []

            async def release() -> None:
                releases.append(selection.target)
                assert len([m for m, _ in peer.calls if m.startswith("thread/")]) == 3

            result = await selection.select(management, "stored-1", release=release)
            assert selection.target == StoredConversationTarget("stored-1", cwd, "project-1")
            assert selection.thread == result.thread
            assert (
                selection.goal
                == result.goal
                == GoalState("ラッキーを捕獲する", "active", 30, 2, 500)
            )
            assert result.history.turns[0].items == tuple(_items())
            assert result.history.next_cursor == "next:/+?"
            assert releases == [NewConversationTarget()]
            assert not selection.busy
            assert not cwd.exists()
            assert [(m, p) for m, p in peer.calls if m.startswith("thread/")] == [
                ("thread/read", {"threadId": "stored-1", "includeTurns": False}),
                ("thread/goal/get", {"threadId": "stored-1"}),
                ("thread/turns/list", {"threadId": "stored-1", "itemsView": "full"}),
            ]
            assert not any(m.startswith(("turn/", "mcp")) for m, _ in peer.calls)

    _run(scenario())


@pytest.mark.parametrize(
    "failed", ["thread/read", "thread/goal/get", "thread/turns/list", "release"]
)
def test_failed_selection_keeps_previous_target_metadata_and_goal(
    tmp_path: Path, failed: str
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (management, peer):
            selection = ConversationSelection()
            _saved(peer, tmp_path / "old")
            previous = await selection.select(management, "stored-1", release=_released)
            target = selection.target
            _saved(peer, tmp_path / "new", "stored-2")
            release_called = False

            async def release() -> None:
                nonlocal release_called
                release_called = True
                raise CodexConnectionError("解放失敗")

            if failed != "release":
                peer.rpc_errors[failed] = -32602
            with pytest.raises((RpcError, CodexConnectionError)):
                await selection.select(management, "stored-2", release=release)
            assert release_called == (failed == "release")
            assert selection.target == target
            assert selection.thread == previous.thread
            assert selection.goal == previous.goal
            assert not selection.busy

    _run(scenario())


def test_selection_rejects_second_selection_until_initial_history_finishes(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (management, peer):
            selection = ConversationSelection()
            _saved(peer, tmp_path / "stored")
            peer.gates["thread/turns/list"] = gate = _RequestGate()
            pending = asyncio.create_task(
                selection.select(management, "stored-1", release=_released)
            )
            await gate.entered.wait()
            assert selection.busy
            assert selection.target == NewConversationTarget()
            with pytest.raises(CodexConnectionError):
                await selection.new("project-2", release=_released)
            with pytest.raises(CodexConnectionError):
                await selection.select(management, "stored-2", release=_released)
            assert len([m for m, _ in peer.calls if m == "thread/read"]) == 1
            gate.set()
            await pending
            assert not selection.busy

    _run(scenario())


def test_cancelled_selection_keeps_old_target_and_releases_the_operation_slot(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (management, peer):
            selection = ConversationSelection()
            await selection.new("project-old", release=_released)
            _saved(peer, tmp_path / "stored")
            peer.gates["thread/read"] = gate = _RequestGate()
            pending = asyncio.create_task(
                selection.select(management, "stored-1", release=_released)
            )
            await gate.entered.wait()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert selection.target == NewConversationTarget("project-old")
            assert not selection.busy
            assert await selection.new(None, release=_released) == NewConversationTarget()

    _run(scenario())


def test_extra_history_returns_its_target_and_cursor_without_blocking_selection(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (management, peer):
            selection = ConversationSelection()
            _saved(peer, tmp_path / "stored")
            first = await selection.select(management, "stored-1", release=_released)
            old_target = selection.target
            peer.calls.clear()
            peer.gates["thread/turns/list"] = gate = _RequestGate()
            pending = asyncio.create_task(
                selection.history(management, cursor=first.history.next_cursor)
            )
            await gate.entered.wait()
            assert not selection.busy
            await selection.new(None, release=_released)
            gate.set()
            result = await pending
            assert result.target == old_target
            assert result.page.next_cursor == "next:/+?"
            assert selection.target == NewConversationTarget()
            assert selection.thread is None
            assert selection.goal is None
            assert peer.calls == [
                (
                    "thread/turns/list",
                    {"threadId": "stored-1", "itemsView": "full", "cursor": "next:/+?"},
                )
            ]

    _run(scenario())


def test_new_target_has_no_history_and_started_target_needs_no_metadata_read(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (management, peer):
            selection = ConversationSelection()
            with pytest.raises(CodexConnectionError):
                await selection.history(management)
            selection.adopt_started("started-1", tmp_path / "workspace", "project-1")
            assert selection.target == StoredConversationTarget(
                "started-1", tmp_path / "workspace", "project-1"
            )
            assert selection.thread is None
            assert selection.goal is None
            assert not any(m.startswith("thread/") for m, _ in peer.calls)

    _run(scenario())
