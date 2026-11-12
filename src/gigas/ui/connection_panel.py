"""Codex connection controls and plain-text conversation rendering."""

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gigas.runtime.codex.models import (
    HumanRequest,
    LoginEvent,
    RequestResolved,
    SessionStatus,
    TextEvent,
    TurnStarted,
)
from gigas.services.codex import ServiceEvent, ServiceResult
from gigas.ui.elided_label import ElidedLabel
from gigas.ui.notifications import Notice
from gigas.ui.prompt_edit import PromptEdit
from gigas.ui.request_card import RequestCard
from gigas.ui.transcript import Transcript

if TYPE_CHECKING:
    from collections.abc import Callable

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
        self._texts: dict[str, tuple[str, str]] = {}
        self._submitted = ""
        self._error = ""
        self._stopping = False
        self._failure_ids: set[int] = set()
        self.cards: dict[str, RequestCard] = {}
        layout = QVBoxLayout(self)
        self._layout = layout
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
        self.transcript = Transcript(self)
        layout.addWidget(self.transcript, 3)
        self.requests = QScrollArea(self)
        self.requests.setWidgetResizable(True)
        self._request_content = QWidget(self.requests)
        self._request_layout = QVBoxLayout(self._request_content)
        self._request_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.requests.setWidget(self._request_content)
        self.requests.hide()
        layout.addWidget(self.requests, 2)
        self.prompt = PromptEdit(self)
        self.prompt.setMaximumHeight(130)
        layout.addWidget(self.prompt, 1)
        turn_actions = QHBoxLayout()
        turn_actions.addStretch()
        self.send_button = QPushButton("送信", self)
        self.interrupt_button = QPushButton("中断", self)
        turn_actions.addWidget(self.send_button)
        turn_actions.addWidget(self.interrupt_button)
        layout.addLayout(turn_actions)
        self.connect_action.triggered.connect(self.connect_requested)
        self.disconnect_action.triggered.connect(self._disconnect)
        self.login_action.triggered.connect(self._login)
        self.open_login_button.clicked.connect(self._open_login)
        self.send_button.clicked.connect(self._send)
        self.prompt.submit_requested.connect(self.send_button.click)
        self.interrupt_button.clicked.connect(self.interrupt)
        self.prompt.textChanged.connect(self._render)
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
        for card in self.cards.values():
            card.invalidate()
            self._request_layout.removeWidget(card)
            card.deleteLater()
        self.cards.clear()
        self.requests.hide()
        self._texts.clear()
        self.transcript.clear()
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
            if event.phase != "authenticating":
                self.login_url.clear()
        elif isinstance(event, TurnStarted):
            if self.prompt.toPlainText() == self._submitted:
                self.prompt.clear()
            for token, card in tuple(self.cards.items()):
                if card.request.turn_id != event.turn_id and card.is_settled:
                    self._request_layout.removeWidget(card)
                    card.deleteLater()
                    del self.cards[token]
            self.requests.setVisible(bool(self.cards))
        elif isinstance(event, LoginEvent):
            url = QUrl(event.url)
            if self._status.phase == "authenticating" and url.scheme() in {"http", "https"}:
                self.login_url.setText(event.url)
        elif isinstance(event, TextEvent):
            previous = self._texts.get(event.item_id, (event.role, ""))[1]
            self._texts[event.item_id] = (
                event.role,
                event.text if event.replace else previous + event.text,
            )
            self.transcript.set_text(
                "\n\n".join(
                    f"{'Codex' if role == 'assistant' else 'あなた'}\n{text}"
                    for role, text in self._texts.values()
                )
            )
        elif isinstance(event, HumanRequest):
            if (
                event.token not in self.cards
                and not self._stopping
                and "disconnect" not in self._pending
                and self._status.phase in {"starting", "running", "waiting"}
            ):
                card = RequestCard(event, self._request_content)
                card.submitted.connect(self.answer_requested)
                card.changed.connect(self._render)
                self.cards[event.token] = card
                self._request_layout.addWidget(card)
                self.requests.show()
                QTimer.singleShot(
                    0,
                    lambda generation=envelope.generation, token=event.token: self._reveal_request(
                        generation, token
                    ),
                )
        elif isinstance(event, RequestResolved) and event.token in self.cards:
            self.cards[event.token].invalidate()
        self._render()

    @Slot(object)
    def handle_result(self, result: object) -> None:
        """Release operation controls and retain unsent text on failure."""
        if not isinstance(result, ServiceResult) or result.generation != self._generation:
            return
        if result.operation == "answer" and result.answer is not None:
            card = self.cards.get(result.answer.token)
            if card is not None:
                card.finish_submission(result.answer.outcome)
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
        if "send" in self._pending:
            return
        self._submitted = self.prompt.toPlainText()
        self._error = ""
        self._stopping = False
        self._pending.add("send")
        self._render()
        self.send_requested.emit(self._submitted)

    def _expire_requests(self) -> None:
        for card in self.cards.values():
            card.invalidate()
            card.mark_unconfirmed()

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

    def _reveal_request(self, generation: int, token: str) -> None:
        if generation == self._generation and token in self.cards:
            self._request_layout.activate()
            self._request_content.adjustSize()
            self.requests.verticalScrollBar().setValue(self.cards[token].y())

    def _render(self) -> None:
        requesting = any(card.state in {"waiting", "sending"} for card in self.cards.values())
        self.prompt.setVisible(not requesting)
        self._layout.setStretchFactor(self.transcript, 1 if requesting else 3)
        self._layout.setStretchFactor(self.requests, 5 if requesting else 2)
        available = self._available and "disconnect" not in self._pending
        phase = self._status.phase
        self.connect_action.setEnabled(available and self._settings_ready and not self._connected)
        self.disconnect_action.setEnabled(available and self._connected)
        self.login_action.setEnabled(
            available and phase == "login_required" and "login" not in self._pending
        )
        ready = (
            available
            and not requesting
            and phase in {"ready", "stopped"}
            and "send" not in self._pending
        )
        self.send_button.setEnabled(ready and bool(self.prompt.toPlainText().strip()))
        self.prompt.setReadOnly(not ready)
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
