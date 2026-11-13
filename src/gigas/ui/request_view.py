"""Question and approval controls attached to their selectable conversation text."""

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QRadioButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gigas.ui.prompt_edit import PromptEdit
from gigas.ui.request_card import DECISION_LABELS

if TYPE_CHECKING:
    from collections.abc import Callable

    from PySide6.QtGui import QAction, QMouseEvent

    from gigas.runtime.codex.models import Question
    from gigas.ui.request_card import RequestRecord
    from gigas.ui.transcript import Transcript


class _ChoiceLabel(QLabel):
    def __init__(self, value: str, choice: QRadioButton, parent: QWidget) -> None:
        super().__init__(value, parent)
        self._choice = choice

    def mouseReleaseEvent(self, event: "QMouseEvent") -> None:  # noqa: N802 -- Qt override
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and not self.hasSelectedText():
            self._choice.click()


def _label(value: str, parent: QWidget, choice: QRadioButton) -> QLabel:
    label = _ChoiceLabel(value, choice, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.TextSelectableByKeyboard
    )
    return label


class QuestionInput(QWidget):
    """Edit one explicit answer without sharing the ordinary message draft.

    Args:
        question: Public question with its permitted answer choices.
        draft: Existing local answer draft; empty means no initial selection.
        parent: Owning conversation viewport, if any.
    """

    changed = Signal(str)
    submit_requested = Signal()

    def __init__(
        self, question: "Question", draft: str = "", parent: QWidget | None = None
    ) -> None:
        """Create stable controls for one question and restore only its own draft."""
        super().__init__(parent)
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(10, 8, 10, 8)
        self.content.setSpacing(6)
        self.choices: dict[str, QRadioButton] = {}
        self.other_button: QRadioButton | None = None
        self.editor: PromptEdit | None = None
        self._group = QButtonGroup(self)
        for option in question.options:
            row = QWidget(self)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            choice = QRadioButton(row)
            choice.setAccessibleName(option.label)
            self._group.addButton(choice)
            self.choices[option.label] = choice
            layout.addWidget(choice, 0, Qt.AlignmentFlag.AlignTop)
            label = _label(option.label, row, choice)
            label.setBuddy(choice)
            layout.addWidget(label, 1)
            if option.description:
                layout.addWidget(_label(option.description, row, choice), 2)
            self.content.addWidget(row)
            choice.toggled.connect(
                lambda checked, value=option.label: self._choose(value) if checked else None
            )

        if not question.options or question.is_other:
            self.editor = PromptEdit(self)
            self.editor.setAccessibleName(question.header or question.question)
            self.editor.setFixedHeight(2 * self.editor.fontMetrics().lineSpacing() + 16)
            self.editor.set_draft(draft if draft not in self.choices else "")
            self.editor.submit_requested.connect(self.submit_requested.emit)
            self.editor.textChanged.connect(self._edit)
            if question.options:
                self.other_button = QRadioButton("自由入力", self)
                self._group.addButton(self.other_button)
                self.other_button.toggled.connect(self._choose_other)
                self.content.addWidget(self.other_button)
            self.content.addWidget(self.editor)
            self.editor.setVisible(not question.options)

        if draft in self.choices:
            self.choices[draft].setChecked(True)
        elif draft and self.other_button is not None:
            self.other_button.setChecked(True)

    def set_editable(self, editable: bool) -> None:
        """Enable draft changes while keeping unavailable text selectable.

        Args:
            editable: Whether this request currently accepts an explicit answer.
        """
        for choice in self.choices.values():
            choice.setEnabled(editable)
        if self.other_button is not None:
            self.other_button.setEnabled(editable)
        if self.editor is not None:
            self.editor.setReadOnly(not editable)

    def _choose(self, value: str) -> None:
        if self.editor is not None:
            self.editor.hide()
        self.changed.emit(value)

    def _choose_other(self, checked: bool) -> None:
        if self.editor is not None and checked:
            self.editor.show()
            self.changed.emit(self.editor.toPlainText())

    def _edit(self) -> None:
        if self.editor is not None and (self.other_button is None or self.other_button.isChecked()):
            self.changed.emit(self.editor.toPlainText())


class RequestView:
    """Keep request text and native controls together in one transcript.

    All document items are registered at creation, so later result updates stay
    at the request's original position. Input widgets are retained across updates;
    ordinary message drafts and their focus are never changed here.

    Args:
        record: Existing request lifetime, draft and delivery evidence.
        transcript: Selectable conversation document that owns attached controls.
        submit: Explicit submission callback receiving request token and decision.
    """

    def __init__(
        self,
        record: "RequestRecord",
        transcript: "Transcript",
        submit: "Callable[[str, str | None], None]",
    ) -> None:
        """Create one stable set of controls without submitting an answer."""
        self.record = record
        self.transcript = transcript
        self._submit = submit
        self._available = True
        self._expanded = False
        self.question_inputs: dict[str, QuestionInput] = {}
        self.decision_actions: dict[str, QAction] = {}
        self.more_button: QToolButton | None = None
        self.submit_button = QPushButton("回答を送信", transcript.viewport())
        self.submit_button.hide()
        self.submit_button.clicked.connect(self._answer)
        prefix = f"request:{(record.request.turn_id, record.request.token)!r}"
        self._summary_id = f"{prefix}:summary"
        self._details_id = f"{prefix}:details"
        self._status_id = f"{prefix}:status"
        self._question_ids = {
            question.id: f"{prefix}:question:{question.id}" for question in record.request.questions
        }
        transcript.update_item(self._summary_id, "", kind="request")
        transcript.update_item(self._details_id, "", kind="request")
        for question in record.request.questions:
            transcript.update_item(self._question_ids[question.id], "", kind="request")
            controls = QuestionInput(
                question, record.drafts.get(question.id, ""), transcript.viewport()
            )
            controls.changed.connect(
                lambda value, question_id=question.id: self._edit_draft(question_id, value)
            )
            controls.submit_requested.connect(self._answer)
            self.question_inputs[question.id] = controls
        transcript.update_item(self._status_id, "", kind="request")
        self._summary_control = QWidget(transcript.viewport())
        summary_layout = QHBoxLayout(self._summary_control)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.addStretch()
        self.expand_button = QToolButton(self._summary_control)
        self.expand_button.setAccessibleName("回答の詳細")
        self.expand_button.setArrowType(Qt.ArrowType.DownArrow)
        self.expand_button.setCheckable(True)
        self.expand_button.toggled.connect(self._expand)
        summary_layout.addWidget(self.expand_button)
        self._summary_control.hide()

        if self.question_inputs:
            self._summary_control.setParent(next(iter(self.question_inputs.values())))
            last = next(reversed(self.question_inputs.values()))
            last.content.addWidget(self.submit_button)
            self.submit_button.show()
        else:
            self._create_approval()
        self.refresh()

    def _create_approval(self) -> None:
        controls = QWidget(self.transcript.viewport())
        self.record.controls = controls
        self._summary_control.setParent(controls)
        self.submit_button.setParent(controls)
        layout = QHBoxLayout(controls)
        layout.setContentsMargins(10, 8, 10, 8)
        for decision in self.record.request.decisions:
            if decision == "cancel":
                self.more_button = QToolButton(controls)
                self.more_button.setText("その他")
                self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                menu = QMenu(self.more_button)
                action = menu.addAction(DECISION_LABELS[decision])
                action.triggered.connect(lambda _checked=False: self._decide("cancel"))
                self.decision_actions[decision] = action
                self.more_button.setMenu(menu)
                layout.addWidget(self.more_button)
            else:
                button = QPushButton(DECISION_LABELS[decision], controls)
                button.clicked.connect(lambda _checked=False, value=decision: self._decide(value))
                self.record.decision_buttons[decision] = button
                layout.addWidget(button)
        layout.addStretch()

    def refresh(self, available: bool = True) -> None:
        """Update delivery state without rebuilding inputs or changing their drafts.

        Args:
            available: Whether the connection currently permits request submission.
        """
        self._available = available
        record = self.record
        sent = record.state == "sent"
        editable = available and record.is_live
        if record.request.kind != "question":
            self.transcript.update_item(
                self._details_id, record.request.details or record.title, kind="request"
            )
            self.transcript.set_item_control(
                self._details_id, record.controls if record.is_live else None
            )
            for button in record.decision_buttons.values():
                button.setEnabled(editable)
            for action in self.decision_actions.values():
                action.setEnabled(editable)
            if self.more_button is not None:
                self.more_button.setEnabled(editable)
            outcome = record.answer_text if sent else ""
            if not record.is_live and not sent:
                outcome = "\n".join(value for value in (record.answer_text, record.label) if value)
            self.transcript.update_item(self._status_id, outcome, kind="request")
            return

        first = record.request.questions[0]
        summary = record.submitted_answers.get(first.id, "") if sent else ""
        self.transcript.update_item(self._summary_id, summary, kind="request")
        self.transcript.set_item_control(self._summary_id, self._summary_control if sent else None)
        self.transcript.update_item(
            self._details_id,
            record.request.details if not sent or self._expanded else "",
            kind="request",
        )
        for question in record.request.questions:
            body = "\n".join(value for value in (question.header, question.question) if value)
            if sent:
                if self._expanded:
                    options = "\n".join(
                        value
                        for option in question.options
                        for value in (option.label, option.description)
                        if value
                    )
                    body = "\n".join(
                        value
                        for value in (body, options, record.submitted_answers.get(question.id, ""))
                        if value
                    )
                else:
                    body = ""
            item_id = self._question_ids[question.id]
            self.transcript.update_item(item_id, body, kind="request")
            controls = self.question_inputs[question.id]
            controls.set_editable(editable)
            self.transcript.set_item_control(item_id, None if sent else controls)
        self.transcript.update_item(
            self._status_id, record.label if not record.is_live and not sent else "", kind="request"
        )
        self.submit_button.setEnabled(editable and record.complete)

    def _edit_draft(self, question_id: str, value: str) -> None:
        if self.record.is_live and self._available:
            self.record.drafts[question_id] = value
            self.submit_button.setEnabled(self.record.complete)

    def _answer(self) -> None:
        if self._available and self.record.is_live and self.record.complete:
            self._submit(self.record.request.token, None)

    def _decide(self, decision: str) -> None:
        if self._available and self.record.is_live:
            self._submit(self.record.request.token, decision)

    def _expand(self, expanded: bool) -> None:
        self._expanded = expanded
        self.expand_button.setArrowType(
            Qt.ArrowType.UpArrow if expanded else Qt.ArrowType.DownArrow
        )
        self.refresh(self._available)
