import asyncio
from pathlib import Path

import pytest
from tests.unit.test_codex_management import _client

from gigas.runtime.codex.connection import CodexConnectionError
from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcError


def _thread(root: Path, project_id: str | None = None) -> JsonObject:
    return {
        "id": "thread-1",
        "name": None,
        "preview": "ラッキーを捕獲して",
        "cwd": str(root),
        "projectId": project_id,
        "createdAt": 10,
        "updatedAt": 20,
        "model": "test-model",
        "reasoningEffort": "high",
        "status": {"type": "notLoaded"},
    }


def _items() -> list[JsonValue]:
    return [
        {"id": "user-1", "type": "userMessage", "content": [{"type": "text", "text": "指示"}]},
        {
            "id": "agent-1",
            "type": "agentMessage",
            "text": "確定本文\n" * 1000,
            "phase": "commentary",
        },
        {"id": "reason-1", "type": "reasoning", "summary": ["公開要約"], "content": []},
        {
            "id": "search-1",
            "type": "webSearch",
            "action": {"type": "search", "query": "ラッキー", "queries": ["ラッキー"]},
            "status": "completed",
            "results": [{"url": "https://example.test/", "title": "図鑑"}],
        },
    ]


@pytest.mark.parametrize("project_id", [None, "project-1"])
def test_thread_list_filters_assignment_and_all_providers_without_execution(
    tmp_path: Path, project_id: str | None
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (client, peer):
            peer.responses["thread/list"] = {
                "data": [_thread(tmp_path, project_id)],
                "nextCursor": "next:/+?",
                "backwardsCursor": "back:/+?",
            }
            page = await client.list_threads(project_id, limit=1, sort_direction="desc")
            assert page.threads[0].project_id == project_id
            assert page.threads[0].preview == "ラッキーを捕獲して"
            assert page.next_cursor == "next:/+?"
            assert page.backwards_cursor == "back:/+?"
            assert [(m, p) for m, p in peer.calls if m.startswith("thread/")] == [
                (
                    "thread/list",
                    {
                        "projectId": project_id,
                        "modelProviders": [],
                        "limit": 1,
                        "sortDirection": "desc",
                    },
                )
            ]
            assert not any(m.startswith(("turn/", "mcp")) for m, _ in peer.calls)

    asyncio.run(scenario())


def test_thread_read_returns_metadata_and_missing_workspace_without_resuming(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (client, peer):
            root = tmp_path / "removed-workspace"
            peer.responses["thread/read"] = {"thread": _thread(root, "project-1")}
            thread = await client.read_thread("thread-1")
            assert thread.id == "thread-1"
            assert thread.cwd == root
            assert thread.name is None
            assert (thread.model, thread.reasoning_effort) == ("test-model", "high")
            assert (thread.created_at, thread.updated_at) == (10, 20)
            assert thread.status == {"type": "notLoaded"}
            assert not root.exists()
            assert [(m, p) for m, p in peer.calls if m.startswith("thread/")] == [
                ("thread/read", {"threadId": "thread-1", "includeTurns": False})
            ]

    asyncio.run(scenario())


def test_global_listing_uses_update_order_and_only_explicit_read_states(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (client, peer):
            peer.responses["thread/list"] = {
                "data": [{**_thread(tmp_path), "id": tid} for tid in ("seen", "unread", "unknown")],
                "readStates": {
                    "seen": {"firstUnread": None, "revision": "r1"},
                    "unread": {"firstUnread": {"type": "threadStart"}, "revision": "r2"},
                },
            }
            page = await client.list_all_threads(
                archived=False, cursor="next", sort_direction="asc"
            )
            assert page.read_ids == {"seen"}
            assert [thread.is_read for thread in page.threads] == [True, False, None]
            assert page.threads[1].unread_revision == "r2"
            assert peer.calls[-1] == (
                "thread/list",
                {
                    "archived": False,
                    "modelProviders": [],
                    "sortKey": "updated_at",
                    "limit": 50,
                    "sortDirection": "asc",
                    "cursor": "next",
                    "useStateDbOnly": True,
                },
            )
            peer.responses["thread/readState/update"] = {
                "readState": {"firstUnread": None, "revision": "r3"}
            }
            await client.mark_read("unread", "r2")
            assert peer.calls[-1] == (
                "thread/readState/update",
                {"threadId": "unread", "expectedRevision": "r2", "operation": {"type": "read"}},
            )

    asyncio.run(scenario())


def test_turn_page_requests_full_items_and_preserves_bodies_and_error_metadata(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (client, peer):
            items = _items()
            error: JsonObject = {"message": "応答が中断", "additionalDetails": "公開詳細"}
            peer.responses["thread/turns/list"] = {
                "data": [
                    {
                        "id": "turn-1",
                        "status": "failed",
                        "items": items,
                        "itemsView": "full",
                        "error": error,
                        "startedAt": 10,
                        "completedAt": 20,
                        "durationMs": 10000,
                        "rootTurnId": "root-1",
                    }
                ],
            }
            page = await client.list_turns("thread-1", limit=1)
            turn = page.turns[0]
            assert (turn.id, turn.status, turn.items_view) == ("turn-1", "failed", "full")
            assert turn.items == tuple(items)
            assert turn.error == error
            assert (turn.started_at, turn.completed_at, turn.duration_ms, turn.root_turn_id) == (
                10,
                20,
                10000,
                "root-1",
            )
            assert page.next_cursor is None
            assert page.backwards_cursor is None
            assert [(m, p) for m, p in peer.calls if m.startswith("thread/")] == [
                ("thread/turns/list", {"threadId": "thread-1", "itemsView": "full", "limit": 1})
            ]

    asyncio.run(scenario())


def test_item_page_preserves_turn_wrapper_optional_times_and_full_items(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (client, peer):
            items = _items()
            peer.responses["thread/items/list"] = {
                "data": [
                    {
                        "turnId": "turn-1",
                        "item": items[1],
                        "startedAtMs": 10001,
                        "completedAtMs": 10002,
                    },
                    {"turnId": "turn-2", "item": items[3]},
                ],
            }
            page = await client.list_items("thread-1")
            first, second = page.items
            assert (first.turn_id, first.item) == ("turn-1", items[1])
            assert (first.started_at_ms, first.completed_at_ms) == (10001, 10002)
            assert (second.turn_id, second.item) == ("turn-2", items[3])
            assert second.started_at_ms is None
            assert second.completed_at_ms is None
            assert [(m, p) for m, p in peer.calls if m.startswith("thread/")] == [
                ("thread/items/list", {"threadId": "thread-1"})
            ]

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["thread/list", "thread/turns/list", "thread/items/list"])
def test_history_pages_pass_cursors_and_direction_without_fetching_more(
    tmp_path: Path, method: str
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (client, peer):
            cursor = "opaque:/+?page=2"
            peer.responses[method] = {"data": [], "nextCursor": cursor, "backwardsCursor": "back"}
            params: JsonObject = {"cursor": cursor, "limit": 0, "sortDirection": "asc"}
            if method == "thread/list":
                page = await client.list_threads(None, cursor=cursor, limit=0, sort_direction="asc")
                params.update({"projectId": None, "modelProviders": []})
            elif method == "thread/turns/list":
                page = await client.list_turns(
                    "thread-1", cursor=cursor, limit=0, sort_direction="asc"
                )
                params.update({"threadId": "thread-1", "itemsView": "full"})
            else:
                page = await client.list_items(
                    "thread-1", turn_id="turn-2", cursor=cursor, limit=0, sort_direction="desc"
                )
                params.update({"threadId": "thread-1", "turnId": "turn-2", "sortDirection": "desc"})
            assert page.next_cursor == cursor
            assert page.backwards_cursor == "back"
            assert [(m, p) for m, p in peer.calls if m.startswith("thread/")] == [(method, params)]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("method", "response"),
    [
        ("thread/list", {}),
        ("thread/read", {"thread": {"id": "thread-1", "cwd": "relative"}}),
        (
            "thread/turns/list",
            {"data": [{"id": "turn-1", "status": "completed", "items": [False]}]},
        ),
        (
            "thread/items/list",
            {"data": [{"turnId": 1, "item": {"id": "item-1", "type": "reasoning"}}]},
        ),
        ("thread/items/list", {"data": [], "backwardsCursor": False}),
    ],
)
def test_invalid_history_response_fails_without_loading_a_thread(
    tmp_path: Path, method: str, response: JsonValue
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (client, peer):
            peer.responses[method] = response
            if method == "thread/list":
                operation = client.list_threads(None)
            elif method == "thread/read":
                operation = client.read_thread("thread-1")
            elif method == "thread/turns/list":
                operation = client.list_turns("thread-1")
            else:
                operation = client.list_items("thread-1")
            with pytest.raises(CodexConnectionError):
                await operation
            assert [m for m, _ in peer.calls if m.startswith("thread/")] == [method]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "method", ["thread/list", "thread/read", "thread/turns/list", "thread/items/list"]
)
def test_history_rpc_failure_is_propagated_without_retry(tmp_path: Path, method: str) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (client, peer):
            peer.rpc_errors[method] = -32602
            if method == "thread/list":
                operation = client.list_threads(None)
            elif method == "thread/read":
                operation = client.read_thread("thread-1")
            elif method == "thread/turns/list":
                operation = client.list_turns("thread-1")
            else:
                operation = client.list_items("thread-1")
            with pytest.raises(RpcError, match="-32602"):
                await operation
            assert [m for m, _ in peer.calls if m.startswith("thread/")] == [method]

    asyncio.run(scenario())
