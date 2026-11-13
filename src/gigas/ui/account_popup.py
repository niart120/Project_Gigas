"""Compact account and usage values from public Codex events."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from gigas.ui.geometry import fit_to_screen
from gigas.ui.transcript import Transcript

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from gigas.runtime.codex.models import AccountInfo, RateLimitWindow, UsageEvent


def _window_text(window: "RateLimitWindow") -> str:
    duration = ""
    if window.window_minutes is not None:
        minutes = window.window_minutes
        if minutes >= 1440 and minutes % 1440 == 0:
            duration = f" · {minutes // 1440}日"
        elif minutes >= 60 and minutes % 60 == 0:
            duration = f" · {minutes // 60}時間"
        else:
            duration = f" · {minutes}分"
    reset = "—"
    if window.resets_at is not None:
        reset = (
            QDateTime.fromSecsSinceEpoch(window.resets_at).toLocalTime().toString("MM/dd HH:mm t")
        )
    return f"使用率 {window.used_percent}%{duration}\n回復 {reset}"


class AccountPopup(QFrame):
    """Show received account values without issuing RPCs or owning usage decisions.

    Args:
        parent: Owning connection panel.
    """

    def __init__(self, parent: "QWidget") -> None:
        """Prepare a hidden popup with selectable, scrollable values."""
        super().__init__(parent, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setWindowTitle("アカウント・利用状況")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.addWidget(QLabel(self.windowTitle(), self))
        self.view = Transcript(self, show_headings=False)
        self.view.setAccessibleName(self.windowTitle())
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self.view)
        self.resize(410, 320)
        self.reset()

    def reset(self) -> None:
        """Discard values when their connection generation is replaced or closed."""
        self.hide()
        self.view.clear()
        self.view.update_item("account", "アカウント —\n\n")
        self.view.update_item("usage", "利用枠 未取得")

    def show_values(self) -> None:
        """Open at the pointer and fit the popup to its current screen."""
        self.move(QCursor.pos())
        self.show()
        fit_to_screen(self)
        self.view.setFocus()

    def set_account(self, account: "AccountInfo") -> None:
        """Replace public identity values without displaying credentials."""
        if account.authenticated:
            text = (
                f"アカウント {account.email or '—'}\n"
                f"プラン {account.plan_type or '—'}\n"
                f"ワークスペース {account.workspace_name or '—'}"
            )
        else:
            text = "アカウント 未認証"
        self.view.update_item("account", text + "\n\n")

    def set_usage(self, usage: "UsageEvent") -> None:
        """Replace the connection's merged usage snapshot, retaining reader selection."""
        lines = ["利用枠" if usage.available else "利用枠 取得不能"]
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
