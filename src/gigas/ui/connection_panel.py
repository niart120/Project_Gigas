"""Codex connection controls and plain-text conversation rendering."""

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
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
)
from gigas.services.codex import ServiceEvent, ServiceResult
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
        self.cards: dict[str, RequestCard] = {}
        layout = QVBoxLayout(self)
        actions = QHBoxLayout()
        self.connect_button = QPushButton("接続", self)
        self.disconnect_button = QPushButton("切断", self)
        self.login_button = QPushButton("ログイン", self)
        self.status = QLabel("未接続", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.model = QLabel(self)
        self.model.setTextFormat(Qt.TextFormat.PlainText)
        for widget in (self.connect_button, self.disconnect_button, self.login_button, self.status):
            actions.addWidget(widget)
        actions.addStretch()
        actions.addWidget(self.model)
        layout.addLayout(actions)
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
        self.prompt = QPlainTextEdit(self)
        self.prompt.setMaximumHeight(130)
        layout.addWidget(self.prompt, 1)
        turn_actions = QHBoxLayout()
        turn_actions.addStretch()
        self.send_button = QPushButton("送信", self)
        self.interrupt_button = QPushButton("中断", self)
        turn_actions.addWidget(self.send_button)
        turn_actions.addWidget(self.interrupt_button)
        layout.addLayout(turn_actions)
        self.connect_button.clicked.connect(self.connect_requested)
        self.disconnect_button.clicked.connect(self._disconnect)
        self.login_button.clicked.connect(self._login)
        self.open_login_button.clicked.connect(self._open_login)
        self.send_button.clicked.connect(self._send)
        self.interrupt_button.clicked.connect(self.interrupt)
        self.prompt.textChanged.connect(self._render)
        self._render()

    def activate(self, generation: int) -> None:
        """Reset the view for a newly submitted connection generation."""
        self._generation = generation
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
            if event.phase in {"stopping", "stopped", "closed", "error"}:
                self._expire_requests()
            if event.phase != "authenticating":
                self.login_url.clear()
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
        self._pending.discard(result.operation)
        self._error = result.error
        if result.operation == "connect" and result.error:
            self._status = SessionStatus("error", detail=result.error)
        if (
            result.operation == "send"
            and not result.error
            and self.prompt.toPlainText() == self._submitted
        ):
            self.prompt.clear()
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
            if card.is_live:
                card.invalidate()

    def _reveal_request(self, generation: int, token: str) -> None:
        if generation == self._generation and token in self.cards:
            self._request_layout.activate()
            self._request_content.adjustSize()
            self.requests.ensureWidgetVisible(self.cards[token])

    def _render(self) -> None:
        available = self._available and "disconnect" not in self._pending
        phase = self._status.phase
        self.connect_button.setEnabled(available and self._settings_ready and not self._connected)
        self.disconnect_button.setEnabled(available and self._connected)
        self.login_button.setEnabled(
            available and phase == "login_required" and "login" not in self._pending
        )
        ready = available and phase in {"ready", "stopped"} and "send" not in self._pending
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
        self.status.setText(self._error or self._status.detail or _PHASE_LABELS[phase])
        self.model.setText(
            " · ".join(value for value in (self._status.model, self._status.effort) if value)
        )
