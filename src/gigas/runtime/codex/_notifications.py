"""Public item conversion and unfinished content belonging to one accepted turn."""

import json
from contextlib import suppress
from typing import TYPE_CHECKING

from gigas.runtime.codex.models import (
    ActivityEvent,
    DialogueEvent,
    PlanEvent,
    PlanStep,
    ReasoningEvent,
    TextEvent,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from gigas.runtime.codex.models import ConnectionEvent
    from gigas.runtime.codex.transport import JsonObject, JsonValue


def _object(value: "JsonValue") -> "JsonObject":
    if isinstance(value, dict):
        return value
    message = "Codexの応答形式が不正"
    raise ValueError(message)


def _text(value: "JsonValue") -> str:
    if isinstance(value, str) and value:
        return value
    message = "Codexの応答形式が不正"
    raise ValueError(message)


def file_change_details(value: "JsonValue") -> str:
    """Format the complete file-change description shared by display and approval.

    Raises:
        ValueError: A change lacks a supported action, path, diff or move target.
    """
    if not isinstance(value, list) or not value:
        message = "ファイル変更の確認対象が不足"
        raise ValueError(message)
    details: list[str] = []
    for raw in value:
        change = _object(raw)
        path = _text(change.get("path"))
        diff = change.get("diff")
        kind = _object(change.get("kind"))
        action = kind.get("type")
        target = kind.get("move_path")
        if (
            action not in ("add", "delete", "update")
            or not isinstance(diff, str)
            or (target is not None and not isinstance(target, str))
        ):
            message = "ファイル変更の形式に未対応"
            raise ValueError(message)
        details.append(f"{action}: {path}")
        if isinstance(target, str):
            details.append(f"移動先: {target}")
        details.append(diff)
    return "\n".join(details)


def _plan(turn_id: str, params: "JsonObject") -> PlanEvent | None:
    plan = params.get("plan")
    if not isinstance(plan, list):
        return None
    steps: list[PlanStep] = []
    for raw in plan:
        step = _object(raw)
        status = step.get("status")
        if not isinstance(status, str) or status not in ("pending", "inProgress", "completed"):
            return None
        steps.append(PlanStep(_text(step.get("step")), status))
    explanation = params.get("explanation")
    return PlanEvent(turn_id, tuple(steps), explanation if isinstance(explanation, str) else "")


def _mcp_text(text: str) -> str:
    # The pinned runtime may wrap the whole MCP result in one textual envelope.
    try:
        result = json.loads(text)
    except (ValueError, RecursionError):
        return text
    if (
        isinstance(result, dict)
        and isinstance(result.get("content"), list)
        and type(result.get("isError")) is bool
    ):
        return "\n".join(
            block["text"]
            for block in result["content"]
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
    return text


def _activity(turn_id: str, item_id: str, item: "JsonObject") -> ActivityEvent | None:
    kind, status = item.get("type"), item.get("status")
    if (
        not isinstance(kind, str)
        or not isinstance(status, str)
        or kind not in ("commandExecution", "fileChange", "mcpToolCall")
        or status not in ("inProgress", "completed", "failed", "declined")
    ):
        return None
    details: list[str] = []
    if kind == "commandExecution":
        title = _text(item.get("command"))
        for key in ("cwd", "aggregatedOutput"):
            value = item.get(key)
            if isinstance(value, str) and value:
                details.append(value)
        exit_code = item.get("exitCode")
        if type(exit_code) is int:
            details.append(f"終了コード: {exit_code}")
    elif kind == "fileChange":
        changes = item.get("changes")
        if not isinstance(changes, list):
            return None
        details.append(file_change_details(changes))
        title = "\n".join(_text(_object(change).get("path")) for change in changes)
    else:
        title = f"{_text(item.get('server'))}/{_text(item.get('tool'))}"
        result = item.get("result")
        if isinstance(result, dict) and isinstance(content := result.get("content"), list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str):
                        details.append(_mcp_text(text))
        error = item.get("error")
        if isinstance(error, dict) and isinstance(message := error.get("message"), str):
            details.append(message)
    return ActivityEvent(turn_id, item_id, kind, title, status, "\n".join(details))


class TurnContent:
    """Accumulate only the public summaries and unfinished records of one turn."""

    def __init__(self, turn_id: str) -> None:
        """Create empty content for an already accepted turn."""
        self._turn_id = turn_id
        self._summaries: dict[str, dict[int, str]] = {}
        self._pending: dict[str, JsonObject] = {}

    def accept(self, method: str, params: "JsonObject") -> "Iterator[ConnectionEvent]":
        """Convert a notification already matched to this turn by the connection.

        Malformed optional display data is ignored. Message identity failures
        remain protocol failures for the connection to report.

        Raises:
            ValueError: A message or item envelope has no usable identity or shape.
        """
        with suppress(ValueError):
            yield from self._public(method, params)
        if method == "item/agentMessage/delta":
            delta = params.get("delta")
            if isinstance(delta, str):
                item_id = _text(params.get("itemId"))
                yield TextEvent(item_id, delta)
                previous = self._pending.get(item_id, {}).get("text", "")
                self._pending[item_id] = {
                    "item_id": item_id,
                    "kind": "message",
                    "text": str(previous) + delta,
                }
        elif method in {"item/started", "item/completed"}:
            item = _object(params.get("item"))
            text = item.get("text")
            if (
                item.get("type") == "agentMessage"
                and method == "item/completed"
                and isinstance(text, str)
            ):
                item_id = _text(item.get("id"))
                yield TextEvent(item_id, text, replace=True)
                yield self._complete(item_id, {"item_id": item_id, "kind": "message", "text": text})

    def _public(self, method: str, params: "JsonObject") -> "Iterator[ConnectionEvent]":
        if method == "turn/plan/updated":
            if (plan := _plan(self._turn_id, params)) is not None:
                yield plan
        elif method == "item/reasoning/summaryTextDelta":
            index, delta = params.get("summaryIndex"), params.get("delta")
            if type(index) is not int or index < 0 or not isinstance(delta, str):
                return
            item_id = _text(params.get("itemId"))
            summary = self._summaries.setdefault(item_id, {})
            summary[index] = summary.get(index, "") + delta
            paragraphs = tuple(summary[i] for i in sorted(summary))
            yield ReasoningEvent(self._turn_id, item_id, paragraphs)
            self._pending[item_id] = {
                "item_id": item_id,
                "kind": "reasoning",
                "summary": list(paragraphs),
            }
        elif method in {"item/started", "item/completed"}:
            item = _object(params.get("item"))
            item_id = _text(item.get("id"))
            if item.get("type") == "reasoning" and method == "item/completed":
                raw_summary = item.get("summary", [])
                if not isinstance(raw_summary, list):
                    return
                summary = {}
                for index, paragraph in enumerate(raw_summary):
                    if not isinstance(paragraph, str):
                        return
                    summary[index] = paragraph
                self._summaries[item_id] = summary
                yield ReasoningEvent(self._turn_id, item_id, tuple(summary.values()))
                yield self._complete(
                    item_id,
                    {"item_id": item_id, "kind": "reasoning", "summary": list(summary.values())},
                )
            elif (activity := _activity(self._turn_id, item_id, item)) is not None:
                yield activity
                snapshot: JsonObject = {
                    "item_id": item_id,
                    "kind": "activity",
                    "type": activity.kind,
                    "title": activity.title,
                    "status": activity.status,
                    "details": activity.details,
                }
                if method == "item/completed":
                    yield self._complete(item_id, snapshot)
                else:
                    self._pending[item_id] = snapshot

    def _complete(self, item_id: str, payload: "JsonObject") -> DialogueEvent:
        self._pending.pop(item_id, None)
        return DialogueEvent(str(payload["kind"]), {**payload, "partial": False}, self._turn_id)

    def finish(self) -> list[DialogueEvent]:
        """Discard accumulation and return each unfinished snapshot exactly once."""
        records = [
            DialogueEvent(str(payload["kind"]), {**payload, "partial": True}, self._turn_id)
            for payload in self._pending.values()
        ]
        self._summaries.clear()
        self._pending.clear()
        return records
