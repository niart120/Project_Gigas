from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from gigas.runtime.codex.models import PlanEvent, PlanStep
from gigas.ui.plan_panel import PlanPanel


def test_plan_keeps_its_place_and_last_public_steps_after_turn_end(qtbot: QtBot) -> None:
    panel = PlanPanel()
    qtbot.addWidget(panel)
    panel.resize(500, 260)
    panel.show()
    before = panel.geometry()
    assert panel.view.toPlainText()
    assert panel.goal_heading.isVisible()
    assert panel.goal_value.isVisible()
    goal_text = panel.goal_value.text()
    assert goal_text == "—"
    assert panel.goal_heading.geometry().bottom() < panel.goal_value.geometry().top()
    assert panel.goal_value.geometry().bottom() < panel.heading.geometry().top()
    event = PlanEvent(
        "turn-1",
        (
            PlanStep("画面を確認", "completed"),
            PlanStep("橋🙂へ移動 <script>literal</script>", "inProgress"),
            PlanStep("入口で停止", "pending"),
        ),
        "橋の周囲を確認しながら進みます。",
    )
    panel.set_plan(event)
    view = panel.view
    for step in event.steps:
        assert step.step in view.toPlainText()
    assert event.explanation in view.toPlainText()
    assert panel.goal_value.text() == goal_text
    found = view.document().find("橋🙂へ移動")
    assert found.charFormat().fontWeight() == QFont.Weight.Bold

    panel.set_plan(event, interrupted=True)
    assert panel.geometry() == before
    assert event.steps[1].status == "inProgress"
    assert all(step.step in view.toPlainText() for step in event.steps)
    found = view.document().find("橋🙂へ移動")
    assert found.charFormat().fontWeight() != QFont.Weight.Bold
    assert panel.status.text() == "中断"

    panel.set_plan(event, finished=True)
    assert event.steps[1].status == "inProgress"
    assert panel.status.text() == "終了"
    assert "進行中" not in view.toPlainText()
    assert view.document().find("橋🙂へ移動").charFormat().fontWeight() != QFont.Weight.Bold
    panel.set_plan(PlanEvent("turn-1", (PlanStep("入口で停止", "completed"),)), finished=True)
    assert panel.status.text() == "完了"

    panel.set_plan(None)
    assert panel.geometry() == before
    assert panel.view.isVisible()
    assert not any(step.step in view.toPlainText() for step in event.steps)
    assert not panel.status.text()
    assert panel.goal_heading.isVisible()
    assert panel.goal_value.isVisible()
    assert panel.goal_value.text() == goal_text


def test_plan_update_preserves_reverse_selection_copy_and_reading_position(qtbot: QtBot) -> None:
    panel = PlanPanel()
    qtbot.addWidget(panel)
    panel.resize(460, 230)
    panel.show()
    steps = tuple(PlanStep(f"観測地点 {index} 日本語🙂", "pending") for index in range(35))
    panel.set_plan(PlanEvent("turn", steps))
    view = panel.view
    cursor = view.document().find("観測地点 17 日本語🙂")
    start, end = cursor.selectionStart(), cursor.selectionEnd()
    cursor.setPosition(end)
    cursor.setPosition(start, QTextCursor.MoveMode.KeepAnchor)
    view.setTextCursor(cursor)
    view.ensureCursorVisible()
    selected = cursor.selectedText()
    visible = view.cursorForPosition(QPoint(0, 1))
    visible_text = visible.block().text()
    visible_y = view.cursorRect(visible).top()

    updated = (PlanStep("最初の観測地点で機器を確認\n周囲を確認", "completed"), *steps[1:])
    panel.set_plan(PlanEvent("turn", updated))
    assert view.textCursor().selectedText() == selected
    assert view.textCursor().anchor() > view.textCursor().position()
    view.copy()
    assert QApplication.clipboard().text() == selected
    now = view.cursorForPosition(QPoint(0, 1))
    assert now.block().text() == visible_text
    assert abs(view.cursorRect(now).top() - visible_y) <= 1

    panel.resize(380, 200)
    assert view.textCursor().selectedText() == selected
