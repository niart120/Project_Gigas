"""Decode public management responses without selecting or executing a conversation."""

from pathlib import Path
from typing import TYPE_CHECKING

from gigas.runtime.codex.connection import CodexConnectionError
from gigas.runtime.codex.management_models import (
    ItemPage,
    ProjectInfo,
    ProjectPage,
    ThreadInfo,
    ThreadItemInfo,
    ThreadPage,
    TurnInfo,
    TurnPage,
)

if TYPE_CHECKING:
    from gigas.runtime.codex.transport import JsonObject, JsonValue


def project_response(value: "JsonValue") -> ProjectInfo:
    """Decode a create/read project response."""
    return _project(_object(value).get("project"))


def project_page(value: "JsonValue") -> ProjectPage:
    """Decode one project page while preserving the opaque cursor."""
    result = _object(value)
    data = result.get("data")
    if not isinstance(data, list):
        raise CodexConnectionError("CodexのProject一覧の応答形式が不正")
    next_cursor = result.get("nextCursor")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise CodexConnectionError("CodexのProject一覧のcursorが不正")
    return ProjectPage(tuple(_project(value) for value in data), next_cursor)


def thread_response(value: "JsonValue") -> ThreadInfo:
    """Decode metadata from a thread read response."""
    return _thread(_object(value).get("thread"))


def thread_page(value: "JsonValue") -> ThreadPage:
    """Decode a thread page and both opaque cursors."""
    result = _object(value)
    return ThreadPage(
        tuple(_thread(item) for item in _array(result.get("data"))),
        _optional_text(result.get("nextCursor")),
        _optional_text(result.get("backwardsCursor")),
    )


def turn_page(value: "JsonValue") -> TurnPage:
    """Decode a turn page without reducing or interpreting public item payloads."""
    result = _object(value)
    return TurnPage(
        tuple(_turn(item) for item in _array(result.get("data"))),
        _optional_text(result.get("nextCursor")),
        _optional_text(result.get("backwardsCursor")),
    )


def item_page(value: "JsonValue") -> ItemPage:
    """Decode items with their containing turns and both opaque cursors."""
    result = _object(value)
    return ItemPage(
        tuple(_entry(item) for item in _array(result.get("data"))),
        _optional_text(result.get("nextCursor")),
        _optional_text(result.get("backwardsCursor")),
    )


def _object(value: "JsonValue") -> "JsonObject":
    if isinstance(value, dict):
        return value
    raise CodexConnectionError("Codexの管理情報の応答形式が不正")


def _text(value: "JsonValue") -> str:
    if isinstance(value, str):
        return value
    raise CodexConnectionError("Codexの管理情報の応答形式が不正")


def _project(value: "JsonValue") -> ProjectInfo:
    project = _object(value)
    roots = project.get("roots")
    if not isinstance(roots, list) or len(roots) != 1:
        raise CodexConnectionError("CodexのProjectには作業フォルダーが1件必要")
    root = Path(_text(_object(roots[0]).get("path")))
    if not root.is_absolute():
        raise CodexConnectionError("CodexのProjectの作業フォルダーが絶対パスではない")
    return ProjectInfo(_text(project.get("id")), _text(project.get("name")), root)


def _array(value: "JsonValue") -> list["JsonValue"]:
    if isinstance(value, list):
        return value
    raise CodexConnectionError("Codexの履歴応答形式が不正")


def _optional_text(value: "JsonValue") -> str | None:
    return None if value is None else _text(value)


def _integer(value: "JsonValue") -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise CodexConnectionError("Codexの履歴の時刻が不正")


def _optional_integer(value: "JsonValue") -> int | None:
    return None if value is None else _integer(value)


def _thread(value: "JsonValue") -> ThreadInfo:
    thread = _object(value)
    cwd = Path(_text(thread.get("cwd")))
    if not cwd.is_absolute():
        raise CodexConnectionError("Codexの会話の作業フォルダーが絶対パスではない")
    if "projectId" not in thread:
        raise CodexConnectionError("Codexの会話のProject所属が不明")
    return ThreadInfo(
        id=_text(thread.get("id")),
        name=_optional_text(thread.get("name")),
        preview=_text(thread.get("preview")),
        cwd=cwd,
        project_id=_optional_text(thread.get("projectId")),
        created_at=_integer(thread.get("createdAt")),
        updated_at=_integer(thread.get("updatedAt")),
        model=_optional_text(thread.get("model")),
        reasoning_effort=_optional_text(thread.get("reasoningEffort")),
        status=_object(thread.get("status")),
    )


def _item(value: "JsonValue") -> "JsonObject":
    item = _object(value)
    _text(item.get("id"))
    _text(item.get("type"))
    return item


def _turn(value: "JsonValue") -> TurnInfo:
    turn = _object(value)
    view = turn.get("itemsView", "full")
    if not isinstance(view, str) or view not in ("notLoaded", "summary", "full"):
        raise CodexConnectionError("CodexのturnのitemsViewが不正")
    error = turn.get("error")
    return TurnInfo(
        id=_text(turn.get("id")),
        status=_text(turn.get("status")),
        items=tuple(_item(item) for item in _array(turn.get("items"))),
        items_view=view,
        error=None if error is None else _object(error),
        started_at=_optional_integer(turn.get("startedAt")),
        completed_at=_optional_integer(turn.get("completedAt")),
        duration_ms=_optional_integer(turn.get("durationMs")),
        root_turn_id=_optional_text(turn.get("rootTurnId")),
    )


def _entry(value: "JsonValue") -> ThreadItemInfo:
    entry = _object(value)
    return ThreadItemInfo(
        turn_id=_text(entry.get("turnId")),
        item=_item(entry.get("item")),
        started_at_ms=_optional_integer(entry.get("startedAtMs")),
        completed_at_ms=_optional_integer(entry.get("completedAtMs")),
    )
