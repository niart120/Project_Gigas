"""Combine bounded item pages with independently paged turn metadata."""

from dataclasses import replace
from typing import TYPE_CHECKING

from gigas.runtime.codex.connection import CodexConnectionError
from gigas.runtime.codex.management_models import ItemPage, TurnPage
from gigas.services.conversations import HistoryCursor, HistoryPage

if TYPE_CHECKING:
    from gigas.runtime.codex.management import CodexManagement
    from gigas.runtime.codex.transport import JsonObject

HISTORY_PAGE_ITEMS = 25
HISTORY_PAGE_TURNS = 25


async def read_history(
    management: "CodexManagement", thread_id: str, cursor: HistoryCursor | None = None
) -> HistoryPage:
    """Read one body page and enough metadata to place its turn status lines.

    Args:
        management: Caller-owned open management connection.
        thread_id: Saved conversation to browse without resuming execution.
        cursor: Immutable continuation from the previous successful page.

    Returns:
        Chronologically reconstructable turns and the next continuation. Only
        metadata is retained between pages; item bodies are never cached here.

    Raises:
        CodexConnectionError: The cursor belongs to another conversation or the
            metadata cannot account for the returned item bodies.
        TransportError: A request fails; the supplied cursor remains reusable.
    """
    if cursor is not None and cursor.thread_id != thread_id:
        raise CodexConnectionError("履歴の継続位置が別の会話に属しています")
    items = (
        await management.list_items(
            thread_id,
            cursor=cursor.item_cursor if cursor is not None else None,
            limit=HISTORY_PAGE_ITEMS,
            sort_direction="desc",
        )
        if cursor is None or cursor.item_cursor is not None
        else ItemPage((), None, None)
    )
    turns = list(cursor.turns) if cursor is not None else []
    turn_cursor = cursor.turn_cursor if cursor is not None else None
    oldest = items.items[-1].turn_id if items.items else None
    # On the final body page, include already fetched older empty turns. Further
    # metadata pages remain explicit user requests, not an automatic full scan.
    while cursor is None or turn_cursor is not None:
        if turns and (oldest is None or any(turn.id == oldest for turn in turns)):
            break
        page = await management.list_turns(
            thread_id,
            cursor=turn_cursor,
            limit=HISTORY_PAGE_TURNS,
            sort_direction="desc",
            items_view="notLoaded",
        )
        if page.next_cursor is not None and page.next_cursor == turn_cursor:
            raise CodexConnectionError("turnの履歴取得位置が進みませんでした")
        turns.extend(page.turns)
        turn_cursor = page.next_cursor
        # Stop at exhaustion, or after one metadata-only page.
        if turn_cursor is None or oldest is None:
            break
    end = (
        len(turns)
        if items.next_cursor is None
        else next((i + 1 for i, turn in enumerate(turns) if turn.id == oldest), 0)
    )
    selected = turns[:end]
    bodies: dict[str, list[JsonObject]] = {}
    for wrapper in reversed(items.items):
        bodies.setdefault(wrapper.turn_id, []).append(wrapper.item)
    if set(bodies) - {turn.id for turn in selected}:
        raise CodexConnectionError("履歴本文に対応するturnの状態を取得できませんでした")
    content = tuple(replace(turn, items=tuple(bodies.get(turn.id, ()))) for turn in selected)
    remaining = tuple(turns[max(0, end - 1) :]) if items.next_cursor is not None else ()
    continuation = (
        HistoryCursor(thread_id, items.next_cursor, remaining, turn_cursor)
        if items.next_cursor is not None or turn_cursor is not None
        else None
    )
    return HistoryPage(TurnPage(content, None, None), continuation)
