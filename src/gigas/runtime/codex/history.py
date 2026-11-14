"""Side-effect-free presentation of public saved conversation items."""

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from gigas.runtime.codex._notifications import item_activity

if TYPE_CHECKING:
    from gigas.runtime.codex.management_models import TurnPage
    from gigas.runtime.codex.models import ActivityEvent
    from gigas.runtime.codex.transport import JsonObject, JsonValue

type HistoryKind = Literal[
    "user", "assistant", "reasoning", "activity", "plan", "request", "answer"
]


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """Literal display text with an identity shared by saved and live items.

    Attributes:
        key: Stable display identity including the public thread, turn, item and role.
        kind: Presentation role; never an instruction to create an input control.
        text: Literal public text, without hidden reasoning or embedded image data.
    """

    key: str
    kind: HistoryKind
    text: str


def item_key(thread_id: str, turn_id: str, item_id: str, *, kind: HistoryKind) -> str:
    """Return an unambiguous display key shared by saved and live public items."""
    return f"item:{(thread_id, turn_id, item_id, kind)!r}"


def activity_entry(thread_id: str, event: "ActivityEvent") -> HistoryEntry:
    """Format one public activity using the same labels for saved and live items."""
    statuses = {"inProgress": "実行中", "completed": "完了", "failed": "失敗", "declined": "拒否"}
    label = statuses.get(event.status, "") if event.status is not None else ""
    title = f"{label} · {event.title}" if label else event.title
    return HistoryEntry(
        item_key(thread_id, event.turn_id, event.item_id, kind="activity"),
        "activity",
        "\n".join(part for part in (title, event.details) if part),
    )


def _texts(value: "JsonValue", *, block_type: str) -> str:
    if not isinstance(value, list):
        return ""
    return "\n".join(
        text
        for block in value
        if isinstance(block, dict)
        and block.get("type") == block_type
        and isinstance(text := block.get("text"), str)
    )


def _agent_text(item: "JsonObject") -> str:
    body = item.get("text")
    parts = [body] if isinstance(body, str) and body else []
    questions = item.get("questions")
    if isinstance(questions, list):
        for question in questions:
            if not isinstance(question, dict) or not isinstance(
                title := question.get("title"), str
            ):
                continue
            lines = [title]
            options = question.get("options")
            if isinstance(options, list):
                lines.extend(option for option in options if isinstance(option, str))
            parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _entry(thread_id: str, turn_id: str, item: "JsonObject") -> HistoryEntry | None:
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id:
        return None
    kind = item.get("type")
    role: HistoryKind
    text = ""
    if kind == "userMessage":
        role, text = "user", _texts(item.get("content"), block_type="text")
    elif kind == "agentMessage":
        role, text = "assistant", _agent_text(item)
    elif kind == "plan":
        role = "plan"
        body = item.get("text")
        text = body if isinstance(body, str) else ""
    elif kind == "reasoning":
        role = "reasoning"
        summary = item.get("summary")
        if isinstance(summary, list):
            text = "\n\n".join(paragraph for paragraph in summary if isinstance(paragraph, str))
    elif kind == "functionCallOutput":
        role = "activity"
        output = item.get("output")
        text = output if isinstance(output, str) else _texts(output, block_type="input_text")
        name = item.get("name")
        if text and isinstance(name, str):
            text = f"{name}\n{text}"
    else:
        activity = item_activity(turn_id, item_id, item)
        return activity_entry(thread_id, activity) if activity is not None else None
    return (
        HistoryEntry(item_key(thread_id, turn_id, item_id, kind=role), role, text) if text else None
    )


def history_entries(thread_id: str, page: "TurnPage") -> tuple[HistoryEntry, ...]:
    """Convert one descending public turn page into chronological display entries.

    Only the supplied public text is used. Unknown or malformed optional items
    are skipped. Saved plans remain plain text, and questions or tool outputs
    never create live requests, replay notifications or write game records.

    Args:
        thread_id: Public identity of the conversation containing this page.
        page: One newest-first turn page; item order inside each turn is retained.

    Returns:
        Oldest-first entries, including a separate interrupted or failed turn
        status when present. Cursors and the input page are not modified.
    """
    entries: list[HistoryEntry] = []
    for turn in reversed(page.turns):
        for item in turn.items:
            with suppress(ValueError):
                entry = _entry(thread_id, turn.id, item)
                if entry is not None:
                    entries.append(entry)
        if turn.status in {"interrupted", "failed"}:
            lines = ["中断" if turn.status == "interrupted" else "失敗"]
            if turn.error is not None:
                for field in ("message", "additionalDetails"):
                    value = turn.error.get(field)
                    if isinstance(value, str) and value:
                        lines.append(value)
            entries.append(
                HistoryEntry(f"turn-status:{(thread_id, turn.id)!r}", "activity", "\n".join(lines))
            )
    return tuple(entries)
