"""Explicit question and approval controls with local validity guards."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gigas.ui.wrapped_radio import WrappedRadioButton

if TYPE_CHECKING:
    from collections.abc import Mapping

    from gigas.runtime.codex.models import HumanRequest, Question
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
    answers: "Mapping[str, str] | None" = None


def _text_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    policy.setHeightForWidth(True)
    label.setSizePolicy(policy)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class QuestionInput(QGroupBox):
    """Render one question without selecting an answer by default.

    Args:
        question: Runtime-provided question, options and input constraints.
        parent: Owning request card.
    """

    changed = Signal()

    def __init__(self, question: "Question", parent: QWidget) -> None:
        """Create choices and an optional masked free-answer field."""
        super().__init__(parent)
        self.setAccessibleName(question.header)
        self.question = question
        layout = QVBoxLayout(self)
        layout.addWidget(_text_label(question.header, self))
        layout.addWidget(_text_label(question.question, self))
        self.choices: list[QRadioButton] = []
        self._group = QButtonGroup(self)
        for option in question.options:
            button = WrappedRadioButton(option.label, self)
            self.choices.append(button)
            self._group.addButton(button)
            layout.addWidget(button)
            if option.description:
                layout.addWidget(_text_label(option.description, self))
            button.toggled.connect(self._changed)
        if question.options and question.is_other:
            other = QRadioButton("その他", self)
            self.choices.append(other)
            self._group.addButton(other)
            layout.addWidget(other)
            other.toggled.connect(self._changed)
        self.answer = QLineEdit(self)
        if question.is_secret:
            self.answer.setEchoMode(QLineEdit.EchoMode.Password)
        self.answer.setVisible(not question.options or question.is_other)
        self.answer.textChanged.connect(self.changed)
        layout.addWidget(self.answer)
        self._changed()

    def value(self) -> str:
        """Return the chosen label or explicit free answer; empty means incomplete."""
        for index, option in enumerate(self.question.options):
            if self.choices[index].isChecked():
                return option.label
        if not self.question.options or (self.question.is_other and self.choices[-1].isChecked()):
            return self.answer.text()
        return ""

    def _changed(self) -> None:
        self.answer.setEnabled(
            not self.question.options or (self.question.is_other and self.choices[-1].isChecked())
        )
        self.changed.emit()


class RequestCard(QGroupBox):
    """Keep a request visible while preventing repeated or expired submissions.

    Approval decisions use a vertical column so native font and style metrics
    determine one button's required width, without adding three minimum widths.

    Args:
        request: Runtime request and its allowed answers.
        parent: Owning panel content widget.
    """

    submitted = Signal(object)
    changed = Signal()

    def __init__(self, request: "HumanRequest", parent: QWidget) -> None:
        """Create only the decision controls advertised by this request."""
        super().__init__(
            {"question": "質問", "command": "コマンド承認", "file": "変更承認"}[request.kind],
            parent,
        )
        self.request = request
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.state: RequestState = "waiting"
        self._result_received = False
        self.editors: dict[str, QuestionInput] = {}
        self.decision_buttons: dict[str, QPushButton] = {}
        layout = QVBoxLayout(self)
        self.status = _text_label("回答待ち", self)
        layout.addWidget(self.status)
        self.body = QWidget(self)
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.body)
        if request.details:
            details = QPlainTextEdit(self.body)
            details.setPlainText(request.details)
            details.setReadOnly(True)
            details.setMaximumHeight(180)
            body_layout.addWidget(details)
        for question in request.questions:
            editor = QuestionInput(question, self.body)
            self.editors[question.id] = editor
            editor.changed.connect(self._validate)
            body_layout.addWidget(editor)
        actions = QHBoxLayout() if request.kind == "question" else QVBoxLayout()
        if request.kind == "question":
            actions.addStretch()
        self.submit_button = QPushButton("回答", self)
        self.submit_button.setVisible(request.kind == "question")
        self.submit_button.clicked.connect(self._answer)
        actions.addWidget(self.submit_button)
        labels = {"accept": "許可", "decline": "拒否", "cancel": "取消し"}
        for decision in request.decisions:
            button = QPushButton(labels.get(decision, decision), self)
            button.clicked.connect(lambda _checked=False, value=decision: self._decide(value))
            self.decision_buttons[decision] = button
            actions.addWidget(button, alignment=Qt.AlignmentFlag.AlignRight)
        body_layout.addLayout(actions)
        self._validate()

    @property
    def is_live(self) -> bool:
        """Whether the card still accepts one explicit submission."""
        return self.state == "waiting"

    @property
    def is_settled(self) -> bool:
        """Whether expiry or a delivery result permits removal at the next turn.

        A provisional unknown after interruption still awaits its queued result.
        """
        return self.state == "expired" or self._result_received

    def invalidate(self) -> None:
        """Expire unanswered requests without changing submitted delivery evidence."""
        if self.is_live:
            self._set_state("expired")

    def mark_unconfirmed(self) -> None:
        """End a pending display when the connection or service stops."""
        if self.state == "sending":
            self._set_state("unknown")

    def finish_submission(self, outcome: "AnswerOutcome") -> None:
        """Apply the first delivery result only to a locally submitted request.

        A result queued before service exit may refine its provisional unknown state.
        Resolution and later connection status never overwrite a confirmed result.

        Args:
            outcome: Token-matched transport delivery evidence, not execution success.
        """
        if not self._result_received and self.state in {"sending", "unknown"}:
            self._result_received = True
            self._set_state(outcome)

    def _set_state(self, state: RequestState) -> None:
        self.state = state
        self.status.setText(_STATE_LABELS[state])
        for editor in self.editors.values():
            editor.setEnabled(False)
            if editor.question.is_secret:
                editor.answer.clear()
        self.submit_button.setEnabled(False)
        for button in self.decision_buttons.values():
            button.setEnabled(False)
        self.body.setVisible(state in {"waiting", "sending"})
        self.changed.emit()

    def _validate(self) -> None:
        self.submit_button.setEnabled(
            self.is_live
            and bool(self.editors)
            and all(editor.value().strip() for editor in self.editors.values())
        )

    def _answer(self) -> None:
        if not self.is_live or not self.submit_button.isEnabled():
            return
        answers = {key: editor.value() for key, editor in self.editors.items()}
        self._set_state("sending")
        self.submitted.emit(AnswerSubmission(self.request.token, answers=answers))

    def _decide(self, decision: str) -> None:
        if not self.is_live or decision not in self.decision_buttons:
            return
        self._set_state("sending")
        self.submitted.emit(AnswerSubmission(self.request.token, decision=decision))
