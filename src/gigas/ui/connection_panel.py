"""Codex connection controls and item-based conversation rendering."""

from math import ceil
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from gigas.runtime.codex.models import (
    ActivityEvent,
    HumanRequest,
    LoginEvent,
    PlanEvent,
    ReasoningEvent,
    RequestResolved,
    SessionStatus,
    TextEvent,
    TurnStarted,
)
from gigas.services.codex import ServiceEvent, ServiceResult
from gigas.ui.elided_label import ElidedLabel
from gigas.ui.notifications import Notice
from gigas.ui.prompt_edit import PromptEdit, SubmitFilter
from gigas.ui.request_card import DECISION_LABELS, RequestRecord
from gigas.ui.transcript import Transcript

if TYPE_CHECKING:
    from collections.abc import Callable

    from PySide6.QtCore import QObject
    from PySide6.QtGui import QResizeEvent

_PHASE_LABELS = {
    "connecting": "接続中",
    "login_required": "未認証",
    "authenticating": "認証待ち",
    "ready": "待機",
    "starting": "開始中",
    "running": "実行中",
    "waiting": "回答待ち",
    "stopping": "中断中",
    "stopped": "停止",
    "error": "エラー",
    "closed": "切断",
}


class ConnectionPanel(QWidget):
    """Render connection events and emit explicit user actions.

    The failed signal carries a Notice for display-safe failures. A generation's
    failure IDs correlate state events with command results; activating a new
    connection discards those IDs, without modifying retained notification history.
    MainWindow places the status and model widgets in its shared status bar.

    Args:
        parent: Owning widget, if any.
        open_url: Browser opener called only after the URL button is pressed.
    """

    connect_requested = Signal()
    disconnect_requested = Signal()
    login_requested = Signal()
    send_requested = Signal(str)
    steer_requested = Signal(str, str)
    interrupt_requested = Signal()
    answer_requested = Signal(object)
    failed = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        open_url: "Callable[[QUrl], bool]" = QDesktopServices.openUrl,
    ) -> None:
        """Create an idle panel without starting services or authentication."""
        super().__init__(parent)
        self._open_url = open_url
        self._generation = 0
        self._connected = False
        self._available = True
        self._settings_ready = True
        self._status = SessionStatus("closed")
        self._pending: set[str] = set()
        self._submitted = ""
        self._active_turn: str | None = None
        self._current_plan: PlanEvent | None = None
        self._error = ""
        self._stopping = False
        self._failure_ids: set[int] = set()
        self.requests: dict[str, RequestRecord] = {}
        self._target: tuple[str, str] | None = None
        self._message_draft = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.connect_action = QAction("接続", self)
        self.disconnect_action = QAction("切断", self)
        self.login_action = QAction("ログイン", self)
        self.status = QLabel("未接続", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.model = ElidedLabel(parent=self)
        self.status.hide()
        self.model.hide()
        login = QHBoxLayout()
        self.login_url = QLineEdit(self)
        self.login_url.setReadOnly(True)
        self.open_login_button = QPushButton("ブラウザで開く", self)
        login.addWidget(self.login_url, 1)
        login.addWidget(self.open_login_button)
        layout.addLayout(login)
        self.plan_heading = QLabel("Plan", self)
        plan_row = QHBoxLayout()
        plan_row.addWidget(self.plan_heading, 0, Qt.AlignmentFlag.AlignTop)
        self.plan = Transcript(self, show_headings=False)
        self.plan.setFrameShape(QFrame.Shape.NoFrame)
        palette = self.plan.palette()
        palette.setBrush(QPalette.ColorRole.Base, self.palette().brush(QPalette.ColorRole.Window))
        self.plan.setPalette(palette)
        plan_row.addWidget(self.plan, 1)
        layout.addLayout(plan_row)
        document_layout = self.plan.document().documentLayout()
        if document_layout is not None:
            document_layout.documentSizeChanged.connect(self._fit_plan)
        self._clear_plan()
        self.history_heading = QLabel("History", self)
        layout.addWidget(self.history_heading)
        self.transcript = Transcript(self)
        self.transcript.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.transcript, 1)
        self.approvals = QScrollArea(self)
        self.approvals.setWidgetResizable(True)
        self.approvals.setFrameShape(QFrame.Shape.NoFrame)
        self._approval_content = QWidget(self.approvals)
        self._approval_layout = QVBoxLayout(self._approval_content)
        self._approval_layout.setContentsMargins(0, 0, 0, 0)
        self.approvals.setWidget(self._approval_content)
        self.approvals.viewport().installEventFilter(self)
        layout.addWidget(self.approvals)
        self.target = QComboBox(self)
        self.target.setAccessibleName("送信先")
        self.target.addItem("追加指示", None)
        message_heading = QHBoxLayout()
        message_heading.addWidget(QLabel("Message", self))
        message_heading.addWidget(self.target, 1)
        layout.addLayout(message_heading)
        self.composer = QStackedWidget(self)
        self.prompt = PromptEdit(self.composer)
        self.prompt.setAccessibleName("Message")
        self.secret = QLineEdit(self.composer)
        self.secret.setAccessibleName("秘密回答")
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.choice = QComboBox(self.composer)
        self.choice.setAccessibleName("回答")
        for combo in (self.target, self.choice):
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(12)
        self.composer.addWidget(self.prompt)
        self.composer.addWidget(self.secret)
        self.composer.addWidget(self.choice)
        layout.addWidget(self.composer)
        turn_actions = QHBoxLayout()
        turn_actions.addStretch()
        self.interrupt_button = QPushButton(self)
        self.interrupt_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.interrupt_button.setAccessibleName("中断")
        self.send_button = QPushButton("送信", self)
        turn_actions.addWidget(self.interrupt_button)
        turn_actions.addWidget(self.send_button)
        layout.addLayout(turn_actions)
        self.connect_action.triggered.connect(self.connect_requested)
        self.disconnect_action.triggered.connect(self._disconnect)
        self.login_action.triggered.connect(self._login)
        self.open_login_button.clicked.connect(self._open_login)
        self.send_button.clicked.connect(self._send)
        self.prompt.submit_requested.connect(self.send_button.click)
        self.interrupt_button.clicked.connect(self.interrupt)
        self.prompt.textChanged.connect(self._edit_draft)
        self.secret.textChanged.connect(self._edit_draft)
        self.choice.currentIndexChanged.connect(self._edit_draft)
        self.target.currentIndexChanged.connect(self._select_target)
        self._submit_filter = SubmitFilter(self)
        self._submit_filter.submit_requested.connect(self.send_button.click)
        self.secret.installEventFilter(self._submit_filter)
        self.choice.installEventFilter(self._submit_filter)
        self._render()

    def activate(self, generation: int) -> None:
        """Reset the view for a newly submitted connection generation."""
        self._generation = generation
        self._failure_ids.clear()
        self._connected = True
        self._status = SessionStatus("connecting")
        self._error = ""
        self._pending = {"connect"}
        self._stopping = False
        self._active_turn = None
        self._current_plan = None
        for record in self.requests.values():
            record.invalidate()
            if record.controls is not None:
                self._approval_layout.removeWidget(record.controls)
                record.controls.deleteLater()
        self.requests.clear()
        self._target = None
        self._refresh_targets()
        self._show_editor()
        self.transcript.clear()
        self._clear_plan()
        self.login_url.clear()
        self._render()

    def set_available(self, available: bool, *, settings_ready: bool = True) -> None:
        """Update command availability without coupling sessions to settings I/O.

        Args:
            available: Whether application lifetime permits session commands.
            settings_ready: Whether settings permit a new connection.
        """
        self._available = available
        self._settings_ready = settings_ready
        self._render()

    def service_finished(self) -> None:
        """Disable commands after an unexpected service-thread exit."""
        self._available = False
        self._status = SessionStatus("error", detail="接続サービス終了")
        self._notify_failure(self._status.detail)
        self.login_url.clear()
        self._expire_requests()
        self._archive_plan()
        self._render()

    @Slot(object)
    def handle_event(self, envelope: object) -> None:
        """Apply a typed event only when it belongs to the displayed connection."""
        if not isinstance(envelope, ServiceEvent) or envelope.generation != self._generation:
            return
        event = envelope.event
        if isinstance(event, SessionStatus):
            self._error = ""
            self._status = event
            if event.failure_id is not None or (event.phase == "error" and event.detail):
                self._notify_failure(event.detail, event.failure_id)
            if event.phase in {"stopping", "stopped", "closed", "error"}:
                self._expire_requests()
            if event.phase in {"ready", "stopped", "closed", "error"}:
                self._archive_plan()
                self._active_turn = None
            if event.phase != "authenticating":
                self.login_url.clear()
        elif isinstance(event, TurnStarted):
            if event.turn_id != self._active_turn and not self._stopping:
                self._archive_plan()
            self._active_turn = event.turn_id
        elif isinstance(event, LoginEvent):
            url = QUrl(event.url)
            if self._status.phase == "authenticating" and url.scheme() in {"http", "https"}:
                self.login_url.setText(event.url)
        elif isinstance(event, TextEvent):
            self.transcript.update_item(
                f"message:{event.item_id}", event.text, kind=event.role, append=not event.replace
            )
        elif isinstance(event, PlanEvent):
            if event.explanation:
                self.transcript.update_item(f"plan-explanation:{event.turn_id}", event.explanation)
            if event.turn_id == self._active_turn:
                self._current_plan = event
            if (
                event.turn_id == self._active_turn
                and not self._stopping
                and self._status.phase != "stopping"
                and "disconnect" not in self._pending
            ):
                self.plan.update_plan("current", event.steps)
                self.plan.setVisible(bool(event.steps))
                self.plan_heading.setVisible(bool(event.steps))
                self._fit_plan()
            else:
                self.transcript.update_plan(f"plan:{event.turn_id}", event.steps)
        elif isinstance(event, ReasoningEvent):
            self.transcript.update_item(
                f"reasoning:{(event.turn_id, event.item_id)!r}",
                "\n\n".join(event.summary),
                kind="reasoning",
            )
        elif isinstance(event, ActivityEvent):
            statuses = {
                "inProgress": "実行中",
                "completed": "完了",
                "failed": "失敗",
                "declined": "拒否",
            }
            self.transcript.update_item(
                f"activity:{(event.turn_id, event.item_id)!r}",
                "\n".join(
                    value
                    for value in (f"{statuses[event.status]} · {event.title}", event.details)
                    if value
                ),
                kind="activity",
            )
        elif isinstance(event, HumanRequest):
            if (
                event.token not in self.requests
                and not self._stopping
                and "disconnect" not in self._pending
                and self._status.phase in {"starting", "running", "waiting"}
            ):
                self._add_request(event)
        elif isinstance(event, RequestResolved) and event.token in self.requests:
            record = self.requests[event.token]
            record.invalidate()
            self._request_changed(record)
        self._render()

    @Slot(object)
    def handle_result(self, result: object) -> None:
        """Release operation controls and retain unsent text on failure."""
        if not isinstance(result, ServiceResult) or result.generation != self._generation:
            return
        if result.operation == "answer" and result.answer is not None:
            record = self.requests.get(result.answer.token)
            if record is not None:
                record.finish_submission(result.answer.outcome)
                self._request_changed(record)
        if (
            result.operation in {"send", "steer"}
            and result.operation in self._pending
            and result.message is not None
            and result.message.outcome == "accepted"
            and self._message_draft == self._submitted
            and not (self._target is None and self.prompt.is_composing)
        ):
            self._message_draft = ""
            if self._target is None:
                self._show_editor()
        self._pending.discard(result.operation)
        self._error = result.error
        if result.error:
            self._notify_failure(result.error, result.failure_id, operation=result.operation)
        if result.operation == "connect" and result.error:
            self._status = SessionStatus("error", detail=result.error)
        if result.operation == "disconnect" and not result.error:
            self._connected = False
            self._status = SessionStatus("closed")
        self._render()

    def interrupt(self) -> None:
        """Immediately block repeated interruption and enqueue a stop request."""
        if not self._connected or "stop" in self._pending or "disconnect" in self._pending:
            return
        self._pending.add("stop")
        self._stopping = True
        self._expire_requests()
        self._render()
        self.interrupt_requested.emit()

    def _disconnect(self) -> None:
        self._error = ""
        self._pending.add("disconnect")
        self._expire_requests()
        self.login_url.clear()
        self._render()
        self.disconnect_requested.emit()

    def _login(self) -> None:
        self._error = ""
        self._pending.add("login")
        self._render()
        self.login_requested.emit()

    def _open_login(self) -> None:
        url = QUrl(self.login_url.text())
        if self._status.phase == "authenticating" and url.scheme() in {"http", "https"}:
            self._open_url(url)

    def _send(self) -> None:
        if not self.send_button.isEnabled():
            return
        if self._target is not None:
            self._answer(self._target[0])
            return
        self._submitted = self._message_draft
        self._error = ""
        steering = self._status.phase in {"running", "waiting"}
        self._pending.add("steer" if steering else "send")
        if not steering:
            self._stopping = False
        self._render()
        if steering and self._active_turn is not None:
            self.steer_requested.emit(self._submitted, self._active_turn)
        else:
            self.send_requested.emit(self._submitted)

    def _add_request(self, request: HumanRequest) -> None:
        previous_target = self._target
        record = RequestRecord(request, len(self.requests) + 1)
        self.requests[request.token] = record
        if request.kind != "question":
            controls = QWidget(self._approval_content)
            record.controls = controls
            form = QFormLayout(controls)
            form.setContentsMargins(0, 0, 0, 0)
            form.setVerticalSpacing(4)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            field = QWidget(controls)
            actions = QHBoxLayout(field)
            actions.setContentsMargins(0, 0, 0, 0)
            actions.setSpacing(4)
            actions.addStretch()
            form.addRow(record.title, field)
            for decision in request.decisions:
                button = QPushButton(DECISION_LABELS[decision], controls)
                button.clicked.connect(
                    lambda _checked=False, token=request.token, value=decision: self._answer(
                        token, value
                    )
                )
                record.decision_buttons[decision] = button
                actions.addWidget(button)
            self._approval_layout.addWidget(controls)
        if (
            request.questions
            and self._target is None
            and not self._message_draft
            and not self.prompt.is_composing
            and not self._pending.intersection({"send", "steer"})
        ):
            first = next(
                item for item in self.requests.values() if item.is_live and item.request.questions
            )
            self._target = (first.request.token, first.request.questions[0].id)
        self._request_changed(record)
        if self._target != previous_target:
            self._show_editor()

    def _answer(self, token: str, decision: str | None = None) -> None:
        record = self.requests[token]
        if not self._available or self._stopping or "disconnect" in self._pending:
            return
        submission = record.submit(decision)
        if submission is not None:
            self._request_changed(record)
            self._render()
            self.answer_requested.emit(submission)

    def _request_changed(self, record: RequestRecord) -> None:
        request = record.request
        body = [f"{record.title} · {record.label}"]
        if request.details:
            body.append(request.details)
        for question in request.questions:
            body.append(f"{question.header}\n{question.question}")
            body.extend(f"{option.label}: {option.description}" for option in question.options)
        key = (request.turn_id, request.token)
        self.transcript.update_item(f"request:{key!r}", "\n".join(body), kind="request")
        if record.answer_text:
            self.transcript.update_item(
                f"answer:{key!r}",
                f"{record.title} · {record.label}\n{record.answer_text}",
                kind="answer",
            )
        if record.controls is not None:
            record.controls.setVisible(record.is_live)
            for button in record.decision_buttons.values():
                button.setEnabled(record.is_live)
        previous_target = self._target
        self._refresh_targets()
        if self._target != previous_target or (
            self._target is not None and self._target[0] == request.token
        ):
            self._show_editor()

    def _refresh_targets(self) -> None:
        with QSignalBlocker(self.target):
            self.target.clear()
            self.target.addItem("追加指示", None)
            for token, record in self.requests.items():
                for question in record.request.questions:
                    if record.state in {"waiting", "sending"} or record.drafts.get(question.id):
                        label = f"{record.title} · {question.header}"
                        if not record.is_live:
                            label += f" · {record.label}"
                        self.target.addItem(label, (token, question.id))
            index = next(
                (i for i in range(self.target.count()) if self.target.itemData(i) == self._target),
                -1,
            )
            if index < 0:
                self._target = None
                index = 0
            self.target.setCurrentIndex(index)
        self.target.setVisible(self.target.count() > 1)
        self.approvals.setVisible(
            any(record.is_live and record.controls is not None for record in self.requests.values())
        )
        self._fit_approvals()

    def _select_target(self) -> None:
        self._target = self.target.currentData()
        self._show_editor()
        self._render()

    def _show_editor(self) -> None:
        # Restoring another target must not feed its text back into the previous draft.
        self._submit_filter.reset_composition()
        with QSignalBlocker(self.prompt), QSignalBlocker(self.secret), QSignalBlocker(self.choice):
            self.secret.clear()
            self.choice.clear()
            self.choice.setEditable(False)
            if self._target is None:
                self.prompt.set_draft(self._message_draft)
                self.composer.setCurrentWidget(self.prompt)
                self._fit_composer()
                return
            token, question_id = self._target
            record = self.requests[token]
            question = next(item for item in record.request.questions if item.id == question_id)
            text = record.drafts.get(question_id, "")
            self.prompt.set_draft("")
            if question.options and not question.is_other:
                self.choice.addItems([option.label for option in question.options])
                if question.is_secret:
                    self.choice.setEditable(True)
                    editor = self.choice.lineEdit()
                    if editor is not None:
                        editor.setReadOnly(True)
                        editor.setEchoMode(QLineEdit.EchoMode.Password)
                        editor.setAccessibleName("秘密回答")
                        editor.installEventFilter(self._submit_filter)
                self.choice.setCurrentIndex(self.choice.findText(text) if text else -1)
                self.composer.setCurrentWidget(self.choice)
            elif question.is_secret:
                self.secret.setText(text)
                self.composer.setCurrentWidget(self.secret)
            else:
                self.prompt.set_draft(text)
                self.composer.setCurrentWidget(self.prompt)
        self._fit_composer()

    def _edit_draft(self) -> None:
        if self._target is None:
            self._message_draft = self.prompt.toPlainText()
        else:
            record = self.requests[self._target[0]]
            if not record.is_live:
                return
            editor = self.composer.currentWidget()
            text = (
                self.secret.text()
                if editor is self.secret
                else self.choice.currentText()
                if editor is self.choice
                else self.prompt.toPlainText()
            )
            record.drafts[self._target[1]] = text
        self._render()

    def _expire_requests(self) -> None:
        for record in self.requests.values():
            record.invalidate()
            record.mark_unconfirmed()
            self._request_changed(record)

    def _clear_plan(self) -> None:
        self.plan.clear()
        self.plan.hide()
        self.plan_heading.hide()

    def _archive_plan(self) -> None:
        if self._current_plan is not None:
            event = self._current_plan
            self.transcript.update_plan(f"plan:{event.turn_id}", event.steps)
            self._current_plan = None
        self._clear_plan()

    def _fit_plan(self) -> None:
        limit = (
            3 * self.plan.fontMetrics().lineSpacing() + 2 * self.plan.document().documentMargin()
        )
        self.plan.setFixedHeight(ceil(min(self.plan.document().size().height(), limit)))

    def _fit_composer(self) -> None:
        editor = self.composer.currentWidget()
        if editor is None or editor is self.prompt:
            # QPlainTextEdit also reserves one pixel for its final caret line.
            height = ceil(
                2 * self.prompt.fontMetrics().lineSpacing()
                + 2 * self.prompt.document().documentMargin()
                + 2 * self.prompt.frameWidth()
                + 1
            )
        else:
            height = editor.sizeHint().height()
        self.composer.setFixedHeight(height)

    def _fit_approvals(self) -> None:
        natural = max(
            self._approval_content.sizeHint().height(),
            self._approval_content.heightForWidth(self.approvals.viewport().width()),
        )
        self._approval_content.setMinimumHeight(natural)
        self.approvals.setFixedHeight(min(natural, 6 * self.fontMetrics().lineSpacing()))

    def eventFilter(self, watched: "QObject", event: QEvent) -> bool:  # noqa: N802 -- Qt override
        """Fit approval rows to their viewport using Qt's standard row wrapping."""
        if watched is self.approvals.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_approvals()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event: "QResizeEvent") -> None:  # noqa: N802 -- Qt override
        """Keep Message at its editor's natural height when resizing the panel."""
        super().resizeEvent(event)
        self._fit_composer()

    def _notify_failure(
        self, detail: str, failure_id: int | None = None, *, operation: str = ""
    ) -> None:
        if failure_id is not None:
            if failure_id in self._failure_ids:
                return
            self._failure_ids.add(failure_id)
        explanation = "Codexの処理に失敗しました。接続状態と詳細を確認してください。"
        if operation == "answer":
            explanation = (
                "回答・承認の送信に失敗しました。各要求の送信状態を確認してください。"
                "送信結果が不明な要求は自動再送しません。"
            )
        self.failed.emit(Notice("codex", "処理失敗", explanation, detail))

    def _render(self) -> None:
        self._fit_composer()
        available = self._available and "disconnect" not in self._pending
        phase = self._status.phase
        self.connect_action.setEnabled(available and self._settings_ready and not self._connected)
        self.disconnect_action.setEnabled(available and self._connected)
        self.login_action.setEnabled(
            available and phase == "login_required" and "login" not in self._pending
        )
        ready = (
            available
            and (
                phase in {"ready", "stopped"}
                or (
                    phase in {"running", "waiting"}
                    and self._active_turn is not None
                    and not self._stopping
                )
            )
            and not self._pending.intersection({"send", "steer"})
        )
        editable = available
        if self._target is not None:
            record = self.requests[self._target[0]]
            editable = available and record.is_live and not self._stopping
            ready = editable and record.complete
        else:
            ready = ready and bool(self._message_draft.strip())
        self.send_button.setEnabled(ready)
        self.prompt.setReadOnly(not editable)
        self.secret.setReadOnly(not editable)
        self.choice.setEnabled(editable)
        self.interrupt_button.setEnabled(
            available
            and (phase in {"starting", "running", "waiting"} or "send" in self._pending)
            and "stop" not in self._pending
        )
        visible = available and phase == "authenticating" and bool(self.login_url.text())
        self.login_url.setVisible(visible)
        self.open_login_button.setVisible(visible)
        self.status.setText(_PHASE_LABELS[phase])
        self.model.setText(
            " · ".join(value for value in (self._status.model, self._status.effort) if value)
        )
