from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.storage.settings import load_settings
from tests.integration.peers.controlled_capture import ControlledCapture
from tests.integration.test_settings_apply_contract import (
    app as app,  # noqa: PLC0414 -- shared isolated application fixture
)
from tests.integration.test_settings_apply_contract import open_editor


def test_ok_waits_for_first_frame_before_closing(
    app: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = app
    dialog = open_editor(shell, capture)
    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData("path-b"))
    dialog.ok_button.click()
    qtbot.waitUntil(lambda: len(capture.connections) == 1)
    assert dialog.isVisible()
    assert not dialog.closing_allowed
    dialog.close()
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    assert dialog.isVisible()
    assert shell.confirmation is None
    capture.stream()
    qtbot.waitUntil(lambda: not dialog.isVisible())
    assert shell.window.isVisible()


def test_cancel_discards_only_edits_since_last_apply(
    app: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = app
    dialog = open_editor(shell, capture)
    dialog.model.setText("applied")
    dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    dialog.model.setText("discard")
    dialog.close_button.click()
    assert shell.confirmation is None
    assert not dialog.isVisible()
    assert load_settings(shell.path).model == "applied"
    shell.open_settings()
    assert dialog.model.text() == "applied"


def test_failed_ok_does_not_close_on_later_apply_retry(
    app: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = app
    dialog = open_editor(shell, capture)
    capture.accept_connection = False
    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData("path-b"))
    dialog.ok_button.click()
    qtbot.waitUntil(lambda: shell.video.apply_state == "failed")
    assert dialog.isVisible()
    assert dialog.closing_allowed
    capture.accept_connection = True
    dialog.save_button.click()
    qtbot.waitUntil(lambda: len(capture.connections) == 1)
    capture.stream()
    qtbot.waitUntil(lambda: not shell.video.applying)
    assert dialog.isVisible()


def test_stopping_ok_clears_automatic_close_and_unstarted_target(
    app: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = app
    dialog = open_editor(shell, capture)
    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData("path-a"))
    dialog.ok_button.click()
    qtbot.waitUntil(lambda: len(capture.connections) == 1)
    shell.window.video.disconnect_action.trigger()
    qtbot.waitUntil(lambda: not shell.video.applying)
    if capture.snapshot().phase == "enumerating":
        capture.complete_enumeration()
        shell.video.timer.timeout.emit()
    assert dialog.isVisible()
    assert dialog.capture_target == ""
    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData("path-a"))
    dialog.save_button.click()
    qtbot.waitUntil(lambda: len(capture.connections) == 2)
    capture.stream()
    qtbot.waitUntil(lambda: not shell.video.applying)
    assert dialog.isVisible()


def test_ok_without_changes_closes_without_save_or_connection(
    app: tuple[ApplicationShell, ControlledCapture],
) -> None:
    shell, capture = app
    dialog = open_editor(shell, capture)
    original = shell.path.stat().st_mtime_ns
    dialog.ok_button.click()
    assert not dialog.isVisible()
    assert shell.operation is None
    assert shell.path.stat().st_mtime_ns == original
    assert not capture.connections
