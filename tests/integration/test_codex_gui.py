from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QFont, QInputMethodEvent, QTextCursor
from PySide6.QtWidgets import QApplication, QLineEdit, QTextBrowser
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.runtime.codex.connection import CodexConnection
from gigas.runtime.codex.models import (
    ActivityEvent,
    ConnectionOptions,
    HumanRequest,
    LoginEvent,
    PlanEvent,
    PlanStep,
    Question,
    QuestionOption,
    ReasoningEvent,
    RequestResolved,
    SessionStatus,
    TextEvent,
    TurnStarted,
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
from gigas.ui.request_card import AnswerSubmission
from gigas.ui.transcript import Transcript
from tests.integration.test_codex_transport import PEER_COMMAND

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from gigas.runtime.codex.models import ConnectionEvent, ThreadConfiguration
    from gigas.ui.notifications import Notice


def _select_question(panel: ConnectionPanel, token: str, question_id: str) -> None:
    index = next(
        i for i in range(panel.target.count()) if panel.target.itemData(i) == (token, question_id)
    )
    panel.target.setCurrentIndex(index)


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
        assert "経路確認" in panel.plan.toPlainText()
        assert "公開要約" in panel.transcript.toPlainText()
        assert "fixture/observe" in panel.transcript.toPlainText()
        assert "観測完了" in panel.transcript.toPlainText()
        assert "INTERNAL_CONTENT" not in panel.transcript.toPlainText()
        qtbot.waitUntil(lambda: any(result.operation == "send" for result in results))
        panel.target.setCurrentIndex(0)
        panel.prompt.setPlainText("質問は保留し、門の前で止まる")
        panel.send_button.click()
        qtbot.waitUntil(lambda: any(result.operation == "steer" for result in results))
        steering = next(result for result in results if result.operation == "steer")
        assert steering.message == MessageResult("accepted", "dialogue-turn")
        assert question.is_live
        _select_question(panel, token, "route")
        panel.choice.setCurrentIndex(1)
        _select_question(panel, token, "note")
        panel.prompt.setPlainText("補給後に現在地を確認する")
        _select_question(panel, token, "secret")
        masked_answer = "D07-SYNTHETIC-SECRET"
        panel.secret.setText(masked_answer)
        panel.send_button.click()
        assert panel.secret.text() == ""
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
        assert "✓ 経路確認" in panel.plan.toPlainText()
        assert panel.target.count() == 1
        assert not panel.approvals.isVisible()
        secret_absent = masked_answer not in panel.transcript.toPlainText()
        assert secret_absent
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
    assert panel.plan.isHidden()
    assert panel.plan_heading.isHidden()
    panel.handle_event(ServiceEvent(1, TurnStarted("old")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running")))
    panel.handle_event(
        ServiceEvent(1, PlanEvent("old", (PlanStep("調査", "inProgress"),), "入口を確認"))
    )
    assert "調査" in panel.plan.toPlainText()
    assert panel.plan_heading.isVisible()
    assert "入口を確認" in panel.transcript.toPlainText()
    panel.handle_event(ServiceEvent(1, PlanEvent("old", ())))
    assert panel.plan.isHidden()
    assert panel.plan_heading.isHidden()
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
    panel.handle_event(ServiceEvent(1, SessionStatus("ready")))
    assert "調査" in panel.plan.toPlainText()
    panel.handle_event(ServiceEvent(1, TurnStarted("new")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running")))
    assert panel.plan.isHidden()
    panel.handle_event(ServiceEvent(1, PlanEvent("new", (PlanStep("移動", "pending"),))))
    panel.handle_event(ServiceEvent(1, ReasoningEvent("new", "shared", ("新しい要約",))))
    panel.handle_event(ServiceEvent(1, PlanEvent("old", (PlanStep("旧計画の訂正", "completed"),))))
    assert "移動" in panel.plan.toPlainText()
    assert "旧計画の訂正" not in panel.plan.toPlainText()
    assert "旧計画の訂正" in panel.transcript.toPlainText()
    assert "新しい要約" in panel.transcript.toPlainText()
    assert "公開要約" in panel.transcript.toPlainText()
    panel.handle_event(ServiceEvent(1, ReasoningEvent("old", "shared", ())))
    assert "公開要約" not in panel.transcript.toPlainText()
    assert "新しい要約" in panel.transcript.toPlainText()
    panel.interrupt()
    panel.handle_event(ServiceEvent(1, PlanEvent("new", (PlanStep("停止後の通知", "completed"),))))
    assert "移動" in panel.plan.toPlainText()
    assert "停止後の通知" in panel.transcript.toPlainText()
    panel.activate(2)
    panel.handle_event(ServiceEvent(1, PlanEvent("new", (PlanStep("旧接続", "pending"),))))
    assert panel.transcript.toPlainText() == ""
    assert panel.plan.isHidden()
    assert panel.plan_heading.isHidden()


def test_current_plan_updates_keep_selection_formatting_and_viewport(qtbot: QtBot) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.resize(500, 600)
    panel.show()
    panel.activate(1)
    panel.handle_event(ServiceEvent(1, TurnStarted("turn")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running")))
    steps = (
        PlanStep("入口を観測", "completed"),
        PlanStep("経路を確認", "inProgress"),
        *(PlanStep(f"計画 {index}", "pending") for index in range(40)),
    )
    panel.handle_event(ServiceEvent(1, PlanEvent("turn", steps)))
    plan = panel.plan
    assert "✓ 入口を観測" in plan.toPlainText()
    assert "▶ 経路を確認" in plan.toPlainText()
    assert "• 計画 20" in plan.toPlainText()
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
    panel.handle_event(ServiceEvent(1, SessionStatus("ready")))
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
    panel.activate(1)
    panel.handle_result(ServiceResult(1, "connect"))
    panel.handle_event(ServiceEvent(1, TurnStarted("turn")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running")))
    panel.prompt.setPlainText("補給後に現在地を確認する")
    assert panel.send_button.isEnabled()
    instructions: list[tuple[str, str]] = []
    panel.steer_requested.connect(lambda text, turn: instructions.append((text, turn)))
    panel.send_button.click()
    panel.send_button.click()
    assert instructions == [("補給後に現在地を確認する", "turn")]
    if edited:
        panel.prompt.setPlainText("次の原稿")
    panel.handle_event(ServiceEvent(1, SessionStatus("ready")))
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
    panel.handle_event(ServiceEvent(1, SessionStatus("running")))
    instructions: list[tuple[str, str]] = []
    panel.steer_requested.connect(lambda text, turn: instructions.append((text, turn)))
    panel.prompt.setPlainText("現在地を確認")
    panel.send_button.click()
    panel.interrupt()
    panel.handle_event(ServiceEvent(1, TurnStarted("old")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running")))
    panel.handle_result(ServiceResult(1, "steer", message=MessageResult("unknown")))
    assert panel.prompt.toPlainText() == "現在地を確認"
    assert not panel.send_button.isEnabled()
    panel.handle_result(ServiceResult(1, "stop"))
    panel.handle_event(ServiceEvent(1, SessionStatus("stopped")))
    panel.send_button.click()
    panel.handle_event(ServiceEvent(1, TurnStarted("new")))
    panel.handle_result(ServiceResult(1, "send", message=MessageResult("accepted", "new")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running")))
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
        shell.window.connection.connect_action.trigger()
        assert len(snapshots) == 1
        assert snapshots[0] == ConnectionOptions(tmp_path / "runtime")
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
    panel.handle_event(ServiceEvent(5, SessionStatus("running")))
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
            Question("text", "入力", "秘密の値は?", is_secret=True),
            Question(
                "other", "経路", "別の経路?", (QuestionOption("既定", "説明"),), is_other=True
            ),
        ),
    )
    panel.handle_event(ServiceEvent(5, request))
    record = panel.requests["token"]
    assert panel.target.currentData() == ("token", "choice")
    assert panel.choice.currentIndex() == -1
    assert not panel.send_button.isEnabled()
    panel.choice.setCurrentIndex(1)
    _select_question(panel, "token", "text")
    assert panel.secret.echoMode() == QLineEdit.EchoMode.Password
    panel.secret.setText("secret")
    _select_question(panel, "token", "other")
    panel.prompt.setPlainText("custom; stop before the gate")
    assert panel.prompt.isVisible()
    panel.send_button.click()
    panel.send_button.click()
    assert len(answers) == 1
    assert answers[0] == AnswerSubmission(
        "token", answers={"choice": "B", "text": "secret", "other": "custom; stop before the gate"}
    )
    panel.handle_event(ServiceEvent(5, RequestResolved("token")))
    assert not record.is_live
    assert panel.secret.text() == ""
    assert record.drafts.get("text", "") == ""
    assert "secret" not in panel.transcript.toPlainText()
    assert "説明B" in panel.transcript.toPlainText()
    assert "custom; stop before the gate" in panel.transcript.toPlainText()
    panel.handle_result(ServiceResult(5, "answer", answer=AnswerResult("token", "sent")))
    assert panel.target.count() == 1
    assert not panel.target.isVisible()
    assert "送信済み" in panel.transcript.toPlainText()
    panel.activate(6)
    panel.handle_event(ServiceEvent(5, request))
    assert not panel.requests


@pytest.mark.parametrize("outcome", ["sent", "not_sent", "unknown"])
def test_question_targets_isolate_drafts_and_late_message_acceptance(
    qtbot: QtBot, outcome: str
) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.activate(1)
    panel.handle_result(ServiceResult(1, "connect"))
    panel.handle_event(ServiceEvent(1, TurnStarted("turn")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running")))
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
    assert panel.target.currentData() is None
    _select_question(panel, "first", "route")
    assert panel.prompt.toPlainText() == ""
    panel.prompt.setPlainText("same text")
    panel.handle_result(ServiceResult(1, "steer", message=MessageResult("accepted", "turn")))
    assert panel.prompt.toPlainText() == "same text"
    _select_question(panel, "second", "route")
    assert panel.prompt.toPlainText() == ""
    panel.prompt.setPlainText("second draft")
    _select_question(panel, "first", "route")
    sent: list[object] = []
    panel.answer_requested.connect(sent.append)
    panel.send_button.click()
    assert sent == [AnswerSubmission("first", answers={"route": "same text"})]
    _select_question(panel, "second", "route")
    result = {
        "sent": AnswerResult("first", "sent"),
        "not_sent": AnswerResult("first", "not_sent"),
        "unknown": AnswerResult("first", "unknown"),
    }[outcome]
    panel.handle_result(ServiceResult(1, "answer", answer=result))
    assert panel.prompt.toPlainText() == "second draft"
    if outcome != "sent":
        _select_question(panel, "first", "route")
        assert panel.prompt.toPlainText() == "same text"
        assert panel.prompt.isReadOnly()
        assert not panel.send_button.isEnabled()
        _select_question(panel, "second", "route")
    panel.handle_event(ServiceEvent(1, RequestResolved("second")))
    assert panel.prompt.toPlainText() == "second draft"
    assert panel.prompt.isReadOnly()
    assert not panel.send_button.isEnabled()
    panel.target.setCurrentIndex(0)
    assert panel.prompt.toPlainText() == ""


@pytest.mark.parametrize("ending", ["resolved", "stop", "disconnect", "terminal"])
def test_secret_drafts_are_removed_without_transferring_to_messages(
    qtbot: QtBot, ending: str
) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.activate(1)
    panel.handle_event(ServiceEvent(1, TurnStarted("turn")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running")))
    panel.handle_event(
        ServiceEvent(
            1,
            HumanRequest(
                "secret",
                1,
                "thread",
                "turn",
                "question",
                questions=(Question("value", "秘密", "秘密の値", is_secret=True),),
            ),
        )
    )
    panel.secret.setText("PRIVATE-DRAFT")
    panel.target.setCurrentIndex(0)
    assert panel.prompt.toPlainText() == ""
    panel.prompt.setPlainText("ordinary draft")
    if ending == "resolved":
        panel.handle_event(ServiceEvent(1, RequestResolved("secret")))
    elif ending == "stop":
        panel.interrupt()
    elif ending == "disconnect":
        panel.disconnect_action.trigger()
    else:
        panel.handle_event(ServiceEvent(1, SessionStatus("stopped")))
    assert panel.requests["secret"].drafts.get("value", "") == ""
    assert panel.secret.text() == ""
    assert panel.prompt.toPlainText() == "ordinary draft"
    assert "PRIVATE-DRAFT" not in panel.transcript.toPlainText()


def test_composition_keeps_message_target_and_secret_ctrl_enter_waits(qtbot: QtBot) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.activate(1)
    panel.handle_event(ServiceEvent(1, TurnStarted("turn")))
    panel.handle_event(ServiceEvent(1, SessionStatus("running")))
    QApplication.sendEvent(panel.prompt, QInputMethodEvent("へんかん", []))
    panel.handle_event(
        ServiceEvent(
            1,
            HumanRequest(
                "secret",
                1,
                "thread",
                "turn",
                "question",
                questions=(Question("value", "秘密", "秘密の値", is_secret=True),),
            ),
        )
    )
    assert panel.target.currentData() is None
    assert panel.prompt.is_composing
    layout = panel.prompt.document().firstBlock().layout()
    assert layout is not None
    assert layout.preeditAreaText() == "へんかん"
    QApplication.sendEvent(panel.prompt, QInputMethodEvent("", []))
    _select_question(panel, "secret", "value")
    panel.secret.setText("private-value")
    sent: list[object] = []
    panel.answer_requested.connect(sent.append)
    QApplication.sendEvent(panel.secret, QInputMethodEvent("へんかん", []))
    qtbot.keyClick(panel.secret, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert not sent
    QApplication.sendEvent(panel.secret, QInputMethodEvent("", []))
    qtbot.keyClick(panel.secret, Qt.Key.Key_Return)
    assert not sent
    qtbot.keyClick(panel.secret, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert sent == [AnswerSubmission("secret", answers={"value": "private-value"})]
    assert panel.secret.text() == ""
    assert "private-value" not in panel.transcript.toPlainText()
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
    _select_question(panel, "free", "value")
    QApplication.sendEvent(panel.prompt, QInputMethodEvent("未確定の回答", []))
    panel.handle_event(ServiceEvent(1, RequestResolved("free")))
    panel.target.setCurrentIndex(0)
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
    panel.handle_event(ServiceEvent(1, SessionStatus("running")))
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
    assert set(card.decision_buttons) == {"accept", "decline", "cancel"}
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
    panel.handle_event(ServiceEvent(1, SessionStatus("running")))
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
