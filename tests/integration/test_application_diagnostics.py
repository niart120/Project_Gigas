"""GUI diagnostic notifications and shutdown retain event-loop responsiveness."""

import json
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from gigas.app.diagnostics import ApplicationDiagnostics
from gigas.ui.notifications import NotificationCenter


def test_initial_failure_reaches_existing_notification_center(tmp_path: Path, qtbot: QtBot) -> None:
    (tmp_path / "logs").write_text("blocked")
    owner = QWidget()
    qtbot.addWidget(owner)
    center = NotificationCenter(owner)
    diagnostics = ApplicationDiagnostics(tmp_path / "settings.toml")
    diagnostics.attach(center)
    qtbot.waitUntil(lambda: "logging" in center.notices)
    notice = center.notices["logging"]
    assert str(diagnostics.session.path) in notice.details
    assert "ゲーム操作は継続" in notice.explanation
    with qtbot.waitSignal(diagnostics.stopped):
        diagnostics.finish()
    diagnostics.join()


def test_shutdown_drain_does_not_block_gui(
    tmp_path: Path, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    diagnostics = ApplicationDiagnostics(tmp_path / "settings.toml")
    entered = Event()
    release = Event()
    original = diagnostics.session.close

    def slow_close() -> None:
        entered.set()
        release.wait(5)
        original()

    monkeypatch.setattr(diagnostics.session, "close", slow_close)
    ticks: list[bool] = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(5)
    try:
        diagnostics.finish()
        qtbot.waitUntil(entered.is_set)
        qtbot.waitUntil(lambda: len(ticks) >= 3)
        with qtbot.waitSignal(diagnostics.stopped):
            release.set()
    finally:
        release.set()
        diagnostics.join()
        timer.stop()
    rows = [
        json.loads(line)
        for line in diagnostics.session.path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["event"] for row in rows] == ["app.starting", "app.stopped"]
