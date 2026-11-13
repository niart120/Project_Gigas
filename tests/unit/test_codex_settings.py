from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from gigas.runtime.codex.models import (
    AccountInfo,
    RateLimitWindow,
    UsageBucket,
    UsageCredits,
    UsageEvent,
)
from gigas.ui.codex_settings import CodexSettings


def test_usage_values_do_not_invent_missing_windows_or_credit_balances(qtbot: QtBot) -> None:
    owner = QWidget()
    qtbot.addWidget(owner)
    popup = CodexSettings(owner)
    popup.set_account(AccountInfo("player@example.test", "plus", "Game room", True))
    reset = 1_800_000_000
    popup.set_usage(
        UsageEvent(
            (
                UsageBucket(
                    "codex",
                    "通常モデル",
                    RateLimitWindow(27, 300, reset),
                    RateLimitWindow(36, 10080),
                    UsageCredits(True, False, "8.50"),
                ),
            ),
            available=True,
        )
    )
    text = popup.view.toPlainText()
    assert "27%" in text
    assert "36%" in text
    assert "8.50" in text
    assert "一週間" in text
    assert "Game room" in popup.account.text()
    assert QDateTime.fromSecsSinceEpoch(reset).toLocalTime().toString("MM/dd HH:mm") in text
    popup.set_usage(UsageEvent((UsageBucket("codex"),), available=True))
    assert "27%" not in popup.view.toPlainText()
    assert "0%" not in popup.view.toPlainText()
    assert "8.50" not in popup.view.toPlainText()
    popup.set_usage(UsageEvent())
    assert "codex" not in popup.view.toPlainText()


def test_empty_usage_does_not_reserve_a_full_history_panel(qtbot: QtBot) -> None:
    owner = QWidget()
    qtbot.addWidget(owner)
    panel = CodexSettings(owner)
    panel.show()
    owner.show()
    assert panel.view.height() <= panel.view.fontMetrics().height() * 3
    short_height = panel.view.height()
    panel.set_usage(
        UsageEvent(
            tuple(UsageBucket(str(i), primary=RateLimitWindow(i, 10080)) for i in range(20)),
            available=True,
        )
    )
    qtbot.waitUntil(lambda: panel.view.verticalScrollBar().maximum() > 0)
    assert panel.view.height() > short_height
    assert panel.view.height() < panel.view.document().size().height()
    bar = panel.view.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    position = bar.value()
    panel.view.setTextCursor(panel.view.document().find("10%"))
    panel.set_usage(
        UsageEvent(
            tuple(
                UsageBucket(str(i), primary=RateLimitWindow(i if i < 19 else 25, 10080))
                for i in range(20)
            ),
            available=True,
        )
    )
    assert panel.view.textCursor().selectedText() == "10%"
    assert bar.value() == position
    panel.reset()
    qtbot.waitUntil(lambda: panel.view.height() == short_height)
