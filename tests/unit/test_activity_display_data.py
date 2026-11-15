from copy import deepcopy
from typing import TYPE_CHECKING

import pytest
from tests.unit.test_history_presentation import _turn

from gigas.runtime.codex._notifications import TurnContent
from gigas.runtime.codex.history import history_entries
from gigas.runtime.codex.management_models import TurnPage
from gigas.runtime.codex.models import ActivityEvent, ReasoningEvent, TextEvent

if TYPE_CHECKING:
    from gigas.runtime.codex.transport import JsonObject


@pytest.mark.parametrize("phase", [None, "unknown", "partial_answer"])
def test_phase_is_public_only_and_retained_in_interrupted_snapshot(phase: str | None) -> None:
    content = TurnContent("turn")
    item: JsonObject = {"id": "a", "type": "agentMessage", "text": "完了しました", "phase": phase}
    events = list(content.accept("item/started", {"item": item}))
    expected = phase if phase == "partial_answer" else None
    assert isinstance(events[0], TextEvent)
    assert events[0].phase == expected
    assert events[0].completed is False
    saved = history_entries("thread", TurnPage((_turn("turn", item),), None, None))[0]
    assert saved.phase == expected
    assert saved.completed is None
    snapshot = content.finish()[0]
    assert snapshot.payload.get("phase") == expected
    assert snapshot.payload["partial"] is True


def test_message_phase_survives_deltas_and_authoritative_completion() -> None:
    content = TurnContent("turn")
    item: JsonObject = {"id": "answer", "type": "agentMessage", "text": "", "phase": "commentary"}
    started = list(content.accept("item/started", {"item": item}))
    delta = list(content.accept("item/agentMessage/delta", {"itemId": "answer", "delta": "途中"}))
    final: JsonObject = {**item, "text": "確定本文", "phase": "final_answer"}
    completed = list(content.accept("item/completed", {"item": final}))
    texts = [event for event in (*started, *delta, *completed) if isinstance(event, TextEvent)]
    assert [(event.text, event.phase, event.completed) for event in texts] == [
        ("", "commentary", False),
        ("途中", "commentary", False),
        ("確定本文", "final_answer", True),
    ]
    saved = history_entries("thread", TurnPage((_turn("turn", final),), None, None))[0]
    assert saved.phase == texts[-1].phase
    assert content.finish() == []
    assert (
        list(content.accept("item/agentMessage/delta", {"itemId": "answer", "delta": "遅着"})) == []
    )


def test_summary_indices_survive_sparse_and_out_of_order_notifications() -> None:
    content = TurnContent("turn")
    events = []
    for index, delta in [(2, "最新"), (0, "以前"), (2, "の続き")]:
        events.extend(
            content.accept(
                "item/reasoning/summaryTextDelta",
                {"itemId": "r", "summaryIndex": index, "delta": delta},
            )
        )
    summaries = [event for event in events if isinstance(event, ReasoningEvent)]
    assert [(part.index, part.text) for part in summaries[-1].summary] == [
        (0, "以前"),
        (2, "最新の続き"),
    ]
    assert (
        list(content.accept("item/reasoning/summaryPartAdded", {"itemId": "r", "summaryIndex": 3}))
        == []
    )
    final: JsonObject = {"id": "r", "type": "reasoning", "summary": ["訂正"], "content": ["非公開"]}
    completed = next(
        event
        for event in content.accept("item/completed", {"item": final})
        if isinstance(event, ReasoningEvent)
    )
    saved = history_entries("thread", TurnPage((_turn("turn", final),), None, None))[0]
    assert saved.summary == completed.summary
    assert completed.completed is True
    assert "非公開" not in repr(saved)


def test_activity_details_are_separate_from_compact_title_in_live_and_history() -> None:
    content = TurnContent("turn")
    item: JsonObject = {
        "id": "cmd",
        "type": "commandExecution",
        "command": "echo first\necho second",
        "status": "completed",
        "cwd": "C:/game",
        "exitCode": 0,
        "aggregatedOutput": "first\nsecond",
    }
    live = next(
        event
        for event in content.accept("item/completed", {"item": item})
        if isinstance(event, ActivityEvent)
    )
    saved = history_entries("thread", TurnPage((_turn("turn", item),), None, None))[0]
    assert saved.activity is not None
    assert (live.title, live.cwd, live.exit_code, live.details) == (
        item["command"],
        "C:/game",
        0,
        "first\nsecond",
    )
    assert saved.activity.title == live.title
    assert saved.activity.details == live.details
    assert saved.activity.completed is None
    assert live.completed is True


def test_mcp_arguments_survive_without_image_or_metadata_and_do_not_alias_input() -> None:
    content = TurnContent("turn")
    item: JsonObject = {
        "id": "input",
        "type": "mcpToolCall",
        "server": "gigas",
        "tool": "input",
        "status": "completed",
        "arguments": {"steps": [{"button": "UP", "duration": 0.4}]},
        "result": {
            "content": [{"type": "text", "text": "送出完了"}, {"type": "image", "data": "IMAGE"}],
            "_meta": {"private": "META"},
        },
    }
    before = deepcopy(item)
    live = next(
        event
        for event in content.accept("item/completed", {"item": item})
        if isinstance(event, ActivityEvent)
    )
    saved = history_entries("thread", TurnPage((_turn("turn", item),), None, None))[0]
    assert live.arguments == before["arguments"]
    assert saved.activity is not None
    assert saved.activity.arguments == live.arguments
    assert live.arguments is not item["arguments"]
    assert live.details == "送出完了"
    assert item == before
    assert "IMAGE" not in repr(saved)
    assert "META" not in repr(saved)


def test_command_output_deltas_survive_interruption_and_final_output_replaces_them() -> None:
    for completed in (False, True):
        content = TurnContent("turn")
        item: JsonObject = {
            "id": "cmd",
            "type": "commandExecution",
            "command": "run",
            "status": "inProgress",
        }
        list(content.accept("item/started", {"item": item}))
        output = list(
            content.accept(
                "item/commandExecution/outputDelta", {"itemId": "cmd", "delta": "途中の出力"}
            )
        )
        assert len(output) == 1
        assert isinstance(output[0], ActivityEvent)
        assert output[0].details == "途中の出力"
        if completed:
            events = list(
                content.accept(
                    "item/completed",
                    {
                        "item": {
                            **item,
                            "status": "completed",
                            "aggregatedOutput": "確定した出力",
                            "exitCode": 0,
                        }
                    },
                )
            )
            assert isinstance(events[0], ActivityEvent)
            assert events[0].details == "確定した出力"
            assert content.finish() == []
        else:
            snapshots = content.finish()
            assert snapshots[0].payload["details"] == "途中の出力"
            assert snapshots[0].payload["partial"] is True
            assert content.finish() == []


@pytest.mark.parametrize(("output", "expected"), [(None, "途中"), ("", "")])
def test_command_completion_distinguishes_unavailable_and_empty_output(
    output: str | None, expected: str
) -> None:
    content = TurnContent("turn")
    item: JsonObject = {
        "id": "c",
        "type": "commandExecution",
        "command": "run",
        "status": "inProgress",
    }
    list(content.accept("item/started", {"item": item}))
    list(content.accept("item/commandExecution/outputDelta", {"itemId": "c", "delta": "途中"}))
    final: JsonObject = {**item, "status": "completed", "aggregatedOutput": output}
    events = list(content.accept("item/completed", {"item": final}))
    assert isinstance(events[0], ActivityEvent)
    assert events[0].details == expected
    assert (
        list(content.accept("item/commandExecution/outputDelta", {"itemId": "c", "delta": "遅着"}))
        == []
    )


def test_mcp_progress_is_replaced_by_result_and_cannot_reopen_completed_item() -> None:
    content = TurnContent("turn")
    item: JsonObject = {
        "id": "m",
        "type": "mcpToolCall",
        "server": "game",
        "tool": "input",
        "status": "inProgress",
        "arguments": {"button": "UP"},
    }
    list(content.accept("item/started", {"item": item}))
    for message in ("準備", "送出中"):
        progress = list(
            content.accept("item/mcpToolCall/progress", {"itemId": "m", "message": message})
        )
        assert isinstance(progress[0], ActivityEvent)
        assert progress[0].details == message
        assert progress[0].arguments == item["arguments"]
    final: JsonObject = {**item, "status": "failed", "error": {"message": "切断"}}
    events = list(content.accept("item/completed", {"item": final}))
    assert isinstance(events[0], ActivityEvent)
    assert events[0].details == "切断"
    assert events[0].status == "failed"
    assert (
        list(content.accept("item/mcpToolCall/progress", {"itemId": "m", "message": "遅着"})) == []
    )
    assert content.finish() == []
