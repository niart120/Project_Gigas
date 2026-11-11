"""Explicit question and approval controls with local validity guards."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

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
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from gigas.runtime.codex.models import HumanRequest, Question


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
        super().__init__(question.header, parent)
        self.question = question
        layout = QVBoxLayout(self)
        layout.addWidget(_text_label(question.question, self))
        self.choices: list[QRadioButton] = []
        self._group = QButtonGroup(self)
        for option in question.options:
            button = QRadioButton(option.label, self)
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

    Args:
        request: Runtime request and its allowed answers.
        parent: Owning panel content widget.
    """

    submitted = Signal(object)

    def __init__(self, request: "HumanRequest", parent: QWidget) -> None:
        """Create only the decision controls advertised by this request."""
        super().__init__(
            {"question": "質問", "command": "コマンド承認", "file": "変更承認"}[request.kind],
            parent,
        )
        self.request = request
        self.is_live = True
        self.editors: dict[str, QuestionInput] = {}
        self.decision_buttons: dict[str, QPushButton] = {}
        layout = QVBoxLayout(self)
        self.status = _text_label("回答待ち", self)
        layout.addWidget(self.status)
        if request.details:
            details = QPlainTextEdit(self)
            details.setPlainText(request.details)
            details.setReadOnly(True)
            details.setMaximumHeight(180)
            layout.addWidget(details)
        for question in request.questions:
            editor = QuestionInput(question, self)
            self.editors[question.id] = editor
            editor.changed.connect(self._validate)
            layout.addWidget(editor)
        actions = QHBoxLayout()
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
            actions.addWidget(button)
        layout.addLayout(actions)
        self._validate()

    def invalidate(self, *, submitted: bool = False) -> None:
        """Permanently close answer controls without erasing the runtime's request."""
        self.is_live = False
        self.status.setText("送信済み" if submitted else "失効")
        for editor in self.editors.values():
            editor.setEnabled(False)
            if editor.question.is_secret:
                editor.answer.clear()
        self.submit_button.setEnabled(False)
        for button in self.decision_buttons.values():
            button.setEnabled(False)

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
        self.invalidate(submitted=True)
        self.submitted.emit(AnswerSubmission(self.request.token, answers=answers))

    def _decide(self, decision: str) -> None:
        if not self.is_live or decision not in self.decision_buttons:
            return
        self.invalidate(submitted=True)
        self.submitted.emit(AnswerSubmission(self.request.token, decision=decision))
