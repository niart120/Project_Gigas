"""GUI diagnostic notifications and shutdown retain event-loop responsiveness."""

import asyncio
import json
import sys
import threading
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from gigas.app.diagnostics import ApplicationDiagnostics
from gigas.app.settings_worker import SettingsIO, SettingsWorker
from gigas.services.application import ApplicationServiceThread
from gigas.storage.diagnostics import DiagnosticSession
from gigas.ui.notifications import NotificationCenter


@pytest.mark.usefixtures("qapp")
def test_uncaught_exception_hooks_save_details_and_redact_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail() -> None:
        message = "uncaught Bearer private-hook-token"
        raise ValueError(message)

    diagnostics = ApplicationDiagnostics(tmp_path / "settings.toml")
    loop = asyncio.new_event_loop()
    original_sys, original_thread = sys.excepthook, threading.excepthook
    diagnostics.install_exception_hooks()
    try:
        try:
            fail()
        except ValueError as error:
            sys.excepthook(type(error), error, error.__traceback__)
            ApplicationServiceThread._loop_exception(
                loop, {"exception": error, "message": "async task failed"}
            )
    finally:
        diagnostics.join()
        loop.close()
    assert sys.excepthook is original_sys
    assert threading.excepthook is original_thread
    contents = diagnostics.session.path.read_text(encoding="utf-8")
    assert "private-hook-token" not in contents
    assert "private-hook-token" not in capsys.readouterr().err
    assert "ValueError" in contents
    assert "app.uncaught_exception" in contents
    assert "service.uncaught_exception" in contents


def test_settings_failure_keeps_exception_and_path(tmp_path: Path, qtbot: QtBot) -> None:
    session = DiagnosticSession(tmp_path / "settings.toml", lambda *_: None)
    session.start()
    worker = SettingsWorker(tmp_path, SettingsIO())
    try:
        with qtbot.waitSignal(worker.completed) as result:
            worker.perform("load", None)
        assert result.args[0].error
    finally:
        session.close()
    rows = [json.loads(line) for line in session.path.read_text(encoding="utf-8").splitlines()]
    failure = next(row for row in rows if row.get("event") == "settings.failed")
    assert failure["path"] == str(tmp_path)
    assert failure["operation"] == "load"
    assert "Traceback" in json.dumps(failure)


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
