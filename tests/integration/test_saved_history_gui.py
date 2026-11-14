from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QTextCursor

from gigas.runtime.codex.history import HistoryEntry, history_entries
from gigas.runtime.codex.management_models import TurnPage
from gigas.runtime.codex.models import ActivityEvent, ConversationStatus, ReasoningEvent, TextEvent
from gigas.services.codex import ServiceEvent
from gigas.ui.connection_panel import ConnectionPanel
from tests.integration.test_inline_transcript import _form, _history
from tests.unit.test_history_presentation import _turn

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


@pytest.mark.parametrize("selected", [False, True])
def test_prepend_keeps_existing_passage_selection_controls_and_newer_body(
    qtbot: "QtBot", *, selected: bool
) -> None:
    view = _history(qtbot)
    control, editor = _form()
    editor.setText("未送信")
    view.set_item_control("question", control)
    cursor = view.document().find("応答 20")
    if selected:
        end = cursor.selectionEnd()
        cursor.setPosition(end)
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveMode.KeepAnchor)
    else:
        cursor.clearSelection()
    view.setTextCursor(cursor)
    view.ensureCursorVisible()
    visible = view.cursorForPosition(QPoint(0, 1))
    y = view.cursorRect(visible).top()
    old_body = view.toPlainText()
    page = (
        HistoryEntry("older", "user", "過去 😀"),
        HistoryEntry("reason", "reasoning", "過去の公開要約"),
        HistoryEntry("0", "assistant", "古い重複本文"),
    )
    view.set_reasoning_visible(False)
    view.prepend_items(page)
    view.prepend_items(page)
    qtbot.wait(1)
    assert view.toPlainText().count("過去 😀") == 1
    assert view.toPlainText().endswith(old_body)
    assert "過去の公開要約" not in view.toPlainText()
    assert abs(view.cursorRect(visible).top() - y) <= 1
    if selected:
        assert view.textCursor().selectedText() == "応答 20"
        assert view.textCursor().anchor() > view.textCursor().position()
    assert editor.text() == "未送信"
    assert control.isVisible()
    view.set_reasoning_visible(True)
    assert (
        view.toPlainText().index("過去 😀")
        < view.toPlainText().index("過去の公開要約")
        < view.toPlainText().index("経路 😀")
    )
    view.update_item("44", "新着", append=True)
    assert view.toPlainText().endswith("新着\n\n")
    assert view.verticalScrollBar().value() < view.verticalScrollBar().maximum()


def test_prepend_preserves_selection_at_first_character_and_unselected_tail(qtbot: "QtBot") -> None:
    view = _history(qtbot)
    view.selectAll()
    selected = view.textCursor().selectedText()
    view.prepend_items((HistoryEntry("first", "user", "過去 😀"),))
    assert view.textCursor().selectedText() == selected
    cursor = view.textCursor()
    cursor.clearSelection()
    view.setTextCursor(cursor)
    bar = view.verticalScrollBar()
    bar.setValue(bar.maximum())
    view.prepend_items((HistoryEntry("earlier", "user", "もっと前\n" * 20),))
    assert bar.value() == bar.maximum()
    view.update_item("latest", "新着\n" * 20)
    assert bar.value() == bar.maximum()


def test_history_and_live_items_share_keys_without_replaying_requests(qtbot: "QtBot") -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.activate(1)
    page = TurnPage(
        (
            _turn(
                "turn",
                {"id": "answer", "type": "agentMessage", "text": "途中本文"},
                {"id": "reason", "type": "reasoning", "summary": ["途中要約"]},
                {"id": "web", "type": "webSearch", "query": "ラッキー"},
                {
                    "id": "ask",
                    "type": "agentMessage",
                    "text": "経路確認",
                    "questions": [{"title": "進路", "options": ["北", "南"]}],
                },
                {"id": "plan", "type": "plan", "text": "過去のPlan本文"},
            ),
        ),
        None,
        None,
    )
    panel.transcript.prepend_items(history_entries("thread", page))
    assert not panel.requests
    assert panel.plan_panel.view.toPlainText() == "—"
    panel.handle_event(ServiceEvent(1, ConversationStatus("thread", "turn")))
    panel.handle_event(
        ServiceEvent(1, TextEvent("answer", "確定本文", replace=True, turn_id="turn"))
    )
    panel.handle_event(ServiceEvent(1, ReasoningEvent("turn", "reason", ("確定要約",))))
    panel.handle_event(
        ServiceEvent(1, ActivityEvent("turn", "web", "webSearch", "ラッキー", "completed", "図鑑"))
    )
    panel.transcript.prepend_items(history_entries("thread", page))
    body = panel.transcript.toPlainText()
    assert "途中本文" not in body
    assert "途中要約" not in body
    assert body.count("確定本文") == body.count("確定要約") == body.count("ラッキー") == 1
    assert "完了 · ラッキー\n図鑑" in body
    assert "進路\n北\n南" in body
    assert not panel.requests
    panel.handle_event(
        ServiceEvent(1, TextEvent("answer", "次のturn", replace=True, turn_id="next"))
    )
    assert "確定本文" in panel.transcript.toPlainText()
    assert "次のturn" in panel.transcript.toPlainText()
