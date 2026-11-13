from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QFont, QInputMethodEvent, QTextCursor
from PySide6.QtWidgets import QApplication, QTextBrowser
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.runtime.codex.connection import CodexConnection
from gigas.runtime.codex.models import (
    AccountInfo,
    ActivityEvent,
    ConnectionOptions,
    GoalEvent,
    GoalState,
    HumanRequest,
    LoginEvent,
    ModelChoice,
    ModelSelection,
    PlanEvent,
    PlanStep,
    Question,
    QuestionOption,
    RateLimitWindow,
    ReasoningEvent,
    RequestResolved,
    SandboxEvent,
    SessionStatus,
    TextEvent,
    TurnStarted,
    UsageBucket,
    UsageEvent,
)
from gigas.runtime.codex.transport import StdioTransport
from gigas.services.codex import (
    AnswerResult,
    CodexServiceThread,
    MessageResult,
    ServiceEvent,
    ServiceResult,
)
from gigas.ui.connection_panel import ConnectionPanel
from gigas.ui.main_window import MainWindow
from gigas.ui.request_card import AnswerSubmission
from gigas.ui.settings_dialog import SettingsDialog
from gigas.ui.transcript import Transcript
from tests.integration.test_codex_transport import PEER_COMMAND

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from gigas.runtime.codex.models import ConnectionEvent, ThreadConfiguration
    from gigas.ui.notifications import Notice
    from gigas.ui.prompt_edit import PromptEdit


def _answer_editor(panel: ConnectionPanel, token: str, question_id: str) -> "PromptEdit":
    editor = panel.request_views[token].question_inputs[question_id].editor
    assert editor is not None
    return editor


def test_shell_dialogue_round_trip_uses_service_and_public_stdio(
    qtbot: QtBot, tmp_path: Path
) -> None:
    def factory(
        options: ConnectionOptions,
        emit: "Callable[[ConnectionEvent], None]",
        prepare: "Callable[[str, str | None], Awaitable[ThreadConfiguration]]",
        started: "Callable[[str], Awaitable[None]]",
    ) -> CodexConnection:
        peer = StdioTransport((*PEER_COMMAND, "dialogue"), options.cwd)
        return CodexConnection(peer, options, emit, prepare_turn=prepare, on_thread_started=started)

    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot), factory)
    results: list[ServiceResult] = []
    service.completed.connect(results.append)
    shell = ApplicationShell(tmp_path / "settings.toml", service=service)
    panel = shell.window.connection
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        panel.connect_action.trigger()
        qtbot.waitUntil(lambda: any(result.operation == "connect" for result in results))
        assert not next(result.error for result in results if result.operation == "connect")
        panel.prompt.setPlainText("開始 日本語🙂")
        panel.send_button.click()
        panel.send_button.click()
        qtbot.waitUntil(lambda: len(panel.requests) == 1)
        question = next(iter(panel.requests.values()))
        token = question.request.token
        assert question.request.kind == "question"
        assert "経路確認" in panel.plan_panel.view.toPlainText()
        assert "公開要約" in panel.transcript.toPlainText()
        assert "fixture/observe" in panel.transcript.toPlainText()
        assert "観測完了" in panel.transcript.toPlainText()
        assert "INTERNAL_CONTENT" not in panel.transcript.toPlainText()
        qtbot.waitUntil(lambda: any(result.operation == "send" for result in results))
        panel.prompt.setPlainText("質問は保留し、門の前で止まる")
        panel.send_button.click()
        qtbot.waitUntil(lambda: any(result.operation == "steer" for result in results))
        steering = next(result for result in results if result.operation == "steer")
        assert steering.message == MessageResult("accepted", "dialogue-turn")
        assert question.is_live
        route = panel.request_views[token].question_inputs["route"]
        tuple(route.choices.values())[1].click()
        _answer_editor(panel, token, "note").setPlainText("補給後に現在地を確認する")
        panel.request_views[token].submit_button.click()
        qtbot.waitUntil(lambda: len(panel.requests) == 2)
        approval = next(
            record for record in panel.requests.values() if record.request.kind == "command"
        )
        assert "Write-Output fixture" in panel.transcript.toPlainText()
        approval.decision_buttons["decline"].click()
        qtbot.waitUntil(lambda: "GIGAS_DIALOGUE_OK" in panel.transcript.toPlainText())
        qtbot.waitUntil(lambda: panel.status.text() == "待機")
        assert question.state == "sent"
        assert approval.state == "sent"
        assert "拒否" in panel.transcript.toPlainText()
        assert "経路確認" in panel.plan_panel.view.toPlainText()
        assert panel.plan_panel.isVisible()
        assert approval.controls is not None
        assert not approval.controls.isVisible()
    finally:
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())
    assert service.wait(0)
    assert service.shutdown_error == ""


def test_public_items_and_current_plan_keep_turn_boundaries(qtbot: QtBot) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.activate(1)
    panel.handle_result(ServiceResult(1, "connect"))
    assert panel.plan_panel.view.toPlainText() == "—"
    panel.handle_event(ServiceEvent(1, TurnStarted("old")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running", "m", "low", True)))
    panel.handle_event(
        ServiceEvent(1, PlanEvent("old", (PlanStep("調査", "inProgress"),), "入口を確認"))
    )
    assert "調査" in panel.plan_panel.view.toPlainText()
    assert "入口を確認" in panel.plan_panel.view.toPlainText()
    assert "入口を確認" not in panel.transcript.toPlainText()
    assert "調査" not in panel.transcript.toPlainText()
    panel.handle_event(ServiceEvent(1, PlanEvent("old", ())))
    assert panel.plan_panel.view.toPlainText() == "—"
    panel.handle_event(ServiceEvent(1, PlanEvent("old", (PlanStep("調査", "inProgress"),))))
    panel.handle_event(ServiceEvent(1, ReasoningEvent("old", "shared", ("公開要約",))))
    panel.handle_event(
        ServiceEvent(
            1, ActivityEvent("old", "shared", "mcpToolCall", "gigas/observe", "inProgress")
        )
    )
    panel.handle_event(
        ServiceEvent(
            1,
            ActivityEvent("old", "shared", "mcpToolCall", "gigas/observe", "completed", "観測済み"),
        )
    )
    history = panel.transcript.toPlainText()
    assert history.count("gigas/observe") == 1
    assert "観測済み" in history
    assert "公開要約" in history
    panel.handle_event(ServiceEvent(1, SessionStatus("ready", "m", "low", True)))
    assert "調査" in panel.plan_panel.view.toPlainText()
    assert "調査" in panel.transcript.toPlainText()
    assert panel.plan_panel.status.text() == "終了"
    assert "進行中" not in panel.plan_panel.view.toPlainText()
    assert "進行中" not in panel.transcript.toPlainText()
    assert (
        panel.plan_panel.view.document().find("調査").charFormat().fontWeight() != QFont.Weight.Bold
    )
    panel.handle_event(ServiceEvent(1, SessionStatus("closed")))
    assert panel.plan_panel.status.text() == "終了"
    panel.handle_event(ServiceEvent(1, SessionStatus("ready", "m", "low", True)))
    panel.handle_event(ServiceEvent(1, TurnStarted("new")))
    assert "調査" in panel.transcript.toPlainText()
    panel.handle_event(ServiceEvent(1, SessionStatus("running", "m", "low", True)))
    assert panel.plan_panel.view.toPlainText() == "—"
    panel.handle_event(ServiceEvent(1, PlanEvent("new", (PlanStep("移動", "pending"),))))
    panel.handle_event(ServiceEvent(1, ReasoningEvent("new", "shared", ("新しい要約",))))
    assert "移動" in panel.plan_panel.view.toPlainText()
    assert "調査" in panel.transcript.toPlainText()
    assert "新しい要約" in panel.transcript.toPlainText()
    assert "公開要約" in panel.transcript.toPlainText()
    panel.interrupt()
    panel.handle_event(ServiceEvent(1, PlanEvent("new", (PlanStep("停止後の通知", "inProgress"),))))
    assert "移動" in panel.plan_panel.view.toPlainText()
    assert "停止後の通知" in panel.transcript.toPlainText()
    panel.handle_event(ServiceEvent(1, SessionStatus("stopped", "m", "low", True)))
    assert "移動" in panel.plan_panel.view.toPlainText()
    assert panel.plan_panel.status.text() == "中断"
    assert "停止後の通知" in panel.transcript.toPlainText()
    assert "移動" not in panel.transcript.toPlainText()
    panel.handle_result(ServiceResult(1, "stop"))
    panel.prompt.setPlainText("次の依頼")
    panel.send_button.click()
    panel.handle_event(ServiceEvent(1, SessionStatus("starting")))
    panel.handle_event(ServiceEvent(1, TurnStarted("after-stop")))
    assert "停止後の通知" in panel.transcript.toPlainText()
    assert "進行中" not in panel.transcript.toPlainText()
    assert (
        panel.transcript.document().find("停止後の通知").charFormat().fontWeight()
        != QFont.Weight.Bold
    )
    panel.activate(2)
    panel.handle_event(ServiceEvent(1, PlanEvent("new", (PlanStep("旧接続", "pending"),))))
    assert panel.transcript.toPlainText() == ""
    assert panel.plan_panel.view.toPlainText() == "—"


def test_current_plan_updates_keep_selection_formatting_and_viewport(qtbot: QtBot) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.resize(500, 600)
    panel.show()
    panel.activate(1)
    panel.handle_event(ServiceEvent(1, TurnStarted("turn")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running", "m", "low", True)))
    steps = (
        PlanStep("入口を観測", "completed"),
        PlanStep("経路を確認", "inProgress"),
        *(PlanStep(f"計画 {index}", "pending") for index in range(40)),
    )
    panel.handle_event(ServiceEvent(1, PlanEvent("turn", steps)))
    panel.plan_panel.resize(400, 200)
    panel.plan_panel.show()
    plan = panel.plan_panel.view
    assert "入口を観測" in plan.toPlainText()
    assert "完了" in plan.toPlainText()
    assert "経路を確認" in plan.toPlainText()
    assert "進行中" in plan.toPlainText()
    assert "計画 20" in plan.toPlainText()
    assert plan.document().find("経路を確認").charFormat().fontWeight() == QFont.Weight.Bold
    assert plan.document().find("計画 20").charFormat().fontWeight() == QFont.Weight.Normal
    plan.setTextCursor(plan.document().find("計画 20"))
    plan.ensureCursorVisible()
    visible = plan.cursorForPosition(QPoint(0, 0))
    visible_y = plan.cursorRect(visible).top()
    panel.handle_event(ServiceEvent(1, PlanEvent("turn", steps)))
    panel.handle_event(
        ServiceEvent(
            1, PlanEvent("turn", (PlanStep("入口をもう一度観測", "completed"), *steps[1:]))
        )
    )
    assert plan.textCursor().selectedText() == "計画 20"
    assert abs(plan.cursorRect(visible).top() - visible_y) <= 1
    plan.copy()
    assert QApplication.clipboard().text() == "計画 20"


@pytest.mark.parametrize("reader", ["tail", "reading", "selected"])
def test_plan_updates_do_not_resize_history_or_move_reading_position(
    qtbot: QtBot, reader: str
) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.resize(400, 400)
    panel.show()
    panel.activate(1)
    panel.handle_result(ServiceResult(1, "connect"))
    panel.handle_event(ServiceEvent(1, TurnStarted("turn")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running", "m", "low", True)))
    qtbot.wait(1)
    panel.handle_event(
        ServiceEvent(
            1, TextEvent("user", "\n".join(f"ユーザー本文 {i}" for i in range(50)), "user")
        )
    )
    history = panel.transcript
    bar = history.verticalScrollBar()
    assert bar.value() == bar.maximum()
    if reader == "reading":
        bar.setValue(bar.maximum() // 2)
    elif reader == "selected":
        selected = history.document().find("ユーザー本文 20")
        selected.setPosition(selected.selectionEnd())
        selected.movePosition(
            QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveMode.KeepAnchor
        )
        history.setTextCursor(selected)
        history.ensureCursorVisible()
    visible = history.cursorForPosition(QPoint(0, 1))
    visible_y = history.cursorRect(visible).top()
    previous_height = history.viewport().height()
    for steps in ((PlanStep("確認", "inProgress"),), ()):
        panel.handle_event(ServiceEvent(1, PlanEvent("turn", steps)))
        qtbot.wait(1)
        assert history.viewport().height() == previous_height
        previous_height = history.viewport().height()
        panel.handle_event(ServiceEvent(1, TextEvent("final", "最終応答", replace=True)))
        qtbot.wait(1)
        if reader == "tail":
            assert bar.value() == bar.maximum()
        else:
            assert abs(history.cursorRect(visible).top() - visible_y) <= 1
            if reader == "selected":
                assert history.textCursor().selectedText() == "ユーザー本文 20"
                assert history.textCursor().anchor() > history.textCursor().position()
    assert "最終応答" in history.toPlainText()


def test_history_item_updates_preserve_reverse_selection_and_viewport(qtbot: QtBot) -> None:
    history = Transcript()
    qtbot.addWidget(history)
    history.resize(420, 300)
    history.show()
    assert isinstance(history, QTextBrowser)
    assert not history.openExternalLinks()
    assert not history.openLinks()
    history.update_item("user", "最初の指示 <b>文字</b>\r\n次行", kind="user")
    for index in range(100):
        history.update_item(str(index), f"本文 {index} 😀\n続き")
    qtbot.waitUntil(lambda: history.verticalScrollBar().maximum() > 0)
    target = history.document().find("本文 50 😀")
    target.setPosition(target.selectionEnd())
    target.movePosition(QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveMode.KeepAnchor)
    history.setTextCursor(target)
    history.ensureCursorVisible()
    visible = history.cursorForPosition(QPoint(0, 0))
    visible_y = history.cursorRect(visible).top()
    history.update_item("0", "以前の本文を差し替え\n追加行\n😀")
    history.update_item("99", "\n受信中の追記", append=True)
    assert history.textCursor().selectedText() == "本文 50 😀"
    assert history.textCursor().anchor() > history.textCursor().position()
    assert abs(history.cursorRect(visible).top() - visible_y) <= 1
    history.copy()
    assert QApplication.clipboard().text() == "本文 50 😀"
    unselected = history.textCursor()
    unselected.clearSelection()
    history.setTextCursor(unselected)
    visible = history.cursorForPosition(QPoint(0, 0))
    visible_y = history.cursorRect(visible).top()
    history.update_item("0", "さらに前方で行数が変わる\n" * 5)
    history.update_item("99", "\n末尾の次の追記", append=True)
    qtbot.wait(1)  # Include the following event-loop pass, not only synchronous layout.
    assert abs(history.cursorRect(visible).top() - visible_y) <= 1
    assert history.verticalScrollBar().value() < history.verticalScrollBar().maximum()
    assert "<b>文字</b>" in history.toPlainText()
    user = history.document().find("最初の指示")
    assistant = history.document().find("本文 1 😀")
    assert user.blockFormat().background() != assistant.blockFormat().background()


def test_history_replacement_keeps_unicode_boundaries_and_follows_only_unselected_tail(
    qtbot: QtBot,
) -> None:
    history = Transcript()
    qtbot.addWidget(history)
    history.resize(400, 180)
    history.show()
    history.update_item("first", "abcde\r\n硬い改行\u2028柔らかい改行\u00a0空白")
    selected = history.document().find("bc")
    history.setTextCursor(selected)
    history.update_item("first", "a😀e\r\n硬い改行\u2028柔らかい改行\u00a0空白")
    assert history.textCursor().selectedText() == "😀"
    history.copy()
    assert QApplication.clipboard().text() == "😀"
    assert "硬い改行\u2028柔らかい改行\u00a0空白" in history.document().toRawText()
    selected = history.textCursor()
    selected.clearSelection()
    selected.movePosition(QTextCursor.MoveOperation.End)
    history.setTextCursor(selected)
    history.verticalScrollBar().setValue(history.verticalScrollBar().maximum())
    history.update_item("tail", "\n".join(f"追記 {index}" for index in range(60)))
    assert history.verticalScrollBar().value() == history.verticalScrollBar().maximum()
    history.clear()
    history.update_item("first", "新しい履歴\r")
    history.update_item("first", "\n次行", append=True)
    assert "a😀e" not in history.toPlainText()
    assert history.toPlainText().count("新しい履歴") == 1
    assert "新しい履歴\n次行" in history.toPlainText()


def test_hidden_reasoning_is_not_copied_and_receives_updates(qtbot: QtBot) -> None:
    history = Transcript()
    qtbot.addWidget(history)
    history.update_item("before", "前の応答")
    history.update_item("summary", "公開要約 😀", kind="reasoning")
    history.update_item("after", "後の応答")
    history.setTextCursor(history.document().find("公開要約"))
    history.set_reasoning_visible(False)
    assert not history.textCursor().hasSelection()
    assert history.textCursor().position() == history.document().find("Codex", 1).selectionStart()
    history.update_item("summary", "更新された要約", kind="reasoning")
    history.selectAll()
    history.copy()
    assert "要約" not in QApplication.clipboard().text()
    assert "前の応答" in QApplication.clipboard().text()
    assert "後の応答" in QApplication.clipboard().text()
    history.setTextCursor(history.document().find("後の応答"))
    history.set_reasoning_visible(True)
    assert history.textCursor().selectedText() == "後の応答"
    assert "更新された要約" in history.toPlainText()
    assert "公開要約" not in history.toPlainText()
    history.resize(400, 180)
    history.show()
    history.set_reasoning_visible(False)
    history.update_item("before", "前の応答\n" * 20)
    history.update_item("after", "後の応答\n" * 30)
    visible = history.document().find("Codex", 1)
    visible.setPosition(visible.selectionStart())
    history.setTextCursor(visible)
    history.ensureCursorVisible()
    bar = history.verticalScrollBar()
    bar.setValue(bar.value() + history.cursorRect(visible).top())
    visible_y = history.cursorRect(visible).top()
    history.set_reasoning_visible(True)
    assert abs(history.cursorRect(visible).top() - visible_y) <= 1


@pytest.mark.parametrize("composing", [False, True])
def test_start_notification_keeps_draft_until_acceptance_result(
    qtbot: QtBot, *, composing: bool
) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.activate(1)
    panel.handle_result(ServiceResult(1, "connect"))
    panel.handle_event(ServiceEvent(1, SessionStatus("ready", "m", "low", True)))
    panel.prompt.setPlainText("受付を確認する原稿")
    panel.send_button.click()
    panel.handle_event(ServiceEvent(1, TurnStarted("turn")))
    assert panel.prompt.toPlainText() == "受付を確認する原稿"
    if composing:
        QApplication.sendEvent(panel.prompt, QInputMethodEvent("次のへんかん", []))
    panel.handle_result(ServiceResult(1, "send", message=MessageResult("accepted", "turn")))
    assert panel.prompt.toPlainText() == ("受付を確認する原稿" if composing else "")
    if composing:
        layout = panel.prompt.document().firstBlock().layout()
        assert layout is not None
        assert layout.preeditAreaText() == "次のへんかん"


@pytest.mark.parametrize("outcome", ["accepted", "not_sent", "unknown"])
@pytest.mark.parametrize("edited", [False, True])
def test_running_turn_accepts_one_targeted_instruction(
    qtbot: QtBot, outcome: str, *, edited: bool
) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.show()
    assert panel.composer.isAncestorOf(panel.prompt)
    panel.activate(1)
    panel.handle_result(ServiceResult(1, "connect"))
    panel.handle_event(ServiceEvent(1, TurnStarted("turn")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running", "m", "low", True)))
    assert panel.interrupt_button.isVisible()
    assert not panel.send_button.isVisible()
    stops: list[bool] = []
    panel.interrupt_requested.connect(lambda: stops.append(True))
    qtbot.keyClick(panel.prompt, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert not stops
    panel.prompt.setPlainText("補給後に現在地を確認する")
    assert panel.send_button.isVisible()
    assert panel.interrupt_button.isVisible()
    assert panel.send_button.isEnabled()
    instructions: list[tuple[str, str]] = []
    panel.steer_requested.connect(lambda text, turn: instructions.append((text, turn)))
    panel.send_button.click()
    panel.send_button.click()
    assert instructions == [("補給後に現在地を確認する", "turn")]
    if edited:
        panel.prompt.setPlainText("次の原稿")
    panel.handle_event(ServiceEvent(1, SessionStatus("ready", "m", "low", True)))
    result = {
        "accepted": MessageResult("accepted", "turn"),
        "not_sent": MessageResult("not_sent"),
        "unknown": MessageResult("unknown"),
    }[outcome]
    panel.handle_result(ServiceResult(1, "steer", message=result))
    expected = "次の原稿" if edited else "" if outcome == "accepted" else "補給後に現在地を確認する"
    assert panel.prompt.toPlainText() == expected
    assert instructions == [("補給後に現在地を確認する", "turn")]


def test_late_turn_notifications_after_stop_do_not_reopen_instruction_delivery(
    qtbot: QtBot,
) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.activate(1)
    panel.handle_result(ServiceResult(1, "connect"))
    panel.handle_event(ServiceEvent(1, TurnStarted("old")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running", "m", "low", True)))
    instructions: list[tuple[str, str]] = []
    panel.steer_requested.connect(lambda text, turn: instructions.append((text, turn)))
    panel.prompt.setPlainText("現在地を確認")
    panel.send_button.click()
    panel.interrupt()
    panel.handle_event(ServiceEvent(1, TurnStarted("old")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running", "m", "low", True)))
    panel.handle_result(ServiceResult(1, "steer", message=MessageResult("unknown")))
    assert panel.prompt.toPlainText() == "現在地を確認"
    assert not panel.send_button.isEnabled()
    panel.handle_result(ServiceResult(1, "stop"))
    panel.handle_event(ServiceEvent(1, SessionStatus("stopped", "m", "low", True)))
    panel.send_button.click()
    panel.handle_event(ServiceEvent(1, TurnStarted("new")))
    panel.handle_result(ServiceResult(1, "send", message=MessageResult("accepted", "new")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running", "m", "low", True)))
    panel.prompt.setPlainText("次の指示")
    panel.send_button.click()
    assert instructions == [("現在地を確認", "old"), ("次の指示", "new")]


@pytest.mark.parametrize("edited_draft", [False, True])
def test_panel_login_text_send_failure_interrupt_and_disconnect(
    qtbot: QtBot, *, edited_draft: bool
) -> None:
    opened: list[QUrl] = []
    panel = ConnectionPanel(open_url=lambda url: bool(opened.append(url)))
    qtbot.addWidget(panel)
    panel.show()
    sent: list[str] = []
    stops: list[bool] = []
    panel.send_requested.connect(sent.append)
    panel.interrupt_requested.connect(lambda: stops.append(True))
    panel.activate(1)
    panel.handle_event(ServiceEvent(1, SessionStatus("login_required")))
    panel.handle_result(ServiceResult(1, "connect"))
    assert panel.login_action.isEnabled()
    assert not panel.send_button.isEnabled()
    panel.handle_event(ServiceEvent(1, SessionStatus("authenticating")))
    panel.handle_event(ServiceEvent(1, LoginEvent("https://example.org/login")))
    assert not opened
    panel.open_login_button.click()
    assert opened == [QUrl("https://example.org/login")]
    panel.handle_event(ServiceEvent(1, SessionStatus("ready", "chosen", "high", True)))
    assert panel.login_url.text() == ""
    assert not panel.open_login_button.isVisible()
    panel.prompt.setPlainText("hello")
    panel.send_button.click()
    panel.send_button.click()
    assert sent == ["hello"]
    panel.handle_result(ServiceResult(1, "send", "送信失敗"))
    assert panel.prompt.toPlainText() == "hello"
    assert panel.send_button.isEnabled()
    panel.send_button.click()
    panel.handle_event(ServiceEvent(1, SessionStatus("starting", "chosen", "high", True)))
    panel.interrupt_button.click()
    assert stops == [True]
    panel.handle_event(ServiceEvent(1, TextEvent("answer", "<b>A")))
    panel.handle_event(ServiceEvent(1, TextEvent("answer", "B</b>")))
    assert "<b>AB</b>" in panel.transcript.toPlainText()
    panel.handle_event(ServiceEvent(1, TextEvent("answer", "final", replace=True)))
    assert "final" in panel.transcript.toPlainText()
    assert "AB" not in panel.transcript.toPlainText()
    panel.handle_result(ServiceResult(1, "send"))
    assert panel.prompt.toPlainText() == "hello"  # A stopped pre-acceptance send also returns.
    if edited_draft:
        panel.prompt.setPlainText("次の原稿")
    panel.handle_event(ServiceEvent(1, TurnStarted("accepted-turn")))
    assert panel.prompt.toPlainText() == ("次の原稿" if edited_draft else "hello")
    panel.disconnect_action.trigger()
    panel.handle_event(ServiceEvent(1, SessionStatus("closed")))
    assert not panel.connect_action.isEnabled()
    panel.handle_result(ServiceResult(1, "disconnect"))
    assert panel.connect_action.isEnabled()


def test_shell_connect_uses_saved_snapshot_without_automatic_start(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = CodexServiceThread(tmp_path, CaptureReader(CaptureSnapshot))
    shell = ApplicationShell(tmp_path / "settings.toml", service=service)
    snapshots: list[ConnectionOptions] = []

    def capture(options: ConnectionOptions) -> int:
        snapshots.append(options)
        return len(snapshots)

    # Replace only the GUI command boundary; no runtime launches in this test.
    monkeypatch.setattr(service, "connect_runtime", capture)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        assert not snapshots
        shell.open_settings()
        assert shell.dialog is not None
        shell.dialog.model.setText("unsaved")
        assert not snapshots
        shell.dialog.tabs.setCurrentIndex(shell.dialog.codex_tab)
        panel = shell.window.connection
        assert not panel.account_button.isEnabled()
        panel.account_button.click()
        assert not snapshots
        panel.server_button.setFocus()
        qtbot.keyClick(panel.server_button, Qt.Key.Key_Return)
        assert len(snapshots) == 1
        assert snapshots[0] == ConnectionOptions(tmp_path / "runtime")
        panel.handle_event(ServiceEvent(1, SessionStatus("login_required")))
        panel.handle_result(ServiceResult(1, "connect"))
        logins: list[bool] = []
        panel.login_requested.connect(lambda: logins.append(True))
        panel.account_button.click()
        panel.account_button.click()
        assert logins == [True]
        assert len(snapshots) == 1
        shell.dialog.close_button.click()
        assert not shell.dialog.isVisible()
        panel.handle_event(
            ServiceEvent(1, AccountInfo("returned@example.test", "plus", None, True))
        )
        shell.open_settings()
        assert shell.dialog.model.text() != "unsaved"
        assert "returned@example.test" in panel.codex_settings.account.text()
        assert len(snapshots) == 1
    finally:
        assert shell.dialog is not None
        shell.dialog.set_values(shell.baseline)
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())


def test_questions_require_each_answer_and_expire_before_late_gui_actions(qtbot: QtBot) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.activate(5)
    panel.show()
    panel.handle_event(ServiceEvent(5, TurnStarted("turn")))
    panel.handle_event(ServiceEvent(5, SessionStatus("running", "m", "low", True)))
    answers: list[object] = []
    panel.answer_requested.connect(answers.append)
    request = HumanRequest(
        "token",
        7,
        "thread",
        "turn",
        "question",
        questions=(
            Question(
                "choice",
                "方針",
                "どれにしますか?",
                (QuestionOption("A", "説明A"), QuestionOption("B", "説明B")),
            ),
            Question("text", "装備", "使用する装備は?"),
            Question(
                "other", "経路", "別の経路?", (QuestionOption("既定", "説明"),), is_other=True
            ),
        ),
    )
    panel.handle_event(ServiceEvent(5, request))
    record = panel.requests["token"]
    view = panel.request_views["token"]
    inputs = view.question_inputs
    assert not any(button.isChecked() for button in inputs["choice"].choices.values())
    assert not panel.send_button.isEnabled()
    assert not view.submit_button.isEnabled()
    inputs["choice"].choices["B"].click()
    assert inputs["text"].editor is not None
    inputs["text"].editor.setPlainText("弓")
    assert inputs["other"].editor is not None
    assert inputs["other"].other_button is not None
    inputs["other"].other_button.click()
    inputs["other"].editor.setPlainText("custom; stop before the gate")
    qtbot.keyClick(inputs["choice"].choices["B"], Qt.Key.Key_Return)
    assert not answers
    view.submit_button.click()
    view.submit_button.click()
    assert len(answers) == 1
    assert answers[0] == AnswerSubmission(
        "token", answers={"choice": "B", "text": "弓", "other": "custom; stop before the gate"}
    )
    panel.handle_event(ServiceEvent(5, RequestResolved("token")))
    assert not record.is_live
    assert record.drafts["text"] == "弓"
    assert inputs["text"].editor.toPlainText() == "弓"
    panel.handle_result(ServiceResult(5, "answer", answer=AnswerResult("token", "sent")))
    assert not record.drafts
    assert panel.transcript.toPlainText().strip() == "B"
    view.expand_button.click()
    assert "A\n説明A" in panel.transcript.toPlainText()
    assert "B\n説明B" in panel.transcript.toPlainText()
    assert "弓" in panel.transcript.toPlainText()
    assert "custom; stop before the gate" in panel.transcript.toPlainText()
    assert not view.submit_button.isVisible()
    assert panel.prompt.toPlainText() == ""
    panel.handle_event(
        ServiceEvent(
            5,
            HumanRequest(
                "pending",
                8,
                "thread",
                "turn",
                "question",
                questions=(Question("late", "経路", "次の経路は?"),),
            ),
        )
    )
    pending_record = panel.requests["pending"]
    pending_view = panel.request_views["pending"]
    pending_editor = pending_view.question_inputs["late"].editor
    assert pending_editor is not None
    pending_editor.setPlainText("北側")
    assert pending_view.submit_button.isEnabled()
    panel.activate(6)
    assert not pending_record.is_live
    pending_view.submit_button.click()
    assert len(answers) == 1
    panel.handle_event(ServiceEvent(5, request))
    assert not panel.requests


@pytest.mark.parametrize("outcome", ["sent", "not_sent", "unknown"])
def test_question_cards_isolate_drafts_and_late_message_acceptance(
    qtbot: QtBot, outcome: str
) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.activate(1)
    panel.handle_result(ServiceResult(1, "connect"))
    panel.handle_event(ServiceEvent(1, TurnStarted("turn")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running", "m", "low", True)))
    panel.prompt.setPlainText("same text")
    panel.send_button.click()
    for token in ("first", "second"):
        panel.handle_event(
            ServiceEvent(
                1,
                HumanRequest(
                    token,
                    token,
                    "thread",
                    "turn",
                    "question",
                    questions=(Question("route", "経路", f"{token}の経路は?"),),
                ),
            )
        )
    first = _answer_editor(panel, "first", "route")
    second = _answer_editor(panel, "second", "route")
    assert first.toPlainText() == second.toPlainText() == ""
    first.setPlainText("same text")
    second.setPlainText("second draft")
    panel.handle_result(ServiceResult(1, "steer", message=MessageResult("accepted", "turn")))
    assert panel.prompt.toPlainText() == ""
    assert first.toPlainText() == "same text"
    sent: list[object] = []
    panel.answer_requested.connect(sent.append)
    panel.request_views["first"].submit_button.click()
    assert sent == [AnswerSubmission("first", answers={"route": "same text"})]
    result = {
        "sent": AnswerResult("first", "sent"),
        "not_sent": AnswerResult("first", "not_sent"),
        "unknown": AnswerResult("first", "unknown"),
    }[outcome]
    panel.handle_result(ServiceResult(1, "answer", answer=result))
    assert second.toPlainText() == "second draft"
    if outcome != "sent":
        assert first.toPlainText() == "same text"
        assert first.isReadOnly()
        assert not panel.request_views["first"].submit_button.isEnabled()
    panel.handle_event(ServiceEvent(1, RequestResolved("second")))
    assert second.toPlainText() == "second draft"
    assert second.isReadOnly()
    assert not panel.request_views["second"].submit_button.isEnabled()
    assert panel.prompt.toPlainText() == ""
    assert not panel.prompt.isReadOnly()


def test_composition_keeps_message_and_question_ctrl_enter_waits(qtbot: QtBot) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.activate(1)
    panel.handle_event(ServiceEvent(1, TurnStarted("turn")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running", "m", "low", True)))
    QApplication.sendEvent(panel.prompt, QInputMethodEvent("へんかん", []))
    panel.handle_event(
        ServiceEvent(
            1,
            HumanRequest(
                "route",
                1,
                "thread",
                "turn",
                "question",
                questions=(Question("value", "経路", "どの道を通るか"),),
            ),
        )
    )
    assert panel.prompt.is_composing
    layout = panel.prompt.document().firstBlock().layout()
    assert layout is not None
    assert layout.preeditAreaText() == "へんかん"
    QApplication.sendEvent(panel.prompt, QInputMethodEvent("", []))
    editor = _answer_editor(panel, "route", "value")
    editor.setPlainText("東")
    sent: list[object] = []
    panel.answer_requested.connect(sent.append)
    QApplication.sendEvent(editor, QInputMethodEvent("へんかん", []))
    qtbot.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert not sent
    QApplication.sendEvent(editor, QInputMethodEvent("", []))
    editor.moveCursor(QTextCursor.MoveOperation.End)
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    assert not sent
    assert editor.toPlainText() == "東\n"
    qtbot.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert sent == [AnswerSubmission("route", answers={"value": "東\n"})]
    panel.handle_event(
        ServiceEvent(
            1,
            HumanRequest(
                "free",
                2,
                "thread",
                "turn",
                "question",
                questions=(Question("value", "経路", "次の経路"),),
            ),
        )
    )
    free = _answer_editor(panel, "free", "value")
    QApplication.sendEvent(free, QInputMethodEvent("未確定の回答", []))
    panel.handle_event(ServiceEvent(1, RequestResolved("free")))
    panel.prompt.setPlainText("次の指示")
    instructions: list[tuple[str, str]] = []
    panel.steer_requested.connect(lambda text, turn: instructions.append((text, turn)))
    qtbot.keyClick(panel.prompt, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert instructions == [("次の指示", "turn")]


@pytest.mark.parametrize("ending", ["resolved", "stop", "disconnect", "terminal"])
def test_approval_is_explicit_and_invalidated_locally(qtbot: QtBot, ending: str) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.activate(1)
    panel.handle_event(ServiceEvent(1, SessionStatus("running", "m", "low", True)))
    answers: list[object] = []
    panel.answer_requested.connect(answers.append)
    request = HumanRequest(
        "approval",
        "7",
        "thread",
        "turn",
        "command",
        details="echo test",
        decisions=("accept", "decline", "cancel"),
    )
    panel.handle_event(ServiceEvent(1, request))
    card = panel.requests["approval"]
    assert not answers
    assert set(card.decision_buttons) == {"accept", "decline"}
    assert "cancel" in panel.request_views["approval"].decision_actions
    if ending == "resolved":
        panel.handle_event(ServiceEvent(1, RequestResolved("approval")))
    elif ending == "stop":
        panel.interrupt()
    elif ending == "disconnect":
        panel.disconnect_action.trigger()
    else:
        panel.handle_event(ServiceEvent(1, SessionStatus("stopped")))
    card.decision_buttons["accept"].click()
    assert not answers
    assert not card.is_live
    panel.handle_event(ServiceEvent(1, request))
    assert not panel.requests["approval"].is_live


def test_approval_sends_only_the_clicked_advertised_decision(qtbot: QtBot) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.activate(1)
    panel.handle_event(ServiceEvent(1, SessionStatus("running", "m", "low", True)))
    answers: list[object] = []
    panel.answer_requested.connect(answers.append)
    panel.handle_event(
        ServiceEvent(
            1,
            HumanRequest(
                "file",
                8,
                "thread",
                "turn",
                "file",
                details="a.txt\n+new",
                decisions=("accept", "decline"),
            ),
        )
    )
    card = panel.requests["file"]
    card.decision_buttons["decline"].click()
    card.decision_buttons["accept"].click()
    assert answers == [AnswerSubmission("file", decision="decline")]


@pytest.mark.parametrize("connection_created", [False, True])
def test_connection_failure_persists_until_explicit_disconnect(
    qtbot: QtBot, connection_created: bool
) -> None:
    panel = ConnectionPanel()
    failures: list[Notice] = []
    panel.failed.connect(failures.append)
    qtbot.addWidget(panel)
    panel.activate(1)
    if connection_created:
        panel.handle_event(ServiceEvent(1, SessionStatus("error", detail="認証方式エラー")))
    panel.handle_result(ServiceResult(1, "connect", "接続失敗"))
    panel.set_available(False)
    panel.set_available(True)
    assert panel.status.text() == "エラー"
    assert failures[-1].details == "接続失敗"
    assert not panel.connect_action.isEnabled()
    assert not panel.send_button.isEnabled()
    assert panel.disconnect_action.isEnabled()
    panel.disconnect_action.trigger()
    assert not panel.connect_action.isEnabled()
    panel.handle_result(ServiceResult(1, "disconnect"))
    assert panel.connect_action.isEnabled()


@pytest.mark.parametrize("model", ["", "first"])
def test_invalid_defaults_keep_login_and_selection_but_block_send_and_resume(
    qtbot: QtBot, model: str
) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.activate(1)
    choices = (ModelChoice("first", ("low", "high"), "low"),)
    failures: list[Notice] = []
    panel.failed.connect(failures.append)
    panel.handle_event(ServiceEvent(1, ModelSelection(choices, model, "")))
    panel.handle_event(ServiceEvent(1, SessionStatus("login_required", model, "")))
    panel.handle_result(ServiceResult(1, "connect"))
    panel.prompt.setPlainText("原稿を保持")
    assert panel.login_action.isEnabled()
    assert not panel.send_button.isEnabled()
    invalid = SessionStatus("ready", model, "", True, "invalid-default-fixture")
    panel.handle_event(ServiceEvent(1, invalid))
    panel.handle_event(ServiceEvent(1, invalid))
    assert len(failures) == 1
    assert failures[0].details == invalid.detail
    panel.handle_event(ServiceEvent(1, GoalEvent(GoalState("保持した目標", "paused"))))
    assert panel.disconnect_action.isEnabled()
    assert panel.plan_panel.clear_button.isEnabled()
    assert not panel.plan_panel.resume_button.isEnabled()
    assert not panel.send_button.isEnabled()
    panel.composer.goal_mode.click()
    assert not panel.send_button.isEnabled()
    composer = panel.composer
    assert composer.model_button.isEnabled()
    composer.model_button.click()
    assert composer.popup.isVisible()
    assert composer.model_choice.currentIndex() == (0 if model else -1)
    assert composer.effort_choice.currentIndex() == -1
    picked: list[tuple[str, str]] = []
    panel.model_requested.connect(lambda selected, effort: picked.append((selected, effort)))
    if model:
        composer.effort_choice.setCurrentText("low")
        composer.effort_choice.activated.emit(composer.effort_choice.currentIndex())
    else:
        composer.model_choice.setCurrentIndex(0)
        composer.model_choice.activated.emit(0)
    assert picked == [("first", "low")]
    assert not panel.send_button.isEnabled()
    panel.handle_event(ServiceEvent(1, ModelSelection(choices, "first", "low")))
    panel.handle_event(ServiceEvent(1, SessionStatus("ready", "first", "low", True)))
    panel.handle_result(ServiceResult(1, "select_model"))
    assert panel.send_button.isEnabled()
    assert panel.plan_panel.resume_button.isEnabled()
    assert panel.prompt.toPlainText() == "原稿を保持"

    panel.activate(2)
    panel.handle_event(ServiceEvent(2, ModelSelection((), "", "")))
    panel.handle_event(ServiceEvent(2, SessionStatus("ready", authenticated=True)))
    panel.handle_result(ServiceResult(2, "connect"))
    assert panel.disconnect_action.isEnabled()
    assert not composer.model_button.isEnabled()
    assert not panel.send_button.isEnabled()


def test_model_popup_selects_next_turn_and_preserves_draft(qtbot: QtBot) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.resize(400, 500)
    panel.show()
    assert not panel.composer.model_button.isEnabled()
    panel.activate(1)
    selection = ModelSelection(
        (
            ModelChoice("first", ("low", "high"), "low"),
            ModelChoice("second", ("medium",), "medium"),
        ),
        "first",
        "high",
    )
    panel.handle_event(ServiceEvent(1, selection))
    panel.handle_event(ServiceEvent(1, SessionStatus("running", "first", "high", True)))
    panel.handle_result(ServiceResult(1, "connect"))
    panel.prompt.setPlainText("原稿を残す")
    composer = panel.composer
    picked: list[tuple[str, str]] = []
    panel.model_requested.connect(lambda model, effort: picked.append((model, effort)))
    composer.model_button.click()
    assert composer.popup.isVisible()
    assert picked == []
    composer.effort_choice.setCurrentText("low")
    composer.effort_choice.activated.emit(composer.effort_choice.currentIndex())
    assert picked == [("first", "low")]
    panel.handle_event(ServiceEvent(1, ModelSelection(selection.choices, "first", "low")))
    panel.handle_result(ServiceResult(1, "select_model"))
    picked.clear()
    composer.model_choice.setCurrentText("second")
    composer.model_choice.activated.emit(composer.model_choice.currentIndex())
    assert composer.effort_choice.count() == 1
    assert picked == [("second", "medium")]
    assert composer.model_button.text() == "first · low"
    assert not composer.model_button.isEnabled()
    assert composer.interrupt_button.isEnabled()
    panel.handle_event(ServiceEvent(1, ModelSelection(selection.choices, "second", "medium")))
    panel.handle_result(ServiceResult(1, "select_model"))
    assert composer.model_button.text() == "second · medium"
    assert composer.popup.isVisible()
    qtbot.keyClick(composer.popup, Qt.Key.Key_Escape)
    assert not composer.popup.isVisible()
    assert picked == [("second", "medium")]
    assert panel.prompt.toPlainText() == "原稿を残す"
    panel.handle_event(ServiceEvent(1, SessionStatus("closed")))
    assert not composer.model_button.isEnabled()
    assert composer.model_button.text() == "未接続"


@pytest.mark.parametrize("outcome", ["accepted", "not_sent", "unknown"])
@pytest.mark.parametrize("edited", [False, True])
def test_goal_mode_only_submits_when_sent_and_keeps_draft(
    qtbot: QtBot, outcome: str, *, edited: bool
) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.activate(7)
    panel.handle_event(ServiceEvent(7, SessionStatus("ready", "m", "low", True)))
    panel.handle_result(ServiceResult(7, "connect"))
    submitted: list[str] = []
    panel.goal_requested.connect(submitted.append)
    panel.prompt.setPlainText("橋まで進む")
    panel.composer.goal_mode.click()
    panel.handle_event(ServiceEvent(7, TurnStarted("ordinary")))
    panel.handle_event(ServiceEvent(7, SessionStatus("running", "m", "low", True)))
    assert not panel.send_button.isEnabled()
    panel.handle_event(ServiceEvent(7, SessionStatus("ready", "m", "low", True)))
    assert submitted == []
    assert panel.prompt.toPlainText() == "橋まで進む"
    panel.send_button.click()
    assert submitted == ["橋まで進む"]
    assert panel.prompt.toPlainText() == "橋まで進む"
    assert not panel.composer.goal_mode.isEnabled()
    if edited:
        panel.prompt.setPlainText("次の原稿")
    result = {
        "accepted": MessageResult("accepted"),
        "not_sent": MessageResult("not_sent"),
        "unknown": MessageResult("unknown"),
    }[outcome]
    panel.handle_result(ServiceResult(7, "goal_set", message=result))
    expected = "次の原稿" if edited else "" if outcome == "accepted" else "橋まで進む"
    assert panel.prompt.toPlainText() == expected
    assert panel.composer.goal_mode.isChecked() == (outcome != "accepted")
    assert submitted == ["橋まで進む"]
    if outcome == "accepted":
        panel.handle_event(ServiceEvent(7, GoalEvent(GoalState("橋まで進む", "active"))))
        panel.handle_event(ServiceEvent(7, TurnStarted("auto-1")))
        panel.handle_event(ServiceEvent(7, SessionStatus("running", "m", "low", True)))
        instructions: list[tuple[str, str]] = []
        panel.steer_requested.connect(lambda text, turn: instructions.append((text, turn)))
        panel.prompt.setPlainText("次の原稿")
        panel.send_button.click()
        assert instructions == [("次の原稿", "auto-1")]
        assert submitted == ["橋まで進む"]


def test_goal_turn_gaps_pause_and_explicit_resume(qtbot: QtBot) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.activate(3)
    panel.handle_event(ServiceEvent(3, SessionStatus("ready", "m", "low", True)))
    panel.handle_result(ServiceResult(3, "connect"))
    panel.handle_event(ServiceEvent(3, GoalEvent(GoalState("橋まで", "active"))))
    panel.prompt.setPlainText("追加指示")
    assert not panel.send_button.isEnabled()
    assert panel.interrupt_button.isEnabled()
    assert not panel.plan_panel.resume_button.isEnabled()
    panel.handle_event(ServiceEvent(3, TurnStarted("auto-1")))
    panel.handle_event(ServiceEvent(3, SessionStatus("running", "m", "low", True)))
    assert panel.send_button.isEnabled()
    panel.interrupt()
    assert not panel.send_button.isEnabled()
    panel.handle_event(ServiceEvent(3, GoalEvent(GoalState("橋まで", "paused"))))
    assert not panel.plan_panel.resume_button.isEnabled()
    panel.handle_event(ServiceEvent(3, SessionStatus("stopping", "m", "low", True)))
    panel.handle_result(ServiceResult(3, "stop"))
    assert not panel.plan_panel.resume_button.isEnabled()
    assert not panel.send_button.isEnabled()
    panel.handle_event(ServiceEvent(3, SessionStatus("stopped", "m", "low", True)))
    assert panel.plan_panel.resume_button.isEnabled()
    assert panel.send_button.isEnabled()
    resumed: list[bool] = []
    panel.goal_resume_requested.connect(lambda: resumed.append(True))
    panel.plan_panel.resume_button.click()
    assert resumed == [True]
    assert not panel.plan_panel.resume_button.isEnabled()
    assert panel.prompt.toPlainText() == "追加指示"
    panel.handle_event(ServiceEvent(3, GoalEvent(GoalState("橋まで", "active"))))
    panel.handle_result(ServiceResult(3, "goal_resume"))
    assert not panel.send_button.isEnabled()
    panel.plan_panel.clear_button.click()
    panel.handle_event(ServiceEvent(3, GoalEvent(None)))
    panel.handle_event(ServiceEvent(3, SessionStatus("stopped", "m", "low", True)))
    panel.handle_result(ServiceResult(3, "goal_clear"))
    assert panel.plan_panel.goal_value.toPlainText() == "—"
    assert not panel.plan_panel.resume_button.isEnabled()
    assert panel.prompt.toPlainText() == "追加指示"


@pytest.mark.parametrize("action", ["natural", "stop", "clear"])
def test_goal_final_report_wait_remains_interruptible_and_finishes_plan(
    qtbot: QtBot, action: str
) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.activate(3)
    panel.handle_event(ServiceEvent(3, SessionStatus("ready", "m", "low", True)))
    panel.handle_result(ServiceResult(3, "connect"))
    panel.handle_event(ServiceEvent(3, GoalEvent(GoalState("橋まで", "active"))))
    panel.handle_event(ServiceEvent(3, TurnStarted("auto")))
    panel.handle_event(ServiceEvent(3, SessionStatus("running", "m", "low", True)))
    panel.handle_event(ServiceEvent(3, PlanEvent("auto", (PlanStep("橋まで移動", "inProgress"),))))
    panel.prompt.setPlainText("次の原稿")
    panel.handle_event(ServiceEvent(3, GoalEvent(GoalState("橋まで", "complete"))))
    panel.handle_event(ServiceEvent(3, SessionStatus("stopping", "m", "low", True)))
    assert not panel.send_button.isEnabled()
    assert not panel.plan_panel.resume_button.isEnabled()
    assert panel.interrupt_button.isEnabled()
    assert panel.plan_panel.clear_button.isEnabled()
    requested: list[str] = []
    panel.interrupt_requested.connect(lambda: requested.append("stop"))
    panel.goal_clear_requested.connect(lambda: requested.append("clear"))
    if action == "natural":
        panel.handle_event(
            ServiceEvent(3, PlanEvent("auto", (PlanStep("橋まで移動", "completed"),)))
        )
        panel.handle_event(ServiceEvent(3, TextEvent("final", "橋に到着しました", replace=True)))
        panel.handle_event(ServiceEvent(3, SessionStatus("ready", "m", "low", True)))
        assert panel.plan_panel.status.text() == "完了"
        assert "橋に到着しました" in panel.transcript.toPlainText()
        assert not requested
    else:
        (panel.interrupt_button if action == "stop" else panel.plan_panel.clear_button).click()
        assert requested == [action]
        assert not panel.interrupt_button.isEnabled()
        panel.handle_event(ServiceEvent(3, SessionStatus("stopped", "m", "low", True)))
        panel.handle_result(ServiceResult(3, "stop" if action == "stop" else "goal_clear"))
        assert panel.plan_panel.status.text() == "中断"
    assert panel.prompt.toPlainText() == "次の原稿"
    assert panel.send_button.isEnabled()


def test_goal_controls_disable_on_disconnect_without_discarding_confirmed_text(
    qtbot: QtBot,
) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.activate(8)
    panel.handle_event(ServiceEvent(8, SessionStatus("ready", "m", "low", True)))
    panel.handle_result(ServiceResult(8, "connect"))
    panel.handle_event(ServiceEvent(8, GoalEvent(GoalState("保持する目標", "paused"))))
    assert panel.plan_panel.clear_button.isEnabled()
    panel.handle_result(ServiceResult(8, "disconnect"))
    assert not panel.plan_panel.clear_button.isEnabled()
    assert not panel.plan_panel.resume_button.isEnabled()
    assert panel.plan_panel.goal_value.toPlainText() == "保持する目標"


def test_account_settings_updates_preserve_copy_and_usage_requires_explicit_resume(
    qtbot: QtBot,
) -> None:
    window = MainWindow()
    window.allow_close = True
    qtbot.addWidget(window)
    window.resize(800, 600)
    window.show()
    panel = window.connection
    assert window.codex_menu.actions() == [panel.connect_action, panel.disconnect_action]
    panel.activate(1)
    panel.handle_event(ServiceEvent(1, SessionStatus("ready", "m", "low", True)))
    panel.handle_result(ServiceResult(1, "connect"))
    account = AccountInfo("player@example.test", "plus", "ゲーム", True)
    panel.handle_event(ServiceEvent(1, account))
    panel.handle_event(
        ServiceEvent(
            1, UsageEvent((UsageBucket("codex", primary=RateLimitWindow(25, 300)),), True, True)
        )
    )
    dialog = SettingsDialog(Path("settings.toml"), window)
    dialog.codex_form.insertRow(0, panel.codex_settings)
    panel.codex_settings.show()
    dialog.close_requested.connect(dialog.hide)
    dialog.tabs.setCurrentIndex(dialog.codex_tab)
    dialog.show()
    view = panel.codex_settings.account
    assert account.email is not None
    view.setSelection(view.text().index(account.email), len(account.email))
    panel.handle_event(
        ServiceEvent(
            1, UsageEvent((UsageBucket("codex", primary=RateLimitWindow(30)),), True, True)
        )
    )
    assert view.selectedText() == account.email
    qtbot.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == account.email
    assert "70%" in panel.codex_settings.view.toPlainText()
    panel.handle_event(ServiceEvent(1, GoalEvent(GoalState("保持した目標", "paused"))))
    panel.prompt.setPlainText("次の依頼")
    panel.handle_event(ServiceEvent(1, UsageEvent(ordinary_usage_allowed=False, available=True)))
    assert not panel.send_button.isEnabled()
    assert not panel.plan_panel.resume_button.isEnabled()
    assert panel.plan_panel.clear_button.isEnabled()
    assert panel.disconnect_action.isEnabled()
    requests: list[bool] = []
    panel.goal_resume_requested.connect(lambda: requests.append(True))
    panel.handle_event(ServiceEvent(1, UsageEvent(ordinary_usage_allowed=False)))
    assert not panel.plan_panel.resume_button.isEnabled()
    panel.handle_event(ServiceEvent(1, UsageEvent(ordinary_usage_allowed=True, available=True)))
    assert panel.send_button.isEnabled()
    assert panel.plan_panel.resume_button.isEnabled()
    assert requests == []
    panel.handle_event(ServiceEvent(1, UsageEvent()))
    assert panel.send_button.isEnabled()
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    panel.handle_event(ServiceEvent(1, SandboxEvent("notConfigured", reconnect_required=True)))
    dialog.show()
    assert panel.codex_settings.isVisible()
    assert not panel.sandbox_action.isEnabled()
    assert panel.disconnect_action.isEnabled()
    panel.activate(2)
    panel.handle_event(ServiceEvent(1, account))
    assert account.email not in panel.codex_settings.account.text()
    assert panel.prompt.toPlainText() == "次の依頼"


def test_explicit_sandbox_setup_remains_disconnectable_and_reports_unknown_acceptance(
    qtbot: QtBot,
) -> None:
    window = MainWindow()
    window.allow_close = True
    qtbot.addWidget(window)
    panel = window.connection
    assert panel.sandbox_action not in window.codex_menu.actions()
    panel.activate(1)
    panel.handle_event(ServiceEvent(1, SessionStatus("login_required")))
    panel.handle_result(ServiceResult(1, "connect"))
    panel.handle_event(ServiceEvent(1, SandboxEvent("notConfigured")))
    assert panel.login_action.isEnabled()
    requested: list[bool] = []
    failures: list[Notice] = []
    panel.sandbox_requested.connect(lambda: requested.append(True))
    panel.failed.connect(failures.append)
    panel.sandbox_action.trigger()
    panel.sandbox_action.trigger()
    assert requested == [True]
    assert not panel.sandbox_action.isEnabled()
    assert panel.disconnect_action.isEnabled()
    unknown = SandboxEvent(None, True, "setup-acceptance-unknown-fixture")
    panel.handle_event(ServiceEvent(1, unknown))
    panel.handle_event(ServiceEvent(1, unknown))
    assert len(failures) == 1
    assert failures[0].details == unknown.detail
    panel.handle_event(ServiceEvent(1, SandboxEvent("ready")))
    panel.handle_result(ServiceResult(1, "setup_windows_sandbox"))
    assert not panel.sandbox_action.isEnabled()
    panel.handle_event(ServiceEvent(1, SandboxEvent("updateRequired")))
    assert panel.sandbox_action.isEnabled()
    panel.handle_event(ServiceEvent(1, SessionStatus("running", "m", "low", True)))
    assert not panel.sandbox_action.isEnabled()
    panel.handle_event(ServiceEvent(1, SessionStatus("ready", "m", "low", True)))
    panel.handle_event(ServiceEvent(1, GoalEvent(GoalState("自律継続中", "active"))))
    assert not panel.sandbox_action.isEnabled()
    panel.handle_event(ServiceEvent(1, GoalEvent(GoalState("保持中", "paused"))))
    panel.sandbox_action.trigger()
    failed = SandboxEvent(None, False, "setup-failed-fixture")
    panel.handle_event(ServiceEvent(1, failed))
    assert len(failures) == 1
    panel.handle_result(ServiceResult(1, "setup_windows_sandbox", failed.detail))
    assert len(failures) == 2
    assert failures[-1].details == failed.detail
    assert panel.sandbox_action.isEnabled()
    assert panel.disconnect_action.isEnabled()
