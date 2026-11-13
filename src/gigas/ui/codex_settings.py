"""Compact account and usage values from public Codex events."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QSizePolicy, QToolButton

from gigas.ui.transcript import Transcript

if TYPE_CHECKING:
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
        self.rows = QGridLayout(self)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setColumnStretch(1, 1)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.rows.setVerticalSpacing(14)
        self.account = Transcript(self, show_headings=False)
        self.sandbox = Transcript(self, show_headings=False)
        self.view = Transcript(self, show_headings=False)
        self.connection = QLabel("未接続", self)
        self.connection.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for row, title, view in (
            (0, "ChatGPTアカウント", self.account),
            (2, "Codex App Server", self.connection),
            (3, "Codexサンドボックス", self.sandbox),
            (4, "利用状況", self.view),
        ):
            self.rows.addWidget(QLabel(title, self), row, 0)
            self.rows.addWidget(view, row, 1)
            view.setAccessibleName(title)
        for view, height in ((self.account, 48), (self.sandbox, 42), (self.view, 100)):
            view.setFrameShape(QFrame.Shape.NoFrame)
            view.setStyleSheet("QTextBrowser { background: transparent; }")
            view.setMinimumHeight(height)
            view.setMaximumHeight(height)
        self.reset()
        self.hide()

    def action_button(self, action: QAction, row: int) -> QToolButton:
        """Bind a settings button to an existing connection action."""
        button = QToolButton(self)
        button.setDefaultAction(action)
        button.setToolTip("")
        self.rows.addWidget(button, row, 2)
        return button

    def reset(self) -> None:
        """Discard values when their connection generation is replaced or closed."""
        for view in (self.account, self.sandbox, self.view):
            view.clear()
        self.account.update_item("account", "未確認")
        self.sandbox.update_item("sandbox", "未確認")
        self.view.update_item("usage", "未取得")

    def set_account(self, account: "AccountInfo") -> None:
        """Replace public identity values without displaying credentials."""
        if account.authenticated:
            text = f"{account.email or '—'} / {account.plan_type or '—'}"
            if account.workspace_name:
                text += f"\n{account.workspace_name}"
        else:
            text = "未ログイン"
        self.account.update_item("account", text)

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
        self.sandbox.update_item("sandbox", f"{state}{detail}")
