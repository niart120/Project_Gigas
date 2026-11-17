import asyncio
from pathlib import Path

import pytest

from gigas.runtime.codex.connection import CodexConnectionError
from gigas.runtime.codex.history import history_entries
from gigas.runtime.codex.transport import JsonObject, RpcError
from gigas.services._conversation_history import read_history
from tests.unit.test_codex_management import _client


def _metadata(identifier: str, status: str = "completed") -> JsonObject:
    return {
        "id": identifier,
        "status": status,
        "itemsView": "notLoaded",
        "items": [],
        "error": {"message": "公開された失敗理由"} if status == "failed" else None,
    }


def _body(turn: str, identifier: str) -> JsonObject:
    return {"turnId": turn, "item": {"id": identifier, "type": "agentMessage", "text": identifier}}


def test_status_pages_are_reused_across_split_turn_and_include_empty_turns(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (management, peer):
            peer.responses["thread/items/list"] = {
                "data": [_body("new", "last")],
                "nextCursor": "items-2",
            }
            peer.responses["thread/turns/list"] = {
                "data": [
                    _metadata("new", "failed"),
                    _metadata("empty", "interrupted"),
                    _metadata("old"),
                ],
                "nextCursor": "turns-2",
            }
            first = await read_history(management, "thread")
            assert [entry.text for entry in history_entries("thread", first.content)] == [
                "last",
                "失敗\n公開された失敗理由",
            ]
            assert first.next_cursor is not None
            peer.responses["thread/items/list"] = {
                "data": [_body("new", "earlier"), _body("old", "old-body")],
            }
            second = await read_history(management, "thread", first.next_cursor)
            assert [entry.text for entry in history_entries("thread", second.content)] == [
                "old-body",
                "中断",
                "earlier",
                "失敗\n公開された失敗理由",
            ]
            assert len([m for m, _ in peer.calls if m == "thread/turns/list"]) == 1
            assert second.next_cursor is not None
            assert second.next_cursor.item_cursor is None
            peer.responses["thread/turns/list"] = {"data": [_metadata("tail", "interrupted")]}
            tail = await read_history(management, "thread", second.next_cursor)
            assert [entry.text for entry in history_entries("thread", tail.content)] == ["中断"]
            assert tail.next_cursor is None
            assert len([m for m, _ in peer.calls if m == "thread/items/list"]) == 2
            requests = [p for m, p in peer.calls if m == "thread/turns/list"]
            assert all(p and p["itemsView"] == "notLoaded" and p["limit"] == 25 for p in requests)
            assert requests[-1] is not None
            assert requests[-1]["cursor"] == "turns-2"

    asyncio.run(scenario())


def test_metadata_failure_leaves_combined_cursor_reusable_and_rejects_other_thread(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (management, peer):
            peer.responses["thread/items/list"] = {
                "data": [_body("new", "last")],
                "nextCursor": "items-2",
            }
            peer.responses["thread/turns/list"] = {
                "data": [_metadata("new")],
                "nextCursor": "turns-2",
            }
            first = await read_history(management, "thread")
            cursor = first.next_cursor
            assert cursor is not None
            peer.responses["thread/items/list"] = {"data": [_body("old", "old-body")]}
            peer.rpc_errors["thread/turns/list"] = -32603
            with pytest.raises(RpcError):
                await read_history(management, "thread", cursor)
            assert cursor.item_cursor == "items-2"
            assert cursor.turn_cursor == "turns-2"
            assert [turn.id for turn in cursor.turns] == ["new"]
            del peer.rpc_errors["thread/turns/list"]
            peer.responses["thread/turns/list"] = {"data": [_metadata("old", "failed")]}
            retried = await read_history(management, "thread", cursor)
            assert retried.next_cursor is None
            assert "失敗\n公開された失敗理由" in [
                entry.text for entry in history_entries("thread", retried.content)
            ]
            with pytest.raises(CodexConnectionError, match="別の会話"):
                await read_history(management, "another", cursor)

    asyncio.run(scenario())


def test_empty_thread_still_returns_failed_turns_without_item_requests_on_continuation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (management, peer):
            peer.responses["thread/items/list"] = {"data": []}
            peer.responses["thread/turns/list"] = {
                "data": [_metadata("empty", "failed")],
                "nextCursor": "older",
            }
            first = await read_history(management, "thread")
            assert len(history_entries("thread", first.content)) == 1
            assert first.next_cursor is not None
            peer.responses["thread/turns/list"] = {"data": [_metadata("older", "interrupted")]}
            last = await read_history(management, "thread", first.next_cursor)
            assert last.next_cursor is None
            assert [entry.text for entry in history_entries("thread", last.content)] == ["中断"]
            assert len([m for m, _ in peer.calls if m == "thread/items/list"]) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("next_cursor", [None, "stuck"])
def test_missing_turn_metadata_fails_without_silently_dropping_body(
    tmp_path: Path, next_cursor: str | None
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (management, peer):
            peer.responses["thread/items/list"] = {"data": [_body("missing", "body")]}
            peer.responses["thread/turns/list"] = {
                "data": [_metadata("different")],
                "nextCursor": next_cursor,
            }
            with pytest.raises(CodexConnectionError):
                await read_history(management, "thread")
            requests = [m for m, _ in peer.calls if m == "thread/turns/list"]
            assert len(requests) == (1 if next_cursor is None else 2)

    asyncio.run(scenario())
