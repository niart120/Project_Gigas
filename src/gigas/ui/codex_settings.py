"""Compact account and usage values from public Codex events."""

from math import ceil
from typing import TYPE_CHECKING

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from gigas.ui.transcript import Transcript

if TYPE_CHECKING:
    from PySide6.QtCore import QSizeF
    from PySide6.QtWidgets import QWidget

    from gigas.runtime.codex.models import AccountInfo, RateLimitWindow, SandboxEvent, UsageEvent


def _window_text(window: "RateLimitWindow") -> str:
    duration = "利用上限"
    if window.window_minutes is not None:
        minutes = window.window_minutes
        if minutes == 10080:
            duration = "一週間の利用上限"
        elif minutes >= 1440 and minutes % 1440 == 0:
            duration = f"{minutes // 1440}日間の利用上限"
        elif minutes >= 60 and minutes % 60 == 0:
            duration = f"{minutes // 60}時間の利用上限"
        else:
            duration = f"{minutes}分間の利用上限"
    reset = "—"
    if window.resets_at is not None:
        reset = QDateTime.fromSecsSinceEpoch(window.resets_at).toLocalTime().toString("MM/dd HH:mm")
    return f"{duration}　使用済み {window.used_percent}%\nリセット {reset}"


class CodexSettings(QFrame):
    """Show received account values without issuing RPCs or owning usage decisions.

    Args:
        parent: Owning connection panel.
    """

    def __init__(self, parent: "QWidget") -> None:
        """Prepare connection values for embedding in the settings dialog."""
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        connection_group = QGroupBox(self)
        self.rows = QGridLayout(connection_group)
        self.rows.setColumnStretch(1, 1)
        self.rows.setVerticalSpacing(12)
        layout.addWidget(connection_group)
        self.connection = self._value("未接続")
        self.sandbox = self._value("—")
        self.account = self._value("—")
        self.server_button = self._button("接続")
        self.sandbox_button = self._button("設定する")
        self.account_button = self._button("ログイン")
        for row, title, value, button in (
            (0, "Codex App Server", self.connection, self.server_button),
            (1, "サンドボックス環境", self.sandbox, self.sandbox_button),
            (2, "認証", self.account, self.account_button),
        ):
            self.rows.addWidget(QLabel(title, self), row, 0, Qt.AlignmentFlag.AlignVCenter)
            self.rows.addWidget(value, row, 1, Qt.AlignmentFlag.AlignVCenter)
            self.rows.addWidget(button, row, 2, Qt.AlignmentFlag.AlignVCenter)
            value.setAccessibleName(title)
        usage = QGroupBox("利用状況", self)
        usage_layout = QVBoxLayout(usage)
        self.view = Transcript(usage, show_headings=False)
        self.view.setAccessibleName("利用状況")
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setStyleSheet("QTextBrowser { background: transparent; }")
        self.view.document().documentLayout().documentSizeChanged.connect(self._fit_usage)
        usage_layout.addWidget(self.view)
        layout.addWidget(usage)
        self.reset()
        self.hide()

    def _value(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        label.setWordWrap(True)
        return label

    def _button(self, text: str) -> QPushButton:
        button = QPushButton(text, self)
        button.setAutoDefault(False)
        return button

    def _fit_usage(self, size: "QSizeF") -> None:
        self.view.setFixedHeight(min(160, ceil(size.height()) + 2))

    def reset(self) -> None:
        """Discard values when their connection generation is replaced or closed."""
        self.view.clear()
        self.account.setText("—")
        self.sandbox.setText("—")
        self.view.update_item("usage", "未取得")

    def set_account(self, account: "AccountInfo") -> None:
        """Replace public identity values without displaying credentials."""
        if account.authenticated:
            text = f"{account.email or '—'} / {account.plan_type or '—'}"
            if account.workspace_name:
                text += f"\n{account.workspace_name}"
        else:
            text = "未ログイン"
        if self.account.text() != text:
            self.account.setText(text)

    def set_usage(self, usage: "UsageEvent") -> None:
        """Replace the connection's merged usage snapshot, retaining reader selection."""
        lines = [] if usage.available else ["取得不能"]
        if usage.ordinary_usage_allowed is False:
            lines.append("通常利用 不可")
        for bucket in usage.buckets:
            lines.append(bucket.limit_name or bucket.limit_id or "—")
            lines.extend(
                _window_text(window)
                for window in (bucket.primary, bucket.secondary)
                if window is not None
            )
            if bucket.primary is None and bucket.secondary is None:
                lines.append("使用率 —")
            if bucket.credits is not None:
                credit = bucket.credits
                if credit.unlimited:
                    value = "無制限"
                elif credit.balance is not None:
                    value = credit.balance
                else:
                    value = "あり" if credit.has_credits else "なし"
                lines.append(f"クレジット {value}")
        if usage.available and not usage.buckets:
            lines.append("—")
        self.view.update_item("usage", "\n".join(lines))

    def set_sandbox(self, sandbox: "SandboxEvent") -> None:
        """Show runtime readiness separately from setup request acceptance."""
        state = (
            "準備中"
            if sandbox.setting_up
            else {
                "ready": "設定済み",
                "notConfigured": "未設定",
                "updateRequired": "更新が必要",
                None: "未確認",
            }[sandbox.readiness]
        )
        if sandbox.reconnect_required:
            state = "再接続が必要"
        detail = f"\n{sandbox.detail}" if sandbox.detail else ""
        text = f"{state}{detail}"
        if self.sandbox.text() != text:
            self.sandbox.setText(text)
