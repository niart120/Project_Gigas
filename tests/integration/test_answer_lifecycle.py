import pytest
from pytestqt.qtbot import QtBot

from gigas.runtime.codex.models import HumanRequest, RequestResolved, SessionStatus
from gigas.services.codex import AnswerResult, ServiceEvent, ServiceResult
from gigas.ui.connection_panel import ConnectionPanel


@pytest.fixture
def panel(qtbot: QtBot) -> ConnectionPanel:
    """Present two independent approval requests in the same live connection."""
    widget = ConnectionPanel()
    qtbot.addWidget(widget)
    widget.activate(1)
    widget.handle_event(ServiceEvent(1, SessionStatus("running")))
    for token in ("first", "second"):
        widget.handle_event(
            ServiceEvent(
                1,
                HumanRequest(token, token, "thread", "turn", "command", decisions=("accept",)),
            )
        )
    return widget


def test_request_resolution_does_not_claim_answer_was_sent(panel: ConnectionPanel) -> None:
    card = panel.cards["first"]
    card.decision_buttons["accept"].click()
    assert card.status.text() == "送信中"
    panel.handle_event(ServiceEvent(1, RequestResolved("first")))
    assert card.status.text() == "送信中"
    assert not card.is_live
    assert not card.decision_buttons["accept"].isEnabled()


def test_service_exit_marks_only_unconfirmed_submissions_unknown(panel: ConnectionPanel) -> None:
    card = panel.cards["first"]
    card.decision_buttons["accept"].click()
    panel.service_finished()
    assert card.status.text() == "送信結果不明"
    assert panel.cards["second"].status.text() == "失効"
    assert not card.decision_buttons["accept"].isEnabled()


def test_resolution_without_submission_expires_and_cannot_send(panel: ConnectionPanel) -> None:
    submissions: list[object] = []
    panel.answer_requested.connect(submissions.append)
    panel.handle_event(ServiceEvent(1, RequestResolved("first")))
    card = panel.cards["first"]
    card.decision_buttons["accept"].click()
    assert card.status.text() == "失効"
    assert submissions == []


def test_results_match_tokens_and_survive_later_resolution(panel: ConnectionPanel) -> None:
    first, second = panel.cards["first"], panel.cards["second"]
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
    assert first.status.text() == "送信済み"
    assert second.state == "unknown"
    assert not first.decision_buttons["accept"].isEnabled()


def test_results_cannot_cross_generations_or_revive_expired_requests(
    panel: ConnectionPanel,
) -> None:
    first, second = panel.cards["first"], panel.cards["second"]
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
    card = panel.cards["first"]
    card.decision_buttons["accept"].click()
    panel.service_finished()
    assert card.state == "unknown"
    panel.handle_result(ServiceResult(1, "answer", answer=AnswerResult("first", "sent")))
    assert card.state == "sent"
    assert not card.decision_buttons["accept"].isEnabled()


def test_cancel_during_submission_keeps_unknown_until_result(panel: ConnectionPanel) -> None:
    card = panel.cards["first"]
    card.decision_buttons["accept"].click()
    panel.interrupt()
    assert card.state == "unknown"
    panel.handle_result(ServiceResult(1, "answer", "不明", AnswerResult("first", "unknown")))
    panel.handle_event(ServiceEvent(1, RequestResolved("first")))
    assert card.state == "unknown"
    assert not card.is_live
