from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QLineEdit
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.runtime.codex.models import (
    ConnectionOptions,
    HumanRequest,
    LoginEvent,
    Question,
    QuestionOption,
    RequestResolved,
    SessionStatus,
    TextEvent,
    TurnStarted,
)
from gigas.services.codex import CodexServiceThread, MessageResult, ServiceEvent, ServiceResult
from gigas.ui.connection_panel import ConnectionPanel
from gigas.ui.request_card import AnswerSubmission

if TYPE_CHECKING:
    from gigas.ui.notifications import Notice


def test_start_notification_keeps_draft_until_acceptance_result(qtbot: QtBot) -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.activate(1)
    panel.handle_result(ServiceResult(1, "connect"))
    panel.handle_event(ServiceEvent(1, SessionStatus("ready")))
    panel.prompt.setPlainText("受付を確認する原稿")
    panel.send_button.click()
    panel.handle_event(ServiceEvent(1, TurnStarted("turn")))
    assert panel.prompt.toPlainText() == "受付を確認する原稿"
    panel.handle_result(ServiceResult(1, "send", message=MessageResult("accepted", "turn")))
    assert panel.prompt.toPlainText() == ""


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
    card = panel.cards["token"]
    assert not card.submit_button.isEnabled()
    assert card.editors["text"].answer.echoMode() == QLineEdit.EchoMode.Password
    card.editors["choice"].choices[1].click()
    card.editors["text"].answer.setText("secret")
    card.editors["other"].choices[-1].click()
    card.editors["other"].answer.setText("custom")
    card.submit_button.click()
    card.submit_button.click()
    assert len(answers) == 1
    assert answers[0] == AnswerSubmission(
        "token", answers={"choice": "B", "text": "secret", "other": "custom"}
    )
    panel.handle_event(ServiceEvent(5, RequestResolved("token")))
    assert not card.is_live
    assert card.editors["text"].answer.text() == ""
    assert "secret" not in panel.transcript.toPlainText()
    panel.activate(6)
    panel.handle_event(ServiceEvent(5, request))
    assert not panel.cards


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
    card = panel.cards["approval"]
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
    assert not panel.cards["approval"].is_live


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
    card = panel.cards["file"]
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
