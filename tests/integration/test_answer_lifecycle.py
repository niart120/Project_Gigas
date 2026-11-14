import pytest
from pytestqt.qtbot import QtBot

from gigas.runtime.codex.models import (
    ConversationStatus,
    HumanRequest,
    RequestResolved,
    SandboxEvent,
    SessionStatus,
)
from gigas.services.codex import AnswerResult, ServiceEvent, ServiceResult
from gigas.ui.connection_panel import ConnectionPanel


@pytest.fixture
def panel(qtbot: QtBot) -> ConnectionPanel:
    """Present two independent approval requests in the same live connection."""
    widget = ConnectionPanel()
    qtbot.addWidget(widget)
    widget.show()
    widget.activate(1)
    widget.handle_event(ServiceEvent(1, SessionStatus("ready", "m", "low", True)))
    widget.handle_event(ServiceEvent(1, SandboxEvent("ready")))
    widget.handle_event(ServiceEvent(1, ConversationStatus("thread", "turn")))
    for token in ("first", "second"):
        widget.handle_event(
            ServiceEvent(
                1,
                HumanRequest(token, token, "thread", "turn", "command", decisions=("accept",)),
            )
        )
    return widget


def test_pending_request_and_late_result_survive_next_turn(panel: ConnectionPanel) -> None:
    card = panel.requests["first"]
    card.decision_buttons["accept"].click()
    panel.handle_event(ServiceEvent(1, RequestResolved("first")))
    panel.handle_event(ServiceEvent(1, ConversationStatus("thread", "next")))
    assert card.state == "sending"
    assert panel.requests["first"] is card
    assert panel.requests["second"].is_live
    assert not card.decision_buttons["accept"].isEnabled()
    panel.handle_result(ServiceResult(1, "answer", answer=AnswerResult("first", "sent")))
    assert card.state == "sent"
    assert panel.requests["first"] is card
    panel.handle_event(ServiceEvent(1, ConversationStatus("thread", "later")))
    assert set(panel.requests) == {"first", "second"}
    assert panel.requests["second"].is_live


def test_service_exit_marks_only_unconfirmed_submissions_unknown(panel: ConnectionPanel) -> None:
    card = panel.requests["first"]
    card.decision_buttons["accept"].click()
    panel.service_finished()
    assert card.label == "送信結果不明"
    assert panel.requests["second"].label == "失効"
    assert not card.decision_buttons["accept"].isEnabled()


def test_results_remain_until_next_turn_is_confirmed(panel: ConnectionPanel) -> None:
    card = panel.requests["first"]
    card.decision_buttons["accept"].click()
    panel.handle_result(ServiceResult(1, "answer", answer=AnswerResult("first", "sent")))
    panel.handle_event(ServiceEvent(1, RequestResolved("second")))
    panel.handle_event(ServiceEvent(1, ConversationStatus("thread")))
    assert set(panel.requests) == {"first", "second"}
    panel.prompt.setPlainText("次の依頼")
    panel.send_button.click()
    panel.handle_event(ServiceEvent(1, ConversationStatus("thread", sending=True)))
    assert set(panel.requests) == {"first", "second"}
    panel.handle_result(ServiceResult(1, "send", "開始失敗"))
    assert set(panel.requests) == {"first", "second"}
    panel.handle_event(ServiceEvent(0, ConversationStatus("thread", "old-generation")))
    assert set(panel.requests) == {"first", "second"}
    panel.handle_event(ServiceEvent(1, ConversationStatus("thread")))
    panel.send_button.click()
    panel.handle_event(ServiceEvent(1, ConversationStatus("thread", "next")))
    assert set(panel.requests) == {"first", "second"}
    assert all(
        record.controls is not None and not record.controls.isVisible()
        for record in panel.requests.values()
    )
    assert "許可" in panel.transcript.toPlainText()
    panel.handle_event(ServiceEvent(1, ConversationStatus("thread", "next")))
    panel.handle_event(
        ServiceEvent(
            1,
            HumanRequest("current", "current", "thread", "next", "command", decisions=("decline",)),
        )
    )
    current = panel.requests["current"]
    current.decision_buttons["decline"].click()
    panel.handle_result(ServiceResult(1, "answer", answer=AnswerResult("current", "sent")))
    panel.handle_event(ServiceEvent(1, ConversationStatus("thread", "next")))
    panel.handle_event(ServiceEvent(1, RequestResolved("first")))
    panel.handle_result(ServiceResult(1, "answer", answer=AnswerResult("first", "unknown")))
    assert set(panel.requests) == {"first", "second", "current"}
    assert current.state == "sent"


def test_results_match_tokens_and_survive_later_resolution(panel: ConnectionPanel) -> None:
    first, second = panel.requests["first"], panel.requests["second"]
    first.decision_buttons["accept"].click()
    second.decision_buttons["accept"].click()
    panel.handle_event(ServiceEvent(1, RequestResolved("first")))
    panel.handle_result(ServiceResult(1, "answer", "不明", AnswerResult("second", "unknown")))
    assert first.state == "sending"
    assert second.state == "unknown"
    panel.handle_result(ServiceResult(1, "answer", answer=AnswerResult("first", "sent")))
    panel.handle_event(ServiceEvent(1, RequestResolved("first")))
    panel.handle_event(ServiceEvent(1, SessionStatus("closed")))
    panel.service_finished()
    assert first.state == "sent"
    assert first.label == "送信済み"
    assert second.state == "unknown"
    assert not first.decision_buttons["accept"].isEnabled()


def test_results_cannot_cross_generations_or_revive_expired_requests(
    panel: ConnectionPanel,
) -> None:
    first, second = panel.requests["first"], panel.requests["second"]
    first.decision_buttons["accept"].click()
    panel.handle_result(ServiceResult(0, "answer", answer=AnswerResult("first", "sent")))
    assert first.state == "sending"
    panel.handle_event(ServiceEvent(1, RequestResolved("second")))
    panel.handle_result(ServiceResult(1, "answer", answer=AnswerResult("second", "sent")))
    assert second.state == "expired"
    panel.handle_result(ServiceResult(1, "answer", "未送信", AnswerResult("first", "not_sent")))
    assert first.state == "not_sent"
    assert not first.is_live
    panel.handle_result(ServiceResult(1, "answer", answer=AnswerResult("first", "sent")))
    assert first.state == "not_sent"


def test_result_queued_before_service_exit_refines_provisional_unknown(
    panel: ConnectionPanel,
) -> None:
    card = panel.requests["first"]
    card.decision_buttons["accept"].click()
    panel.service_finished()
    assert card.state == "unknown"
    panel.handle_event(ServiceEvent(1, ConversationStatus("thread", "next")))
    assert panel.requests["first"] is card
    panel.handle_result(ServiceResult(1, "answer", answer=AnswerResult("first", "sent")))
    assert card.state == "sent"
    assert not card.decision_buttons["accept"].isEnabled()


def test_stop_during_submission_waits_for_the_delivery_result(panel: ConnectionPanel) -> None:
    card = panel.requests["first"]
    card.decision_buttons["accept"].click()
    panel.interrupt()
    assert card.state == "sending"
    assert not card.decision_buttons["accept"].isEnabled()
    panel.handle_result(ServiceResult(1, "answer", "不明", AnswerResult("first", "unknown")))
    panel.handle_event(ServiceEvent(1, RequestResolved("first")))
    assert card.state == "unknown"
    assert not card.is_live
