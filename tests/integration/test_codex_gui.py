from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.runtime.codex.models import ConnectionOptions, LoginEvent, SessionStatus, TextEvent
from gigas.services.codex import CodexServiceThread, ServiceEvent, ServiceResult
from gigas.ui.connection_panel import ConnectionPanel


def test_panel_login_text_send_failure_interrupt_and_disconnect(qtbot: QtBot) -> None:
    opened: list[QUrl] = []
    panel = ConnectionPanel(open_url=lambda url: bool(opened.append(url)))
    qtbot.addWidget(panel)
    panel.show()
    sent: list[str] = []
    stops: list[bool] = []
    panel.send_requested.connect(sent.append)
    panel.interrupt_requested.connect(lambda: stops.append(True))
    panel.activate(1)
    panel.handle_event(ServiceEvent(1, SessionStatus("login_required")))
    panel.handle_result(ServiceResult(1, "connect"))
    assert panel.login_button.isEnabled()
    assert not panel.send_button.isEnabled()
    panel.handle_event(ServiceEvent(1, SessionStatus("authenticating")))
    panel.handle_event(ServiceEvent(1, LoginEvent("https://example.org/login")))
    assert not opened
    panel.open_login_button.click()
    assert opened == [QUrl("https://example.org/login")]
    panel.handle_event(ServiceEvent(1, SessionStatus("ready", "chosen", "high", True)))
    assert panel.login_url.text() == ""
    assert not panel.open_login_button.isVisible()
    panel.prompt.setPlainText("hello")
    panel.send_button.click()
    panel.send_button.click()
    assert sent == ["hello"]
    panel.handle_result(ServiceResult(1, "send", "送信失敗"))
    assert panel.prompt.toPlainText() == "hello"
    assert panel.send_button.isEnabled()
    panel.send_button.click()
    panel.handle_event(ServiceEvent(1, SessionStatus("starting", "chosen", "high", True)))
    panel.interrupt_button.click()
    assert stops == [True]
    panel.handle_event(ServiceEvent(1, TextEvent("answer", "<b>A")))
    panel.handle_event(ServiceEvent(1, TextEvent("answer", "B</b>")))
    assert "<b>AB</b>" in panel.transcript.toPlainText()
    panel.handle_event(ServiceEvent(1, TextEvent("answer", "final", replace=True)))
    assert "final" in panel.transcript.toPlainText()
    assert "AB" not in panel.transcript.toPlainText()
    panel.handle_result(ServiceResult(1, "send"))
    assert panel.prompt.toPlainText() == ""
    panel.disconnect_button.click()
    panel.handle_event(ServiceEvent(1, SessionStatus("closed")))
    assert not panel.connect_button.isEnabled()
    panel.handle_result(ServiceResult(1, "disconnect"))
    assert panel.connect_button.isEnabled()


def test_shell_connect_uses_saved_snapshot_without_automatic_start(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = CodexServiceThread()
    shell = ApplicationShell(tmp_path / "settings.toml", service=service)
    snapshots: list[ConnectionOptions] = []

    def capture(options: ConnectionOptions) -> int:
        snapshots.append(options)
        return len(snapshots)

    # Replace only the GUI command boundary; no runtime launches in this test.
    monkeypatch.setattr(service, "connect_runtime", capture)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        assert not snapshots
        shell.open_settings()
        assert shell.dialog is not None
        shell.dialog.model.setPlainText("unsaved")
        shell.window.connection.connect_button.click()
        assert len(snapshots) == 1
        assert snapshots[0] == ConnectionOptions(tmp_path / "runtime")
    finally:
        assert shell.dialog is not None
        shell.dialog.set_values(shell.baseline)
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())
