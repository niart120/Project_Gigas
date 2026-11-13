"""Codex connection controls and item-based conversation rendering."""

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gigas.runtime.codex.models import (
    AccountInfo,
    ActivityEvent,
    GoalEvent,
    HumanRequest,
    LoginEvent,
    ModelSelection,
    PlanEvent,
    ReasoningEvent,
    RequestResolved,
    SessionStatus,
    TextEvent,
    TurnStarted,
    UsageEvent,
)
from gigas.services.codex import ServiceEvent, ServiceResult
from gigas.ui.account_popup import AccountPopup
from gigas.ui.message_composer import MessageComposer
from gigas.ui.notifications import Notice
from gigas.ui.plan_panel import PlanPanel
from gigas.ui.request_card import RequestRecord
from gigas.ui.request_view import RequestView
from gigas.ui.transcript import Transcript

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.runtime.codex.models import GoalState


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
    MainWindow places the connection status in its shared status bar.

    Args:
        parent: Owning widget, if any.
        open_url: Browser opener called only after the URL button is pressed.
    """

    connect_requested = Signal()
    disconnect_requested = Signal()
    login_requested = Signal()
    model_requested = Signal(str, str)
    goal_requested = Signal(str)
    goal_resume_requested = Signal()
    goal_clear_requested = Signal()
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
        self._usage_blocked = False
        self._goal: GoalState | None = None
        self._status = SessionStatus("closed")
        self._pending: set[str] = set()
        self._submitted = ""
        self._active_turn: str | None = None
        self._current_plan: PlanEvent | None = None
        self._error = ""
        self._stopping = False
        self._failure_ids: set[int] = set()
        self.requests: dict[str, RequestRecord] = {}
        self.request_views: dict[str, RequestView] = {}
        self._message_draft = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.connect_action = QAction("接続", self)
        self.disconnect_action = QAction("切断", self)
        self.login_action = QAction("ログイン", self)
        self.account_action = QAction("アカウント・利用状況", self)
        self.account_popup = AccountPopup(self)
        self.account_action.triggered.connect(self.account_popup.show_values)
        self.status = QLabel("未接続", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.hide()
        login = QHBoxLayout()
        self.login_url = QLineEdit(self)
        self.login_url.setReadOnly(True)
        self.open_login_button = QPushButton("ブラウザで開く", self)
        login.addWidget(self.login_url, 1)
        login.addWidget(self.open_login_button)
        layout.addLayout(login)
        self.plan_panel = PlanPanel(self)
        self.plan_panel.hide()  # MainWindow places this in the right pane.
        self.transcript = Transcript(self)
        self.transcript.setAccessibleName("会話")
        self.transcript.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.transcript, 1)
        self.composer = MessageComposer(self)
        self.composer.model_selected.connect(self._select_model)
        self.composer.goal_mode.toggled.connect(self._render)
        self.plan_panel.resume_button.clicked.connect(self._resume_goal)
        self.plan_panel.clear_button.clicked.connect(self._clear_goal)
        self.prompt = self.composer.prompt
        self.send_button = self.composer.send_button
        self.interrupt_button = self.composer.interrupt_button
        layout.addWidget(self.composer)
        self.connect_action.triggered.connect(self.connect_requested)
        self.disconnect_action.triggered.connect(self._disconnect)
        self.login_action.triggered.connect(self._login)
        self.open_login_button.clicked.connect(self._open_login)
        self.send_button.clicked.connect(self._send)
        self.prompt.submit_requested.connect(self.send_button.click)
        self.interrupt_button.clicked.connect(self.interrupt)
        self.prompt.textChanged.connect(self._edit_draft)
        self._render()

    def _select_model(self, model: str, effort: str) -> None:
        self._pending.add("select_model")
        self._render()
        self.model_requested.emit(model, effort)

    def activate(self, generation: int) -> None:
        """Reset the view for a newly submitted connection generation."""
        self.composer.set_choices(None)
        self.account_popup.reset()
        self._usage_blocked = False
        self._goal = None
        self.plan_panel.set_goal(None)
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
        self.requests.clear()
        self.request_views.clear()
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
        if self._active_turn is not None:
            self.plan_panel.set_plan(self._current_plan, interrupted=True)
        self._active_turn = None
        self._render()

    @Slot(object)
    def handle_event(self, envelope: object) -> None:
        """Apply a typed event only when it belongs to the displayed connection."""
        if not isinstance(envelope, ServiceEvent) or envelope.generation != self._generation:
            return
        event = envelope.event
        if isinstance(event, AccountInfo):
            self.account_popup.set_account(event)
        elif isinstance(event, UsageEvent):
            self._usage_blocked = event.ordinary_usage_allowed is False
            self.account_popup.set_usage(event)
        elif isinstance(event, GoalEvent):
            self._goal = event.goal
            self.plan_panel.set_goal(event.goal)
        elif isinstance(event, ModelSelection):
            self.composer.set_choices(event)
        elif isinstance(event, SessionStatus):
            self._error = ""
            previous_detail = self._status.detail
            self._status = event
            if event.phase in {"closed", "error"}:
                self.composer.set_choices(None)
            if event.phase == "closed":
                self.account_popup.reset()
            if event.failure_id is not None or (event.phase == "error" and event.detail):
                self._notify_failure(event.detail, event.failure_id)
            elif (
                event.phase in {"ready", "login_required"}
                and event.detail
                and event.detail != previous_detail
                and not (event.model and event.effort)
            ):
                self._notify_failure(event.detail, operation="select_model")
            if event.phase in {"stopping", "stopped", "closed", "error"}:
                self._expire_requests()
            if event.phase in {"ready", "stopped", "closed", "error"}:
                self._archive_plan()
                if self._active_turn is not None and not self._stopping:
                    self.plan_panel.set_plan(
                        self._current_plan,
                        interrupted=event.phase != "ready",
                        finished=event.phase == "ready",
                    )
                self._active_turn = None
            if event.phase != "authenticating":
                self.login_url.clear()
        elif isinstance(event, TurnStarted):
            if event.turn_id != self._active_turn and not self._stopping:
                self._archive_plan()
                self._clear_plan()
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
            if event.turn_id == self._active_turn:
                self._current_plan = event
            if (
                event.turn_id == self._active_turn
                and not self._stopping
                and self._status.phase != "stopping"
                and "disconnect" not in self._pending
            ):
                self.plan_panel.set_plan(event)
            else:
                self.transcript.update_plan(
                    f"plan:{event.turn_id}",
                    event.steps,
                    event.explanation,
                    inactive=True,
                )
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
            result.operation in {"send", "steer", "goal_set"}
            and result.operation in self._pending
            and result.message is not None
            and result.message.outcome == "accepted"
        ):
            if result.operation == "goal_set":
                self.composer.goal_mode.setChecked(False)
            if self._message_draft == self._submitted and not self.prompt.is_composing:
                self._message_draft = ""
                self.prompt.clear()
        self._pending.discard(result.operation)
        self._error = result.error
        if result.error:
            if result.operation == "select_model":
                self.composer.set_choices(self.composer.selection)
            self._notify_failure(result.error, result.failure_id, operation=result.operation)
        if result.operation == "connect" and result.error:
            self._status = SessionStatus("error", detail=result.error)
        if result.operation == "disconnect" and not result.error:
            self._connected = False
            self._status = SessionStatus("closed")
            self.account_popup.reset()
        self._render()

    def interrupt(self) -> None:
        """Immediately block repeated interruption and enqueue a stop request."""
        if not self._connected or "stop" in self._pending or "disconnect" in self._pending:
            return
        self._pending.add("stop")
        self._stopping = True
        if self._active_turn is not None:
            self.plan_panel.set_plan(self._current_plan, interrupted=True)
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
        self._submitted = self._message_draft
        self._error = ""
        if self.composer.goal_mode.isChecked():
            self._pending.add("goal_set")
            if self._goal is None or self._goal.status == "complete":
                self._stopping = False
            self._render()
            self.goal_requested.emit(self._submitted)
            return
        steering = self._status.phase in {"running", "waiting"}
        self._pending.add("steer" if steering else "send")
        if not steering:
            self._stopping = False
        self._render()
        if steering and self._active_turn is not None:
            self.steer_requested.emit(self._submitted, self._active_turn)
        else:
            self.send_requested.emit(self._submitted)

    def _resume_goal(self) -> None:
        if not self.plan_panel.resume_button.isEnabled():
            return
        self._pending.add("goal_resume")
        self._stopping = False
        self._render()
        self.goal_resume_requested.emit()

    def _clear_goal(self) -> None:
        if not self.plan_panel.clear_button.isEnabled():
            return
        self._pending.add("goal_clear")
        self._stopping = True
        self._expire_requests()
        self._render()
        self.goal_clear_requested.emit()

    def _add_request(self, request: HumanRequest) -> None:
        record = RequestRecord(request, len(self.requests) + 1)
        self.requests[request.token] = record
        self.request_views[request.token] = RequestView(record, self.transcript, self._answer)

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
        self.request_views[record.request.token].refresh(
            available=self._available and not self._stopping and "disconnect" not in self._pending
        )

    def _edit_draft(self) -> None:
        self._message_draft = self.prompt.toPlainText()
        self._render()

    def _expire_requests(self) -> None:
        for record in self.requests.values():
            record.invalidate()
            record.mark_unconfirmed()
            self._request_changed(record)

    def _clear_plan(self) -> None:
        self._current_plan = None
        self.plan_panel.set_plan(None)

    def _archive_plan(self) -> None:
        if self._current_plan is not None and self._current_plan.turn_id == self._active_turn:
            event = self._current_plan
            self.transcript.update_plan(
                f"plan:{event.turn_id}",
                event.steps,
                event.explanation,
                inactive=True,
            )

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
        elif operation == "select_model" and not (self._status.model and self._status.effort):
            explanation = (
                "モデル・推論レベルを選択できないため、送信できません。"
                "ログイン状態と候補を確認し、選び直してください。"
                "候補を取得できない場合は、切断して再接続してください。"
            )
        self.failed.emit(Notice("codex", "処理失敗", explanation, detail))

    def _render(self) -> None:
        available = self._available and "disconnect" not in self._pending
        phase = self._status.phase
        self.connect_action.setEnabled(available and self._settings_ready and not self._connected)
        self.disconnect_action.setEnabled(available and self._connected)
        self.account_action.setEnabled(available and self._connected)
        self.login_action.setEnabled(
            available and phase == "login_required" and "login" not in self._pending
        )
        goal_active = self._goal is not None and self._goal.status == "active"
        goal_pending = bool(self._pending.intersection({"goal_set", "goal_resume", "goal_clear"}))
        goal_mode = self.composer.goal_mode.isChecked()
        settled = phase in {"ready", "stopped"} and self._active_turn is None
        selection_valid = (
            bool(self._status.authenticated and self._status.model and self._status.effort)
            and not self._usage_blocked
        )
        self.composer.goal_mode.setEnabled(
            available
            and not goal_pending
            and not self._pending.intersection({"send", "steer"})
            and not any(record.state in {"waiting", "sending"} for record in self.requests.values())
        )
        self.plan_panel.resume_button.setEnabled(
            available
            and settled
            and selection_valid
            and not self._pending
            and self._goal is not None
            and self._goal.status not in {"active", "complete"}
        )
        self.plan_panel.clear_button.setEnabled(
            available
            and self._connected
            and phase in {"ready", "running", "waiting", "stopped"}
            and self._goal is not None
            and not self._pending
        )
        ready = (
            available
            and selection_valid
            and (
                phase in {"ready", "stopped"}
                or (
                    phase in {"running", "waiting"}
                    and self._active_turn is not None
                    and not self._stopping
                )
            )
            and not self._pending.intersection(
                {"send", "steer", "goal_set", "goal_resume", "goal_clear"}
            )
        )
        if goal_active and self._active_turn is None and not goal_mode:
            ready = False
        if goal_mode and (self._goal is None or self._goal.status == "complete") and not settled:
            ready = False
        if "stop" in self._pending or phase == "stopping":
            ready = False
        self.prompt.setReadOnly(not available)
        for view in self.request_views.values():
            view.refresh(available=available and not self._stopping)
        running = (
            goal_active
            or phase in {"starting", "running", "waiting", "stopping"}
            or bool(
                self._pending.intersection(
                    {"send", "stop", "goal_set", "goal_resume", "goal_clear"}
                )
            )
        )
        self.composer.render_actions(
            send_enabled=ready and bool(self._message_draft.strip()),
            interrupt_enabled=(
                available
                and (
                    goal_active
                    or phase in {"starting", "running", "waiting"}
                    or bool(self._pending.intersection({"send", "goal_set", "goal_resume"}))
                )
                and "stop" not in self._pending
                and not self._stopping
            ),
            running=running,
            has_draft=bool(self._message_draft.strip()),
        )
        visible = available and phase == "authenticating" and bool(self.login_url.text())
        self.login_url.setVisible(visible)
        self.open_login_button.setVisible(visible)
        self.status.setText(_PHASE_LABELS[phase])
        model_available = (
            self._available
            and self._connected
            and self._status.phase in {"ready", "running", "waiting", "stopped"}
            and not self._pending
        )
        if not model_available and "select_model" not in self._pending:
            self.composer.popup.hide()
        self.composer.model_choice.setEnabled(model_available)
        self.composer.effort_choice.setEnabled(model_available)
        self.composer.model_button.setEnabled(
            model_available
            and self.composer.selection is not None
            and bool(self.composer.selection.choices)
        )
