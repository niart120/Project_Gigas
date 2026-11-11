import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QStyleFactory, QWidget
from pytestqt.qtbot import QtBot

from gigas.runtime.codex.models import (
    HumanRequest,
    Question,
    QuestionOption,
    RequestResolved,
    SessionStatus,
)
from gigas.services.codex import AnswerResult, ServiceEvent, ServiceResult
from gigas.ui.connection_panel import ConnectionPanel


@pytest.fixture
def panel(qtbot: QtBot) -> ConnectionPanel:
    """Use the narrow conversation area of a small main window."""
    widget = ConnectionPanel()
    qtbot.addWidget(widget)
    widget.resize(360, 360)
    widget.show()
    widget.activate(1)
    widget.handle_event(ServiceEvent(1, SessionStatus("running")))
    return widget


def _question(token: str, *, long: bool = False) -> HumanRequest:
    return HumanRequest(
        token,
        token,
        "thread",
        "turn",
        "question",
        questions=(
            Question(
                "choice",
                "質問",
                "確認する内容\n" * (30 if long else 1),
                (QuestionOption("選択肢A", ""), QuestionOption("選択肢B", "")),
            ),
            Question("draft", "回答", "理由"),
        ),
    )


def test_active_request_gets_space_without_losing_prompt_draft(
    panel: ConnectionPanel, qtbot: QtBot
) -> None:
    prompt = panel.prompt
    prompt.setPlainText("保存している入力下書き")
    cursor = prompt.textCursor()
    cursor.setPosition(2)
    cursor.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
    prompt.setTextCursor(cursor)
    panel.handle_event(ServiceEvent(1, _question("question")))
    qtbot.waitUntil(panel.requests.isVisible)
    assert prompt.isHidden()
    qtbot.waitUntil(lambda: panel.requests.viewport().height() > panel.transcript.height() * 2)
    assert panel.requests.viewport().height() >= 150
    assert not any(
        choice.isChecked() for choice in panel.cards["question"].editors["choice"].choices
    )
    panel.handle_event(ServiceEvent(1, RequestResolved("question")))
    panel.handle_event(ServiceEvent(1, SessionStatus("ready")))
    assert panel.prompt is prompt
    assert not prompt.isHidden()
    assert prompt.toPlainText() == "保存している入力下書き"
    assert prompt.textCursor().position() == 5
    assert prompt.textCursor().anchor() == 2


def test_completed_card_collapses_and_reopens_without_reenabling_answers(
    panel: ConnectionPanel, qtbot: QtBot
) -> None:
    panel.handle_event(ServiceEvent(1, _question("question", long=True)))
    card = panel.cards["question"]
    card.editors["choice"].choices[1].click()
    card.editors["draft"].answer.setText("回答下書き")
    card.submit_button.click()
    panel.handle_result(ServiceResult(1, "answer", answer=AnswerResult("question", "sent")))
    qtbot.waitUntil(lambda: card.height() < 100)
    assert not card.body.isVisible()
    card.expand_button.click()
    assert card.body.isVisible()
    assert card.editors["draft"].answer.text() == "回答下書き"
    assert card.editors["choice"].choices[1].isChecked()
    assert not card.submit_button.isEnabled()


def test_new_request_starts_at_its_header_and_decisions_are_reachable(
    panel: ConnectionPanel, qtbot: QtBot
) -> None:
    panel.handle_event(ServiceEvent(1, _question("old", long=True)))
    panel.handle_event(ServiceEvent(1, RequestResolved("old")))
    panel.handle_event(
        ServiceEvent(
            1,
            HumanRequest(
                "new",
                "new",
                "thread",
                "turn",
                "file",
                details="path/to/file.py\n" + "+ long changed line\n" * 80,
                decisions=("accept", "decline", "cancel"),
            ),
        )
    )
    card = panel.cards["new"]
    viewport = panel.requests.viewport()
    qtbot.waitUntil(lambda: 0 <= card.mapTo(viewport, QPoint()).y() < 30)
    panel.requests.verticalScrollBar().setValue(panel.requests.verticalScrollBar().maximum())
    button = card.decision_buttons["decline"]
    top = button.mapTo(viewport, QPoint()).y()
    assert 0 <= top < viewport.height()
    assert top + button.height() <= viewport.height()
    assert button.isEnabled()


def test_long_question_options_fit_width_and_keep_exact_keyboard_choice(
    panel: ConnectionPanel, qtbot: QtBot
) -> None:
    label = "選択肢の長い内容 " * 20 + "long-unbroken-value-" * 20
    request = HumanRequest(
        "long",
        "long",
        "thread",
        "turn",
        "question",
        questions=(
            Question(
                "choice",
                "長い見出し" * 20,
                "長い質問 " * 30,
                (QuestionOption(label, ""), QuestionOption("別案", "")),
            ),
        ),
    )
    panel.handle_event(ServiceEvent(1, request))
    qtbot.waitUntil(panel.requests.isVisible)
    assert panel.width() == 360
    assert panel.requests.horizontalScrollBar().maximum() == 0
    editor = panel.cards["long"].editors["choice"]
    assert not any(button.isChecked() for button in editor.choices)
    choice = editor.choices[0]
    qtbot.waitUntil(lambda: choice.height() > choice.fontMetrics().lineSpacing() * 2)
    choice.setFocus()
    qtbot.keyClick(choice, Qt.Key.Key_Space)
    assert editor.value() == label
    assert panel.cards["long"].submit_button.isEnabled()


def test_pending_answer_cannot_send_a_hidden_prompt_when_turn_finishes(
    panel: ConnectionPanel,
) -> None:
    panel.prompt.setPlainText("未送信の下書き")
    panel.handle_event(ServiceEvent(1, _question("question")))
    card = panel.cards["question"]
    card.editors["choice"].choices[0].click()
    card.editors["draft"].answer.setText("理由")
    card.submit_button.click()
    panel.handle_event(ServiceEvent(1, RequestResolved("question")))
    panel.handle_event(ServiceEvent(1, SessionStatus("ready")))
    assert panel.prompt.isHidden()
    assert not panel.send_button.isEnabled()
    panel.handle_result(ServiceResult(1, "answer", answer=AnswerResult("question", "sent")))
    assert not panel.prompt.isHidden()
    assert panel.send_button.isEnabled()


def test_manual_folding_preserves_unsubmitted_answers(panel: ConnectionPanel) -> None:
    panel.handle_event(ServiceEvent(1, _question("question")))
    card = panel.cards["question"]
    card.editors["choice"].choices[1].click()
    card.editors["draft"].answer.setText("まだ送らない")
    card.expand_button.click()
    assert not card.body.isVisible()
    card.expand_button.click()
    assert card.body.isVisible()
    assert card.editors["choice"].choices[1].isChecked()
    assert card.editors["draft"].answer.text() == "まだ送らない"
    assert card.is_live
    assert card.submit_button.isEnabled()


def test_wrapped_option_accepts_click_on_caption(panel: ConnectionPanel, qtbot: QtBot) -> None:
    panel.handle_event(ServiceEvent(1, _question("question")))
    choice = panel.cards["question"].editors["choice"].choices[1]
    qtbot.waitUntil(choice.isVisible)
    assert not choice.isChecked()
    qtbot.mouseClick(
        choice, Qt.MouseButton.LeftButton, pos=QPoint(choice.width() - 2, choice.height() // 2)
    )
    assert choice.isChecked()


@pytest.mark.parametrize("style_name", ["Fusion", "Windows"])
@pytest.mark.parametrize("point_size", [9, 16])
def test_narrow_approval_keeps_header_and_each_decision_within_viewport(
    panel: ConnectionPanel, qtbot: QtBot, style_name: str, point_size: int
) -> None:
    panel.resize(292, 400)
    panel.setFont(QFont("Meiryo UI", point_size))
    panel.handle_event(
        ServiceEvent(
            1,
            HumanRequest(
                "narrow",
                "narrow",
                "thread",
                "turn",
                "file",
                details="path/to/file.py\n" + "+ changed line\n" * 80,
                decisions=("accept", "decline", "cancel"),
            ),
        )
    )
    style = QStyleFactory.create(style_name)
    assert style is not None
    style.setParent(panel)
    panel.setStyle(style)
    for widget in panel.findChildren(QWidget):
        widget.setStyle(style)
    card = panel.cards["narrow"]
    viewport = panel.requests.viewport()
    qtbot.waitUntil(lambda: 0 <= card.mapTo(viewport, QPoint()).y() < 30)
    assert viewport.width() <= 258
    assert panel.requests.horizontalScrollBar().maximum() == 0
    assert viewport.rect().contains(
        QRect(card.expand_button.mapTo(viewport, QPoint()), card.expand_button.size())
    )
    for button in card.decision_buttons.values():
        panel.requests.ensureWidgetVisible(button)
        qtbot.waitUntil(
            lambda current=button: viewport.rect().contains(
                QRect(current.mapTo(viewport, QPoint()), current.size())
            )
        )
        assert button.isEnabled()


def test_narrow_approval_keyboard_order_requires_one_explicit_decision(
    panel: ConnectionPanel, qtbot: QtBot
) -> None:
    panel.resize(292, 400)
    panel.handle_event(
        ServiceEvent(
            1,
            HumanRequest(
                "keys",
                "keys",
                "thread",
                "turn",
                "command",
                details="A command to review",
                decisions=("accept", "decline", "cancel"),
            ),
        )
    )
    card = panel.cards["keys"]
    sent: list[object] = []
    panel.answer_requested.connect(sent.append)
    buttons = card.decision_buttons
    buttons["accept"].setFocus()
    qtbot.keyClick(buttons["accept"], Qt.Key.Key_Tab)
    assert buttons["decline"].hasFocus()
    qtbot.keyClick(buttons["decline"], Qt.Key.Key_Tab)
    assert buttons["cancel"].hasFocus()
    assert not sent
    qtbot.keyClick(buttons["cancel"], Qt.Key.Key_Space)
    qtbot.keyClick(buttons["cancel"], Qt.Key.Key_Space)
    assert len(sent) == 1
    assert card.state == "sending"
    assert all(not button.isEnabled() for button in buttons.values())
