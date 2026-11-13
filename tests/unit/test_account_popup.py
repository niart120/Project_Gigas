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
from gigas.ui.account_popup import AccountPopup


def test_usage_values_do_not_invent_missing_windows_or_credit_balances(qtbot: QtBot) -> None:
    owner = QWidget()
    qtbot.addWidget(owner)
    popup = AccountPopup(owner)
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
    assert "7日" in text
    assert popup.view.document().find("Game room").block().text().endswith("Game room")
    assert QDateTime.fromSecsSinceEpoch(reset).toLocalTime().toString("MM/dd HH:mm t") in text
    popup.set_usage(UsageEvent((UsageBucket("codex"),), available=True))
    assert "27%" not in popup.view.toPlainText()
    assert "0%" not in popup.view.toPlainText()
    assert "8.50" not in popup.view.toPlainText()
    popup.set_usage(UsageEvent())
    assert "codex" not in popup.view.toPlainText()
