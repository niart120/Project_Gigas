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
    ConversationStatus,
    HumanRequest,
    LoginEvent,
    ModelSelection,
    PlanEvent,
    ReasoningEvent,
    RequestResolved,
    SandboxEvent,
    SessionStatus,
    TextEvent,
    UsageEvent,
    can_submit,
)
from gigas.services.codex import ServiceEvent, ServiceResult
from gigas.ui.codex_settings import CodexSettings
from gigas.ui.message_composer import MessageComposer
from gigas.ui.notifications import Notice
from gigas.ui.plan_panel import PlanPanel
from gigas.ui.request_card import RequestRecord
from gigas.ui.request_view import RequestView
from gigas.ui.transcript import Transcript

if TYPE_CHECKING:
    from collections.abc import Callable

_PHASE_LABELS = {
    "connecting": "接続中",
    "login_required": "未認証",
    "authenticating": "認証待ち",
    "ready": "待機",
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
    sandbox_requested = Signal()
    model_requested = Signal(str, str)
    goal_requested = Signal(str)
    goal_resume_requested = Signal()
    goal_clear_requested = Signal()
    send_requested = Signal(str)
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
        self._sandbox = SandboxEvent()
        self._status = SessionStatus("closed")
        self._conversation = ConversationStatus()
        self._pending: set[str] = set()
        self._submitted = ""
        self._current_plan: PlanEvent | None = None
        self._error = ""
        self._failure_ids: set[int] = set()
        self.requests: dict[str, RequestRecord] = {}
        self.request_views: dict[str, RequestView] = {}
        self._message_draft = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.connect_action = QAction("接続", self)
        self.disconnect_action = QAction("切断", self)
        self.login_action = QAction("ログイン", self)
        self.sandbox_action = QAction("設定する", self)
        self.codex_settings = CodexSettings(self)
        self.account_button = self.codex_settings.account_button
        self.server_button = self.codex_settings.server_button
        self.account_button.clicked.connect(self.login_action.trigger)
        self.server_button.clicked.connect(
            lambda: (
                self.disconnect_action.trigger()
                if self._connected
                else self.connect_action.trigger()
            )
        )
        self.codex_settings.sandbox_button.clicked.connect(self.sandbox_action.trigger)
        self.status = QLabel("未接続", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.hide()
        login = QHBoxLayout()
        self.login_url = QLineEdit(self)
        self.login_url.setReadOnly(True)
        self.open_login_button = QPushButton("ブラウザで開く", self)
        login.addWidget(self.login_url, 1)
        login.addWidget(self.open_login_button)
        self.codex_settings.rows.addLayout(login, 3, 0, 1, 3)
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
        self.sandbox_action.triggered.connect(self._setup_sandbox)
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
        self.codex_settings.reset()
        self._sandbox = SandboxEvent()
        self._conversation = ConversationStatus()
        self.plan_panel.set_goal(None)
        self._generation = generation
        self._failure_ids.clear()
        self._connected = True
        self._status = SessionStatus("connecting")
        self._error = ""
        self._pending = {"connect"}
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
        if self._conversation.turn_id is not None:
            self.plan_panel.set_plan(self._current_plan, interrupted=True)
        self._render()

    @Slot(object)
    def handle_event(self, envelope: object) -> None:
        """Apply a typed event only when it belongs to the displayed connection."""
        if not isinstance(envelope, ServiceEvent) or envelope.generation != self._generation:
            return
        event = envelope.event
        if isinstance(event, AccountInfo):
            self.codex_settings.set_account(event)
        elif isinstance(event, SandboxEvent):
            previous_detail = self._sandbox.detail
            self._sandbox = event
            self.codex_settings.set_sandbox(event)
            if event.setting_up and event.detail and event.detail != previous_detail:
                self._notify_failure(event.detail, operation="setup_windows_sandbox")
        elif isinstance(event, UsageEvent):
            self.codex_settings.set_usage(event)
        elif isinstance(event, ConversationStatus):
            if event.turn_id != self._conversation.turn_id:
                self._archive_plan()
                if event.turn_id is not None:
                    self._clear_plan()
                elif self._conversation.turn_id is not None:
                    interrupted = (
                        event.stopping or self._conversation.stopping or "stop" in self._pending
                    )
                    self.plan_panel.set_plan(
                        self._current_plan, interrupted=interrupted, finished=not interrupted
                    )
            self._conversation = event
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
                self.codex_settings.reset()
            if event.failure_id is not None or (event.phase == "error" and event.detail):
                self._notify_failure(event.detail, event.failure_id)
            elif (
                event.phase in {"ready", "login_required"}
                and event.detail
                and event.detail != previous_detail
                and not (event.model and event.effort)
            ):
                self._notify_failure(event.detail, operation="select_model")
            if event.phase in {"closed", "error"}:
                self._expire_requests()
                self._archive_plan()
                if self._conversation.turn_id is not None:
                    self.plan_panel.set_plan(self._current_plan, interrupted=True)
            if event.phase != "authenticating":
                self.login_url.clear()
        elif isinstance(event, LoginEvent):
            url = QUrl(event.url)
            if self._status.phase == "authenticating" and url.scheme() in {"http", "https"}:
                self.login_url.setText(event.url)
        elif isinstance(event, TextEvent):
            self.transcript.update_item(
                f"message:{event.item_id}", event.text, kind=event.role, append=not event.replace
            )
        elif isinstance(event, PlanEvent):
            if event.turn_id == self._conversation.turn_id:
                self._current_plan = event
                self.plan_panel.set_plan(
                    event,
                    interrupted=self._conversation.stopping or "stop" in self._pending,
                )
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
                and "disconnect" not in self._pending
                and self._status.phase == "ready"
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
            result.operation in {"send", "goal_set"}
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
            self.codex_settings.reset()
        self._render()

    def interrupt(self) -> None:
        """Immediately block repeated interruption and enqueue a stop request."""
        if not self._connected or "stop" in self._pending or "disconnect" in self._pending:
            return
        self._pending.add("stop")
        if self._conversation.turn_id is not None:
            self.plan_panel.set_plan(self._current_plan, interrupted=True)
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

    def _setup_sandbox(self) -> None:
        if not self.sandbox_action.isEnabled():
            return
        self._pending.add("setup_windows_sandbox")
        self._render()
        self.sandbox_requested.emit()

    def _send(self) -> None:
        if not self.send_button.isEnabled():
            return
        self._submitted = self._message_draft
        self._error = ""
        if self.composer.goal_mode.isChecked():
            self._pending.add("goal_set")
            self._render()
            self.goal_requested.emit(self._submitted)
            return
        self._pending.add("send")
        self._render()
        self.send_requested.emit(self._submitted)

    def _resume_goal(self) -> None:
        if not self.plan_panel.resume_button.isEnabled():
            return
        self._pending.add("goal_resume")
        self._render()
        self.goal_resume_requested.emit()

    def _clear_goal(self) -> None:
        if not self.plan_panel.clear_button.isEnabled():
            return
        self._pending.add("goal_clear")
        self._render()
        self.goal_clear_requested.emit()

    def _add_request(self, request: HumanRequest) -> None:
        record = RequestRecord(request, len(self.requests) + 1)
        self.requests[request.token] = record
        self.request_views[request.token] = RequestView(record, self.transcript, self._answer)

    def _answer(self, token: str, decision: str | None = None) -> None:
        record = self.requests[token]
        if not self._answers_available:
            return
        submission = record.submit(decision)
        if submission is not None:
            self._request_changed(record)
            self._render()
            self.answer_requested.emit(submission)

    def _request_changed(self, record: RequestRecord) -> None:
        self.request_views[record.request.token].refresh(available=self._answers_available)

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
        if (
            self._current_plan is not None
            and self._current_plan.turn_id == self._conversation.turn_id
        ):
            event = self._current_plan
            self.transcript.update_plan(
                f"plan:{event.turn_id}",
                event.steps,
                event.explanation,
                inactive=True,
            )

    @property
    def _answers_available(self) -> bool:
        return (
            self._available
            and self._connected
            and self._status.phase == "ready"
            and not self._conversation.stopping
            and not self._pending.intersection({"stop", "disconnect"})
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
        elif operation == "setup_windows_sandbox":
            explanation = (
                "実行環境の準備完了を確認できません。Windowsの確認画面と実行環境の状態を確認してください。"
                "準備後も利用できない場合は、切断して再接続してください。"
                "切断だけではWindowsの準備処理の終了を確認できません。"
            )
        self.failed.emit(Notice("codex", "処理失敗", explanation, detail))

    def _render(self) -> None:
        available = self._available and "disconnect" not in self._pending
        phase = self._status.phase
        conversation = self._conversation
        goal = conversation.goal
        self.connect_action.setEnabled(available and self._settings_ready and not self._connected)
        self.disconnect_action.setEnabled(available and self._connected)
        server_action = self.disconnect_action if self._connected else self.connect_action
        self.server_button.setText(server_action.text())
        self.server_button.setEnabled(server_action.isEnabled())
        self.codex_settings.connection.setText(
            "接続中" if phase == "connecting" else "接続済み" if self._connected else "未接続"
        )
        self.sandbox_action.setText(
            "更新する" if self._sandbox.readiness == "updateRequired" else "設定する"
        )
        self.login_action.setEnabled(
            available and phase == "login_required" and "login" not in self._pending
        )
        goal_active = goal is not None and goal.status == "active"
        self.sandbox_action.setEnabled(
            available
            and self._connected
            and phase in {"ready", "login_required"}
            and conversation.turn_id is None
            and not goal_active
            and not conversation.sending
            and not conversation.stopping
            and not self._pending
            and not self._sandbox.reconnect_required
            and not self._sandbox.setting_up
            and self._sandbox.readiness != "ready"
        )
        self.account_button.setEnabled(self.login_action.isEnabled())
        self.codex_settings.sandbox_button.setText(self.sandbox_action.text())
        self.codex_settings.sandbox_button.setEnabled(self.sandbox_action.isEnabled())
        submitted = bool(
            self._pending.intersection(
                {"send", "goal_set", "goal_resume", "goal_clear", "select_model"}
            )
        )
        stopping = conversation.stopping or "stop" in self._pending
        accepting = (
            available
            and self._connected
            and can_submit(self._status, conversation)
            and not submitted
            and not stopping
        )
        prepared = (
            self._sandbox.readiness == "ready"
            and not self._sandbox.setting_up
            and not self._sandbox.reconnect_required
        )
        ready = accepting and bool(self._status.model and self._status.effort) and prepared
        self.composer.goal_mode.setEnabled(
            available and not submitted and not conversation.sending and not stopping
        )
        self.plan_panel.resume_button.setEnabled(
            ready and goal is not None and goal.status not in {"active", "complete"}
        )
        self.plan_panel.clear_button.setEnabled(ready and goal is not None)
        self.prompt.setReadOnly(not available)
        for view in self.request_views.values():
            view.refresh(available=self._answers_available)
        running = (
            goal_active
            or conversation.turn_id is not None
            or conversation.sending
            or submitted
            or stopping
        )
        self.composer.render_actions(
            send_enabled=ready and bool(self._message_draft.strip()),
            interrupt_enabled=(available and self._connected and running and not stopping),
            running=running,
            has_draft=bool(self._message_draft.strip()),
        )
        visible = available and phase == "authenticating" and bool(self.login_url.text())
        self.login_url.setVisible(visible)
        self.open_login_button.setVisible(visible)
        self.status.setText(
            _PHASE_LABELS[phase]
            if phase != "ready"
            else "中断中"
            if stopping
            else "送信中"
            if conversation.sending or submitted
            else "回答待ち"
            if conversation.waiting_for_answer
            else "実行中"
            if conversation.turn_id is not None
            else "待機"
        )
        if not accepting and "select_model" not in self._pending:
            self.composer.popup.hide()
        self.composer.model_choice.setEnabled(accepting)
        self.composer.effort_choice.setEnabled(accepting)
        self.composer.model_button.setEnabled(
            accepting
            and self.composer.selection is not None
            and bool(self.composer.selection.choices)
        )
