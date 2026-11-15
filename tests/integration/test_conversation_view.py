from PySide6.QtCore import QPoint
from PySide6.QtGui import QInputMethodEvent, QTextCursor
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from gigas.runtime.codex.history import HistoryEntry
from gigas.runtime.codex.models import (
    ConversationStatus,
    GoalState,
    HumanRequest,
    ModelChoice,
    ModelSelection,
    PlanEvent,
    PlanStep,
    Question,
    SessionStatus,
    TextEvent,
)
from gigas.services.codex import ServiceEvent, ServiceResult
from gigas.ui.connection_panel import ConnectionPanel
from tests.integration.test_codex_gui import _ready


def _panel(qtbot: QtBot) -> ConnectionPanel:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.resize(500, 600)
    panel.show()
    panel.activate(1)
    _ready(panel, 1, "m", "low")
    panel.handle_result(ServiceResult(1, "connect"))
    return panel


def _request(panel: ConnectionPanel) -> None:
    panel.handle_event(
        ServiceEvent(
            1,
            HumanRequest(
                "old-question",
                1,
                "old",
                "turn",
                "question",
                questions=(Question("route", "経路", "進路を選択"),),
            ),
        )
    )


def test_reconnect_retains_documents_draft_goal_and_expired_cards(qtbot: QtBot) -> None:
    panel = _panel(qtbot)
    goal = GoalState("保存された目的", "active")
    panel.handle_event(ServiceEvent(1, ConversationStatus("old", "turn", goal)))
    panel.handle_event(ServiceEvent(1, PlanEvent("turn", (PlanStep("現地へ移動", "inProgress"),))))
    panel.handle_event(ServiceEvent(1, TextEvent("text", "既存本文", replace=True, turn_id="turn")))
    _request(panel)
    panel.prompt.setPlainText("未送信の指示")
    panel.handle_event(ServiceEvent(1, SessionStatus("closed")))
    panel.handle_result(ServiceResult(1, "disconnect"))
    before = panel.transcript.toPlainText()
    document = panel.transcript.document()
    selection = document.find("既存本文")
    panel.transcript.setTextCursor(selection)
    panel.activate(2)
    panel.handle_event(ServiceEvent(2, ConversationStatus()))
    _ready(panel, 2, "m", "low")
    panel.handle_result(ServiceResult(2, "connect"))
    assert panel.transcript.document() is document
    assert panel.transcript.toPlainText() == before
    assert panel.transcript.textCursor().selectedText() == "既存本文"
    assert panel.draft == "未送信の指示"
    assert panel.plan_panel.goal_value.toPlainText() == goal.objective
    assert "現地へ移動" in panel.plan_panel.view.toPlainText()
    assert not panel.requests["old-question"].is_live
    assert not panel.request_views["old-question"].submit_button.isEnabled()
    assert not panel.interrupt_button.isEnabled()
    assert panel.can_switch_conversation
    panel.handle_event(ServiceEvent(1, TextEvent("late", "古い世代")))
    assert "古い世代" not in panel.transcript.toPlainText()


def test_explicit_target_replacement_clears_old_controls_but_draft_discard_is_separate(
    qtbot: QtBot,
) -> None:
    panel = _panel(qtbot)
    panel.handle_event(
        ServiceEvent(1, ConversationStatus("old", "turn", GoalState("以前の目的", "paused")))
    )
    _request(panel)
    panel.handle_event(ServiceEvent(1, PlanEvent("turn", (PlanStep("以前の手順", "pending"),))))
    choices = ModelSelection((ModelChoice("m", ("low",), "low"),), "m", "low")
    panel.handle_event(ServiceEvent(1, choices))
    panel.prompt.setPlainText("破棄確認前の原稿")
    panel.begin_loading("別Project", "保存会話")
    panel.replace_target(
        "別Project",
        "保存会話",
        thread_id="saved",
        entries=(HistoryEntry("saved", "assistant", "保存本文"),),
        goal=GoalState("別の目的", "paused"),
    )
    assert panel.heading.project.text() == "別Project"
    assert panel.heading.title.text() == "保存会話"
    assert "保存本文" in panel.transcript.toPlainText()
    assert "進路を選択" not in panel.transcript.toPlainText()
    assert not panel.requests
    assert not panel.request_views
    assert not panel.heading.loading.isVisible()
    assert not panel.transcript.isHidden()
    assert not panel.plan_panel.goal_value.isHidden()
    assert not panel.plan_panel.view.isHidden()
    assert not panel.prompt.isReadOnly()
    assert panel.plan_panel.view.toPlainText() == "—"
    assert panel.plan_panel.goal_value.toPlainText() == "別の目的"
    assert panel.composer.selection == choices
    assert panel.draft == "破棄確認前の原稿"
    QApplication.sendEvent(panel.prompt, QInputMethodEvent("変換中", []))
    assert panel.prompt.is_composing
    panel.discard_draft()
    assert panel.draft == ""
    assert not panel.prompt.is_composing
    panel.replace_target("未所属", "新しい会話")
    assert panel.transcript.toPlainText() == ""
    assert panel.plan_panel.goal_value.toPlainText() == "—"


def test_loading_hides_existing_content_without_recreating_it_and_failure_restores(
    qtbot: QtBot,
) -> None:
    panel = _panel(qtbot)
    panel.replace_target(
        "Project",
        "元の会話",
        thread_id="old",
        entries=tuple(HistoryEntry(str(i), "assistant", f"本文 {i}\n補足") for i in range(50)),
        goal=GoalState("現在の目的", "paused"),
    )
    panel.prompt.setPlainText("そのまま保持")
    selected = panel.transcript.document().find("本文 20")
    start, end = selected.selectionStart(), selected.selectionEnd()
    selected.setPosition(end)
    selected.setPosition(start, QTextCursor.MoveMode.KeepAnchor)
    panel.transcript.setTextCursor(selected)
    panel.transcript.ensureCursorVisible()
    visible = panel.transcript.cursorForPosition(QPoint(0, 1))
    y = panel.transcript.cursorRect(visible).top()
    document = panel.transcript.document()
    panel.begin_loading("候補Project", "候補会話")
    assert panel.heading.title.text() == "候補会話"
    assert panel.heading.loading.isVisible()
    assert panel.transcript.isHidden()
    assert panel.plan_panel.goal_value.isHidden()
    assert panel.plan_panel.view.isHidden()
    assert panel.prompt.isReadOnly()
    assert not panel.send_button.isEnabled()
    assert not panel.composer.model_button.isEnabled()
    assert not panel.plan_panel.clear_button.isEnabled()
    assert not panel.can_switch_conversation
    assert panel.disconnect_action.isEnabled()
    panel.cancel_loading()
    qtbot.wait(1)
    assert panel.heading.title.text() == "元の会話"
    assert not panel.heading.loading.isVisible()
    assert not panel.transcript.isHidden()
    assert not panel.plan_panel.goal_value.isHidden()
    assert panel.transcript.document() is document
    assert panel.transcript.textCursor().selectedText() == "本文 20"
    assert panel.transcript.textCursor().anchor() > panel.transcript.textCursor().position()
    assert abs(panel.transcript.cursorRect(visible).top() - y) <= 1
    assert panel.draft == "そのまま保持"
    assert panel.plan_panel.goal_value.toPlainText() == "現在の目的"
    assert panel.send_button.isEnabled()


def test_browsing_goal_does_not_become_execution_and_clear_needs_no_model_or_sandbox(
    qtbot: QtBot,
) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.activate(1)
    panel.handle_event(ServiceEvent(1, SessionStatus("ready", authenticated=True)))
    panel.handle_result(ServiceResult(1, "connect"))
    active = GoalState("閲覧中の目的", "active")
    panel.replace_target("Project", "保存会話", thread_id="saved", goal=active)
    panel.handle_event(ServiceEvent(1, ConversationStatus()))
    assert panel.plan_panel.goal_value.toPlainText() == active.objective
    assert panel.can_switch_conversation
    assert not panel.interrupt_button.isEnabled()
    assert panel.sandbox_action.isEnabled()
    assert not panel.plan_panel.resume_button.isEnabled()
    assert panel.plan_panel.clear_button.isEnabled()
    cleared: list[bool] = []
    panel.goal_clear_requested.connect(lambda: cleared.append(True))
    panel.plan_panel.clear_button.click()
    assert cleared == [True]
    assert not panel.plan_panel.clear_button.isEnabled()
    assert not panel.can_switch_conversation
    assert panel.disconnect_action.isEnabled()
    panel.set_browse_goal(None)
    panel.handle_result(ServiceResult(1, "goal_clear"))
    assert panel.plan_panel.goal_value.toPlainText() == "—"
    assert panel.can_switch_conversation
    panel.set_browse_goal(active)
    panel.handle_event(ServiceEvent(1, SessionStatus("login_required")))
    assert not panel.plan_panel.clear_button.isEnabled()


def test_loaded_execution_and_loading_keep_stop_but_block_switch(qtbot: QtBot) -> None:
    panel = _panel(qtbot)
    panel.replace_target("Project", "保存会話", thread_id="saved", goal=GoalState("目的", "active"))
    changed: list[bool] = []
    panel.state_changed.connect(lambda: changed.append(panel.can_switch_conversation))
    panel.handle_event(
        ServiceEvent(1, ConversationStatus("saved", "turn", GoalState("目的", "active")))
    )
    assert not panel.can_switch_conversation
    assert panel.interrupt_button.isEnabled()
    panel.begin_loading("Project", "別会話")
    assert panel.interrupt_button.isEnabled()
    assert panel.disconnect_action.isEnabled()
    panel.interrupt_button.click()
    assert not panel.can_switch_conversation
    assert not panel.interrupt_button.isEnabled()
    panel.handle_event(
        ServiceEvent(1, ConversationStatus("saved", goal=GoalState("目的", "paused")))
    )
    panel.handle_result(ServiceResult(1, "stop"))
    assert not panel.can_switch_conversation
    panel.cancel_loading()
    assert panel.can_switch_conversation
    assert changed[0] is False
    assert changed[-1] is True
