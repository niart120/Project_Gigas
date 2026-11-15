"""Public item conversion and unfinished content belonging to one accepted turn."""

import json
from contextlib import suppress
from copy import deepcopy
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from gigas.runtime.codex.models import (
    ActivityEvent,
    DialogueEvent,
    PlanEvent,
    PlanStep,
    ReasoningEvent,
    SummaryPart,
    TextEvent,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from gigas.runtime.codex.models import ConnectionEvent, MessagePhase
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


def message_phase(value: "JsonValue") -> "MessagePhase | None":
    """Keep a recognized public phase without guessing from text or turn state."""
    if isinstance(value, str) and value in ("commentary", "partial_answer", "final_answer"):
        return value
    return None


def summary_parts(value: "JsonValue") -> tuple[SummaryPart, ...]:
    """Read an authoritative public summary without shifting paragraph indices.

    Raises:
        ValueError: The summary is not an array of strings.
    """
    if not isinstance(value, list) or any(not isinstance(part, str) for part in value):
        message = "公開推論要約の形式が不正"
        raise ValueError(message)
    return tuple(
        SummaryPart(index, part) for index, part in enumerate(value) if isinstance(part, str)
    )


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


def _web_details(item: "JsonObject") -> tuple[str, str]:
    query = item.get("query")
    lines = [query] if isinstance(query, str) and query else []
    action = item.get("action")
    if isinstance(action, dict):
        for key in ("query", "url", "pattern"):
            value = action.get(key)
            if isinstance(value, str) and value:
                lines.append(value)
        queries = action.get("queries")
        if isinstance(queries, list):
            lines.extend(value for value in queries if isinstance(value, str) and value)
    lines = list(dict.fromkeys(lines))
    title = lines.pop(0) if lines else "Web検索"
    results = item.get("results")
    if isinstance(results, list) and results:
        # Public results deliberately have no provider-specific schema.
        lines.append(json.dumps(results, ensure_ascii=False, indent=2))
    return title, "\n".join(lines)


def item_activity(
    turn_id: str,
    item_id: str,
    item: "JsonObject",
    *,
    web_status: Literal["inProgress", "completed"] | None = None,
    completed: bool | None = None,
) -> ActivityEvent | None:
    """Convert a public activity item without requests or recording side effects.

    Args:
        turn_id: Containing public turn identity.
        item_id: Public item identity within that turn.
        item: Public item body; unrelated and unsupported types are ignored.
        web_status: Received notification lifecycle for Web search, or None for
            saved items, whose public payload has no lifecycle status.
        completed: Received item lifecycle, or None when unavailable in saved history.

    Returns:
        One display snapshot, or None for unsupported or incomplete activity data.

    Raises:
        ValueError: A supported activity has malformed required display fields.
    """
    kind, status = item.get("type"), item.get("status")
    if kind == "webSearch":
        title, details = _web_details(item)
        return ActivityEvent(
            turn_id, item_id, "webSearch", title, web_status, details, completed=completed
        )
    if (
        not isinstance(kind, str)
        or not isinstance(status, str)
        or kind not in ("commandExecution", "fileChange", "mcpToolCall")
        or status not in ("inProgress", "completed", "failed", "declined")
    ):
        return None
    details: list[str] = []
    cwd = None
    exit_code = None
    arguments = None
    if kind == "commandExecution":
        title = _text(item.get("command"))
        raw_cwd, output, raw_exit = (
            item.get("cwd"),
            item.get("aggregatedOutput"),
            item.get("exitCode"),
        )
        cwd = raw_cwd if isinstance(raw_cwd, str) else None
        exit_code = raw_exit if type(raw_exit) is int else None
        if isinstance(output, str):
            details.append(output)
    elif kind == "fileChange":
        changes = item.get("changes")
        if not isinstance(changes, list):
            return None
        details.append(file_change_details(changes))
        title = "\n".join(_text(_object(change).get("path")) for change in changes)
    else:
        title = f"{_text(item.get('server'))}/{_text(item.get('tool'))}"
        arguments = deepcopy(item.get("arguments"))
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
    return ActivityEvent(
        turn_id,
        item_id,
        kind,
        title,
        status,
        "\n".join(details),
        cwd,
        exit_code,
        arguments,
        completed,
    )


type _Content = TextEvent | ReasoningEvent | ActivityEvent


def _dialogue(event: _Content, *, partial: bool) -> DialogueEvent:
    payload: JsonObject = {"item_id": event.item_id, "partial": partial}
    if isinstance(event, TextEvent):
        kind = "message"
        payload["text"] = event.text
        if event.phase is not None:
            payload["phase"] = event.phase
    elif isinstance(event, ReasoningEvent):
        kind = "reasoning"
        payload["summary"] = [{"index": part.index, "text": part.text} for part in event.summary]
    else:
        kind = "activity"
        payload.update(
            {
                "type": event.kind,
                "title": event.title,
                "status": event.status,
                "details": event.details,
                "cwd": event.cwd,
                "exit_code": event.exit_code,
                "arguments": deepcopy(event.arguments),
            }
        )
    payload["kind"] = kind
    return DialogueEvent(kind, payload, event.turn_id)


class TurnContent:
    """Accumulate only public, unfinished snapshots of one accepted turn."""

    def __init__(self, turn_id: str) -> None:
        """Create content for an accepted turn, independent of UI and storage."""
        self._turn_id = turn_id
        self._pending: dict[str, _Content] = {}
        self._completed: set[str] = set()
        self._finished = False

    def accept(self, method: str, params: "JsonObject") -> "Iterator[ConnectionEvent]":
        """Convert a notification already matched to this turn by the connection.

        Optional malformed display data is ignored. Completed items and finished
        turns reject late deltas; item identity errors remain protocol failures.

        Raises:
            ValueError: A message or item envelope has no usable identity or shape.
        """
        if self._finished:
            return
        item = _object(params.get("item")) if method in {"item/started", "item/completed"} else None
        identity = item.get("id") if item is not None else params.get("itemId")
        if isinstance(identity, str) and identity in self._completed:
            return
        if method == "item/agentMessage/delta":
            delta = params.get("delta")
            if isinstance(delta, str):
                item_id = _text(identity)
                previous = self._pending.get(item_id)
                phase = previous.phase if isinstance(previous, TextEvent) else None
                text = previous.text if isinstance(previous, TextEvent) else ""
                event = TextEvent(item_id, delta, turn_id=self._turn_id, phase=phase)
                self._pending[item_id] = replace(event, text=text + delta, replace=True)
                yield event
        elif item is not None and item.get("type") == "agentMessage":
            if isinstance(text := item.get("text"), str):
                yield from self._publish(
                    TextEvent(
                        _text(identity),
                        text,
                        replace=True,
                        turn_id=self._turn_id,
                        phase=message_phase(item.get("phase")),
                        completed=method == "item/completed",
                    )
                )
        else:
            with suppress(ValueError):
                yield from self._public(method, params, item)

    def _public(
        self, method: str, params: "JsonObject", item: "JsonObject | None"
    ) -> "Iterator[ConnectionEvent]":
        if method == "turn/plan/updated":
            if (plan := _plan(self._turn_id, params)) is not None:
                yield plan
        elif method == "item/reasoning/summaryTextDelta":
            index, delta = params.get("summaryIndex"), params.get("delta")
            if type(index) is not int or index < 0 or not isinstance(delta, str) or not delta:
                return
            item_id = _text(params.get("itemId"))
            previous = self._pending.get(item_id)
            summary = (
                {part.index: part.text for part in previous.summary}
                if isinstance(previous, ReasoningEvent)
                else {}
            )
            summary[index] = summary.get(index, "") + delta
            yield from self._publish(
                ReasoningEvent(
                    self._turn_id,
                    item_id,
                    tuple(SummaryPart(i, summary[i]) for i in sorted(summary)),
                )
            )
        elif method in {"item/commandExecution/outputDelta", "item/mcpToolCall/progress"}:
            item_id = _text(params.get("itemId"))
            previous = self._pending.get(item_id)
            if not isinstance(previous, ActivityEvent):
                return
            if (
                method == "item/commandExecution/outputDelta"
                and previous.kind == "commandExecution"
            ):
                if isinstance(delta := params.get("delta"), str) and delta:
                    yield from self._publish(replace(previous, details=previous.details + delta))
            elif (
                method == "item/mcpToolCall/progress"
                and previous.kind == "mcpToolCall"
                and isinstance(message := params.get("message"), str)
            ):
                yield from self._publish(replace(previous, details=message))
        elif item is not None:
            item_id = _text(item.get("id"))
            completed = method == "item/completed"
            if item.get("type") == "reasoning":
                yield from self._publish(
                    ReasoningEvent(
                        self._turn_id,
                        item_id,
                        summary_parts(item.get("summary", [])),
                        completed=completed,
                    )
                )
            else:
                activity = item_activity(
                    self._turn_id,
                    item_id,
                    item,
                    web_status="completed" if completed else "inProgress",
                    completed=completed,
                )
                if activity is not None:
                    previous = self._pending.get(item_id)
                    if (
                        completed
                        and activity.kind == "commandExecution"
                        and not isinstance(item.get("aggregatedOutput"), str)
                        and isinstance(previous, ActivityEvent)
                        and previous.kind == activity.kind
                    ):
                        activity = replace(activity, details=previous.details)
                    yield from self._publish(activity)

    def _publish(self, event: _Content) -> "Iterator[ConnectionEvent]":
        if event.completed:
            self._pending.pop(event.item_id, None)
            self._completed.add(event.item_id)
        else:
            self._pending[event.item_id] = event
        yield event
        if event.completed:
            yield _dialogue(event, partial=False)

    def finish(self) -> list[DialogueEvent]:
        """Close the turn and return each unfinished snapshot exactly once."""
        self._finished = True
        records = [_dialogue(event, partial=True) for event in self._pending.values()]
        self._pending.clear()
        self._completed.clear()
        return records
