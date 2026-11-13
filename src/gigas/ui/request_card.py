"""Request drafts and delivery evidence, separate from the shared composer."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from PySide6.QtWidgets import QPushButton, QWidget

    from gigas.runtime.codex.models import HumanRequest
    from gigas.services.codex import AnswerOutcome

type RequestState = Literal["waiting", "sending", "sent", "expired", "not_sent", "unknown"]

_STATE_LABELS: dict[RequestState, str] = {
    "waiting": "回答待ち",
    "sending": "送信中",
    "sent": "送信済み",
    "expired": "失効",
    "not_sent": "未送信",
    "unknown": "送信結果不明",
}
DECISION_LABELS = {"accept": "許可", "decline": "拒否", "cancel": "取消し"}


@dataclass(frozen=True, slots=True)
class AnswerSubmission:
    """A user-selected answer to one local request token.

    Attributes:
        token: Opaque live token from HumanRequest.
        decision: Advertised approval decision, or None for questions.
        answers: Answers indexed by question ID, or None for approvals.
    """

    token: str
    decision: str | None = None
    answers: "Mapping[str, str] | None" = field(default=None, repr=False)


@dataclass(slots=True)
class RequestRecord:
    """Own one request's drafts and its first confirmed delivery result.

    Args:
        request: Runtime request with a connection-unique token.
        number: Display number shared by history, targets and approval controls.
    """

    request: "HumanRequest"
    number: int
    state: RequestState = "waiting"
    drafts: dict[str, str] = field(default_factory=dict, repr=False)
    answer_text: str = ""
    controls: "QWidget | None" = field(default=None, repr=False)
    decision_buttons: "dict[str, QPushButton]" = field(default_factory=dict, repr=False)
    _result_received: bool = False

    @property
    def is_live(self) -> bool:
        """Whether this request still accepts one explicit submission."""
        return self.state == "waiting"

    @property
    def label(self) -> str:
        """Return the display label for current delivery evidence."""
        return _STATE_LABELS[self.state]

    @property
    def title(self) -> str:
        """Return a short identity shared by history and controls."""
        kind = {"question": "質問", "command": "確認", "file": "確認"}
        return f"{kind[self.request.kind]} {self.number}"

    @property
    def complete(self) -> bool:
        """Whether every question has an explicit permitted answer."""
        return bool(self.request.questions) and all(
            bool(self.drafts.get(question.id, "").strip())
            and (
                not question.options
                or question.is_other
                or self.drafts.get(question.id, "") in {option.label for option in question.options}
            )
            for question in self.request.questions
        )

    def submit(self, decision: str | None = None) -> AnswerSubmission | None:
        """Snapshot one valid submission without discarding its pending draft.

        Args:
            decision: Explicit advertised decision, or None to answer all questions.

        Returns:
            The one response to send, or None when the request cannot be submitted.
        """
        if not self.is_live:
            return None
        if self.request.kind == "question":
            if decision is not None or not self.complete:
                return None
            answers = {question.id: self.drafts[question.id] for question in self.request.questions}
            self.answer_text = "\n".join(
                f"{question.header}: {answers[question.id]}" for question in self.request.questions
            )
            submission = AnswerSubmission(self.request.token, answers=answers)
        else:
            if decision not in self.request.decisions or decision is None:
                return None
            self.answer_text = DECISION_LABELS[decision]
            submission = AnswerSubmission(self.request.token, decision=decision)
        self.state = "sending"
        return submission

    def invalidate(self) -> None:
        """Expire unanswered requests without changing submitted delivery evidence."""
        if self.is_live:
            self.state = "expired"

    def mark_unconfirmed(self) -> None:
        """Mark an outstanding submission unknown until its queued result arrives."""
        if self.state == "sending":
            self.state = "unknown"

    def finish_submission(self, outcome: "AnswerOutcome") -> None:
        """Apply the first token-matched result, including after provisional unknown.

        Args:
            outcome: Transport delivery evidence, not execution success.
        """
        if not self._result_received and self.state in {"sending", "unknown"}:
            self._result_received = True
            self.state = outcome
            if outcome == "sent":
                self.drafts.clear()
