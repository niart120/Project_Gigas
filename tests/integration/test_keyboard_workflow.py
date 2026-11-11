from typing import Literal

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QInputMethodEvent, QKeyEvent, QTextCursor
from PySide6.QtWidgets import QApplication, QMessageBox
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.runtime.codex.models import HumanRequest, Question, SessionStatus
from gigas.services.codex import ServiceEvent, ServiceResult
from gigas.ui.connection_panel import ConnectionPanel
from tests.integration.peers.controlled_capture import ControlledCapture
from tests.integration.test_video_gui import (
    video_shell as video_shell,  # noqa: PLC0414 -- reuse bounded application lifetime
)


@pytest.fixture
def ready_panel(qtbot: QtBot) -> ConnectionPanel:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.activate(1)
    panel.handle_result(ServiceResult(1, "connect"))
    panel.handle_event(ServiceEvent(1, SessionStatus("ready")))
    panel.prompt.setPlainText("draft")
    panel.prompt.setFocus()
    return panel


def test_settings_shortcut_opens_and_refocuses_the_single_editor(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = video_shell
    opened: list[bool] = []
    shell.window.settings_requested.connect(lambda: opened.append(True))
    shell.window.activateWindow()
    qtbot.waitUntil(shell.window.isActiveWindow)
    qtbot.keyClick(shell.window, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: shell.dialog is not None)
    dialog = shell.dialog
    assert dialog is not None
    qtbot.waitUntil(dialog.isActiveWindow)
    dialog.model.setText("unsaved keyboard draft")
    shell.window.activateWindow()
    qtbot.waitUntil(shell.window.isActiveWindow)
    qtbot.keyClick(shell.window, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(dialog.isActiveWindow)
    qtbot.keyClick(dialog.capture_choices, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
    assert opened == [True, True, True]
    assert shell.dialog is dialog
    assert dialog.model.text() == "unsaved keyboard draft"
    assert capture.refreshes == 1


def test_settings_shortcut_does_not_displace_an_unsaved_confirmation(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, _capture = video_shell
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    dialog.model.setText("unsaved")
    dialog.close()
    confirmation = shell.confirmation
    assert confirmation is not None
    qtbot.waitUntil(confirmation.isActiveWindow)
    opened: list[bool] = []
    shell.window.settings_requested.connect(lambda: opened.append(True))
    qtbot.keyClick(confirmation, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.activeWindow() is confirmation
    assert not opened
    assert dialog.model.text() == "unsaved"
    confirmation.button(QMessageBox.StandardButton.Cancel).click()


@pytest.mark.parametrize(
    ("key", "modifiers"),
    [
        (Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier),
        (
            Qt.Key.Key_Enter,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.KeypadModifier,
        ),
    ],
)
def test_control_enter_uses_the_send_button_once_and_retains_failed_drafts(
    ready_panel: ConnectionPanel, qtbot: QtBot, key: Qt.Key, modifiers: Qt.KeyboardModifier
) -> None:
    panel = ready_panel
    sent: list[str] = []
    clicks: list[bool] = []
    panel.send_requested.connect(sent.append)
    panel.send_button.clicked.connect(lambda: clicks.append(True))
    qtbot.keyClick(panel.prompt, key, modifiers)
    qtbot.keyClick(panel.prompt, key, modifiers)
    assert sent == ["draft"]
    assert clicks == [True]
    assert panel.prompt.toPlainText() == "draft"
    panel.handle_result(ServiceResult(1, "send", "injected failure"))
    qtbot.keyClick(panel.prompt, key, modifiers)
    assert sent == ["draft", "draft"]


def test_plain_enter_inserts_a_line_break_without_sending(
    ready_panel: ConnectionPanel, qtbot: QtBot
) -> None:
    panel = ready_panel
    sent: list[str] = []
    panel.send_requested.connect(sent.append)
    panel.prompt.moveCursor(QTextCursor.MoveOperation.End)
    qtbot.keyClick(panel.prompt, Qt.Key.Key_Return)
    qtbot.keyClicks(panel.prompt, "next line")
    assert panel.prompt.toPlainText() == "draft\nnext line"
    assert not sent


@pytest.mark.parametrize("committed_text", ["確定", ""])
def test_simulated_ime_composition_blocks_shortcut_until_commit_or_cancel(
    ready_panel: ConnectionPanel, qtbot: QtBot, committed_text: str
) -> None:
    panel = ready_panel
    sent: list[str] = []
    panel.send_requested.connect(sent.append)
    panel.prompt.moveCursor(QTextCursor.MoveOperation.End)
    # This replays Qt composition events; it does not exercise a Windows IME.
    QApplication.sendEvent(panel.prompt, QInputMethodEvent("変換中", []))
    qtbot.keyClick(panel.prompt, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert not sent
    assert panel.prompt.toPlainText() == "draft"
    commit = QInputMethodEvent()
    commit.setCommitString(committed_text)
    QApplication.sendEvent(panel.prompt, commit)
    assert not sent
    assert panel.prompt.toPlainText() == f"draft{committed_text}"
    qtbot.keyClick(panel.prompt, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert sent == [f"draft{committed_text}"]


@pytest.mark.parametrize("state", ["closed", "running", "unavailable", "empty", "whitespace"])
def test_shortcut_preserves_existing_send_availability(
    ready_panel: ConnectionPanel,
    qtbot: QtBot,
    state: Literal["closed", "running", "unavailable", "empty", "whitespace"],
) -> None:
    panel = ready_panel
    sent: list[str] = []
    panel.send_requested.connect(sent.append)
    if state in {"closed", "running"}:
        panel.handle_event(ServiceEvent(1, SessionStatus(state)))
    elif state == "unavailable":
        panel.set_available(False)
    else:
        panel.prompt.setPlainText("" if state == "empty" else " \n ")
    previous = panel.prompt.toPlainText()
    assert not panel.send_button.isEnabled()
    qtbot.keyClick(panel.prompt, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert not sent
    assert panel.prompt.toPlainText() == previous


def test_shortcut_does_not_submit_a_question_or_its_hidden_prompt(
    ready_panel: ConnectionPanel, qtbot: QtBot
) -> None:
    panel = ready_panel
    sent: list[str] = []
    answered: list[object] = []
    panel.send_requested.connect(sent.append)
    panel.answer_requested.connect(answered.append)
    panel.handle_event(ServiceEvent(1, SessionStatus("waiting")))
    panel.handle_event(
        ServiceEvent(
            1,
            HumanRequest(
                "question",
                "request",
                "thread",
                "turn",
                "question",
                questions=(Question("q", "方針", "次の方針"),),
            ),
        )
    )
    editor = panel.cards["question"].editors["q"].answer
    editor.setText("explicit answer")
    editor.setFocus()
    qtbot.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(panel.prompt, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert not sent
    assert not answered
    assert panel.prompt.toPlainText() == "draft"
    assert editor.text() == "explicit answer"


def test_repeated_key_press_does_not_send(ready_panel: ConnectionPanel) -> None:
    panel = ready_panel
    sent: list[str] = []
    panel.send_requested.connect(sent.append)
    event = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.ControlModifier,
        "\r",
        True,
    )
    QApplication.sendEvent(panel.prompt, event)
    assert not sent
    assert panel.prompt.toPlainText() == "draft"
