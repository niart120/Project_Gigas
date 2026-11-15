from copy import deepcopy

from gigas.runtime.codex._notifications import TurnContent
from gigas.runtime.codex.history import activity_entry, history_entries, item_key
from gigas.runtime.codex.management_models import TurnInfo, TurnPage
from gigas.runtime.codex.models import ActivityEvent
from gigas.runtime.codex.transport import JsonObject


def _turn(
    turn_id: str, *items: JsonObject, status: str = "completed", error: JsonObject | None = None
) -> TurnInfo:
    return TurnInfo(turn_id, status, items, "full", error, None, None, None, None)


def test_history_presentation_preserves_public_bodies_and_chronological_identity() -> None:
    page = TurnPage(
        (
            _turn(
                "new",
                {
                    "id": "same",
                    "type": "agentMessage",
                    "text": "進捗 <b>😀</b>",
                    "phase": "commentary",
                },
                {"id": "plan", "type": "plan", "text": "1. 準備\n2. 捕獲"},
                {
                    "id": "reason",
                    "type": "reasoning",
                    "summary": ["公開", "要約"],
                    "content": ["PRIVATE"],
                },
                status="failed",
                error={"message": "失敗理由", "additionalDetails": "公開詳細"},
            ),
            _turn(
                "old",
                {
                    "id": "same",
                    "type": "userMessage",
                    "content": [{"type": "text", "text": "開始"}],
                },
            ),
        ),
        None,
        None,
    )
    before = deepcopy(page)
    entries = history_entries("thread", page)
    assert [(entry.kind, entry.text) for entry in entries] == [
        ("user", "開始"),
        ("assistant", "進捗 <b>😀</b>"),
        ("plan", "1. 準備\n2. 捕獲"),
        ("reasoning", "公開\n\n要約"),
        ("activity", "失敗\n失敗理由\n公開詳細"),
    ]
    assert entries[0].key == item_key("thread", "old", "same", kind="user")
    assert entries[1].key == item_key("thread", "new", "same", kind="assistant")
    assert len({entry.key for entry in entries}) == len(entries)
    assert history_entries("other-thread", page)[0].key != entries[0].key
    assert page == before


def test_saved_questions_answers_and_tool_results_are_only_literal_text() -> None:
    page = TurnPage(
        (
            _turn(
                "turn",
                {
                    "id": "ask",
                    "type": "agentMessage",
                    "text": "進路確認",
                    "questions": [{"title": "進路", "options": ["北", "南"]}],
                },
                {
                    "id": "answer",
                    "type": "functionCallOutput",
                    "name": "request_user_input",
                    "output": [
                        {"type": "input_text", "text": '{"進路": "北"}'},
                        {"type": "input_image", "image_url": "PRIVATE"},
                    ],
                },
                {
                    "id": "cmd",
                    "type": "commandExecution",
                    "command": "Get-Date",
                    "cwd": "C:/game",
                    "status": "declined",
                    "aggregatedOutput": "拒否理由",
                    "exitCode": 1,
                },
                {
                    "id": "file",
                    "type": "fileChange",
                    "status": "completed",
                    "changes": [{"path": "攻略.md", "kind": {"type": "add"}, "diff": "+攻略"}],
                },
                {
                    "id": "mcp",
                    "type": "mcpToolCall",
                    "server": "gigas",
                    "tool": "observe",
                    "status": "completed",
                    "arguments": {"secret": "PRIVATE"},
                    "result": {
                        "content": [
                            {"type": "text", "text": "observation-1"},
                            {"type": "image", "data": "PRIVATE"},
                        ]
                    },
                },
                {"id": "unknown", "type": "unsupported", "text": "PRIVATE"},
                status="interrupted",
            ),
        ),
        None,
        None,
    )
    entries = history_entries("thread", page)
    bodies = [entry.text for entry in entries]
    assert bodies == [
        "進路確認\n\n進路\n北\n南",
        'request_user_input\n{"進路": "北"}',
        "拒否 · Get-Date\nC:/game\n拒否理由\n終了コード: 1",
        "完了 · 攻略.md\nadd: 攻略.md\n+攻略",
        "完了 · gigas/observe\nobservation-1",
        "中断",
    ]
    assert "PRIVATE" not in repr(entries)
    assert all(entry.kind != "request" for entry in entries)


def test_saved_web_has_no_invented_lifecycle_and_shares_live_identity() -> None:
    item: JsonObject = {
        "id": "web",
        "type": "webSearch",
        "query": "ラッキー",
        "action": {"type": "search", "queries": ["ラッキー", "捕獲率"]},
        "results": [{"title": "図鑑", "url": "https://example.test/"}],
    }
    entries = history_entries("thread", TurnPage((_turn("turn", item),), None, None))
    assert len(entries) == 1
    assert entries[0].kind == "activity"
    assert "ラッキー" in entries[0].text
    assert "捕獲率" in entries[0].text
    assert "https://example.test/" in entries[0].text
    assert "図鑑" in entries[0].text
    assert "完了" not in entries[0].text
    assert "実行中" not in entries[0].text
    live = activity_entry(
        "thread", ActivityEvent("turn", "web", "webSearch", "ラッキー", "completed")
    )
    assert live.key == entries[0].key


def test_missing_or_malformed_optional_history_does_not_hide_other_items() -> None:
    page = TurnPage(
        (
            _turn(
                "turn",
                {"id": "bad", "type": "fileChange", "status": "completed", "changes": []},
                {"id": "empty", "type": "reasoning", "summary": [], "content": ["PRIVATE"]},
                {"id": "good", "type": "agentMessage", "text": "本文"},
            ),
        ),
        None,
        None,
    )
    assert [entry.text for entry in history_entries("thread", page)] == ["本文"]


def test_live_web_lifecycle_uses_public_action_and_replaces_one_activity() -> None:
    content = TurnContent("turn")
    item: JsonObject = {
        "id": "web",
        "type": "webSearch",
        "query": "ラッキー",
        "action": {"type": "search", "queries": ["ラッキー"]},
    }
    started = list(content.accept("item/started", {"item": item}))
    completed = list(
        content.accept(
            "item/completed",
            {"item": {**item, "results": [{"title": "図鑑", "url": "https://example.test/"}]}},
        )
    )
    activities = [event for event in (*started, *completed) if isinstance(event, ActivityEvent)]
    assert [(event.item_id, event.status) for event in activities] == [
        ("web", "inProgress"),
        ("web", "completed"),
    ]
    assert "図鑑" in activities[-1].details
    assert content.finish() == []
