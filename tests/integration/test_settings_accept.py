from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.storage.settings import load_settings
from tests.integration.peers.controlled_capture import ControlledCapture
from tests.integration.test_settings_apply_contract import (
    app as app,  # noqa: PLC0414 -- shared isolated application fixture
)
from tests.integration.test_settings_apply_contract import open_editor


def test_ok_closes_after_save_before_first_frame(
    app: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = app
    dialog = open_editor(shell, capture)
    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData("path-b"))
    dialog.ok_button.click()
    qtbot.waitUntil(lambda: not dialog.isVisible())
    assert load_settings(shell.path).capture_device == "path-b"
    assert capture.snapshot().phase == "connecting"
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


def test_connection_failure_keeps_saved_choice_and_apply_retry_open(
    app: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = app
    dialog = open_editor(shell, capture)
    capture.accept_connection = False
    dialog.capture_choices.setCurrentIndex(dialog.capture_choices.findData("path-b"))
    dialog.ok_button.click()
    qtbot.waitUntil(lambda: shell.video.apply_state == "failed")
    assert not dialog.isVisible()
    dialog = open_editor(shell, capture)
    assert dialog.capture_choices.currentData() == "path-b"
    capture.accept_connection = True
    dialog.save_button.click()
    qtbot.waitUntil(lambda: len(capture.connections) == 1)
    capture.stream()
    qtbot.waitUntil(lambda: not shell.video.applying)
    assert dialog.isVisible()


def test_ok_without_changes_starts_selected_device_without_resaving(
    app: tuple[ApplicationShell, ControlledCapture],
) -> None:
    shell, capture = app
    dialog = open_editor(shell, capture)
    original = shell.path.stat().st_mtime_ns
    dialog.ok_button.click()
    assert not dialog.isVisible()
    assert shell.operation is None
    assert shell.path.stat().st_mtime_ns == original
    assert len(capture.connections) == 1
