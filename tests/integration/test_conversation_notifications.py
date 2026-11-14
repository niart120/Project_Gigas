import asyncio
import json
from pathlib import Path

import pytest

from gigas.runtime.codex.connection import CodexConnectionError
from gigas.runtime.codex.models import (
    ActivityEvent,
    DialogueEvent,
    ModelSelection,
    PlanEvent,
    PlanStep,
    ReasoningEvent,
    TextEvent,
    TurnStarted,
)
from gigas.runtime.codex.transport import JsonObject, RpcNotification
from tests.integration.test_conversation_execution import Conversation
from tests.unit.test_codex_connection import _run, _terminal, _until


def _notify(
    conversation: Conversation,
    method: str,
    fields: JsonObject,
    *,
    turn: str = "turn-1",
    thread: str = "thread-1",
) -> None:
    conversation.execution.receive(
        RpcNotification(method, {"threadId": thread, "turnId": turn, **fields})
    )


@pytest.mark.parametrize("wrapped_result", [False, True])
def test_public_progress_preserves_turn_identity_and_excludes_private_payloads(
    tmp_path: Path, wrapped_result: bool
) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        try:
            await c.execution.send("開始")
            c.turn("turn/started")
            _notify(c, "turn/plan/updated", {"plan": [{"step": "観測", "status": "pending"}]})
            _notify(c, "turn/plan/updated", {"plan": [], "explanation": None})
            for index, delta in ((1, "道を確認"), (0, "公開"), (0, "要約")):
                _notify(
                    c,
                    "item/reasoning/summaryTextDelta",
                    {"itemId": "reason-1", "summaryIndex": index, "delta": delta},
                )
            _notify(
                c,
                "item/reasoning/textDelta",
                {"itemId": "reason-1", "contentIndex": 0, "delta": "PRIVATE-REASONING"},
            )
            _notify(
                c,
                "item/completed",
                {
                    "item": {
                        "type": "reasoning",
                        "id": "reason-1",
                        "summary": ["確定した公開要約"],
                        "content": ["PRIVATE-REASONING"],
                    }
                },
            )
            command: JsonObject = {
                "type": "commandExecution",
                "id": "cmd-1",
                "command": "Get-Date",
                "cwd": "C:/archive",
                "status": "inProgress",
            }
            _notify(c, "item/started", {"item": command})
            _notify(
                c,
                "item/completed",
                {
                    "item": {
                        **command,
                        "status": "completed",
                        "aggregatedOutput": "時刻",
                        "exitCode": 0,
                    }
                },
            )
            mcp: JsonObject = {
                "type": "mcpToolCall",
                "id": "mcp-1",
                "server": "game",
                "tool": "observe",
                "status": "inProgress",
                "arguments": {"authorization": "PRIVATE-ARGUMENT"},
            }
            _notify(c, "item/started", {"item": mcp})
            result: JsonObject = {
                "content": [
                    {"type": "text", "text": "frame-1"},
                    {"type": "image", "data": "PRIVATE-IMAGE", "mimeType": "image/png"},
                ],
                "structuredContent": {"private": "PRIVATE-STRUCTURED"},
                "_meta": {"private": "PRIVATE-META"},
            }
            if wrapped_result:
                result = {
                    "content": [{"type": "text", "text": json.dumps({**result, "isError": False})}]
                }
            _notify(c, "item/completed", {"item": {**mcp, "status": "completed", "result": result}})
            _notify(
                c,
                "item/completed",
                {
                    "item": {
                        "type": "fileChange",
                        "id": "file-1",
                        "status": "completed",
                        "changes": [
                            {"path": "memory.md", "kind": {"type": "add"}, "diff": "+記録"}
                        ],
                    }
                },
            )
            _notify(c, "turn/plan/updated", {"plan": []}, turn="unknown-turn")
            _notify(c, "turn/plan/updated", {"plan": []}, thread="foreign-thread")
            assert [event for event in c.events if isinstance(event, PlanEvent)] == [
                PlanEvent("turn-1", (PlanStep("観測", "pending"),)),
                PlanEvent("turn-1", ()),
            ]
            assert [event for event in c.events if isinstance(event, ReasoningEvent)] == [
                ReasoningEvent("turn-1", "reason-1", ("道を確認",)),
                ReasoningEvent("turn-1", "reason-1", ("公開", "道を確認")),
                ReasoningEvent("turn-1", "reason-1", ("公開要約", "道を確認")),
                ReasoningEvent("turn-1", "reason-1", ("確定した公開要約",)),
            ]
            assert [event for event in c.events if isinstance(event, ActivityEvent)] == [
                ActivityEvent(
                    "turn-1", "cmd-1", "commandExecution", "Get-Date", "inProgress", "C:/archive"
                ),
                ActivityEvent(
                    "turn-1",
                    "cmd-1",
                    "commandExecution",
                    "Get-Date",
                    "completed",
                    "C:/archive\n時刻\n終了コード: 0",
                ),
                ActivityEvent("turn-1", "mcp-1", "mcpToolCall", "game/observe", "inProgress"),
                ActivityEvent(
                    "turn-1", "mcp-1", "mcpToolCall", "game/observe", "completed", "frame-1"
                ),
                ActivityEvent(
                    "turn-1",
                    "file-1",
                    "fileChange",
                    "memory.md",
                    "completed",
                    "add: memory.md\n+記録",
                ),
            ]
            assert "PRIVATE-" not in repr(c.events)

            c.turn("turn/completed")
            c.peer.responses["turn/start"] = {"turn": {"id": "turn-2", "status": "inProgress"}}
            sending = asyncio.create_task(c.execution.send("続行"))
            await _until(lambda: c.execution.snapshot.sending)
            c.turn("turn/started", "turn-2")
            await sending
            before = list(c.events)
            _notify(c, "turn/plan/updated", {"plan": []})
            _notify(
                c,
                "item/completed",
                {"item": {"type": "reasoning", "id": "reason-1", "summary": []}},
            )
            _notify(
                c,
                "item/completed",
                {"item": {**mcp, "status": "failed", "error": {"message": "停止"}}},
            )
            assert c.events == before
            _notify(
                c,
                "item/reasoning/summaryTextDelta",
                {"itemId": "reason-1", "summaryIndex": 0, "delta": "新しい公開要約"},
                turn="turn-2",
            )
            assert c.events[len(before) :] == [
                ReasoningEvent("turn-2", "reason-1", ("新しい公開要約",))
            ]
            assert c.execution.snapshot.turn_id == "turn-2"
            assert [event for event in c.events if isinstance(event, TurnStarted)] == [
                TurnStarted("turn-1"),
                TurnStarted("turn-2"),
            ]
        finally:
            await c.close()

    _run(scenario())


def test_interrupted_turn_flushes_pending_text_once_at_terminal_notification(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        try:
            await c.execution.send("start")
            c.turn("turn/started")
            for delta in ("one", " two"):
                _notify(c, "item/agentMessage/delta", {"itemId": "answer", "delta": delta})
            stopping = asyncio.create_task(c.execution.stop())
            await _until(lambda: any(method == "turn/interrupt" for method, _ in c.peer.calls))
            assert not stopping.done()
            assert not any(isinstance(event, DialogueEvent) for event in c.events)
            c.execution.receive(_terminal("interrupted"))
            await stopping
            _notify(c, "item/agentMessage/delta", {"itemId": "answer", "delta": " late"})
            _notify(
                c,
                "item/completed",
                {"item": {"type": "agentMessage", "id": "answer", "text": "late final"}},
            )
            await c.close()
            assert [event for event in c.events if isinstance(event, DialogueEvent)] == [
                DialogueEvent(
                    "message",
                    {"item_id": "answer", "kind": "message", "text": "one two", "partial": True},
                    "turn-1",
                ),
                DialogueEvent("turn_completed", {"status": "interrupted"}, "turn-1"),
            ]
            assert [
                event
                for event in c.events
                if isinstance(event, TextEvent) and event.role == "assistant"
            ] == [TextEvent("answer", "one"), TextEvent("answer", " two")]
        finally:
            await c.close()

    _run(scenario())


def test_rejected_model_update_preserves_confirmed_selection_for_next_send(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        try:
            await c.execution.send("first")
            c.turn("turn/started")
            selection = c.connection.selection
            selection_events = [event for event in c.events if isinstance(event, ModelSelection)]
            c.peer.rpc_errors["thread/settings/update"] = -32602
            with pytest.raises(CodexConnectionError, match="RPC -32602"):
                await c.execution.select_model("other", "low")
            assert c.connection.selection == selection
            assert c.execution.snapshot.turn_id == "turn-1"
            assert not c.execution.snapshot.sending
            assert [
                event for event in c.events if isinstance(event, ModelSelection)
            ] == selection_events
            c.turn("turn/completed")
            assert await c.execution.send("second") == "turn-1"
            starts = [params for method, params in c.peer.calls if method == "turn/start"]
            assert len(starts) == 2
            assert starts[-1] is not None
            assert starts[-1]["model"] == selection.model
            assert starts[-1]["effort"] == selection.effort
            assert not any(method == "turn/interrupt" for method, _ in c.peer.calls)
            assert not c.peer.closed
        finally:
            await c.close()

    _run(scenario())
