import pytest
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


@pytest.mark.parametrize("observation", [False, True])
def test_cancel_discards_only_edits_since_last_apply(
    app: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot, *, observation: bool
) -> None:
    shell, capture = app
    dialog = open_editor(shell, capture)
    if observation:
        dialog.observation_resolution.setCurrentIndex(dialog.observation_resolution.findData("hd"))
    else:
        dialog.model.setText("applied")
    dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    if observation:
        dialog.observation_resolution.setCurrentIndex(
            dialog.observation_resolution.findData("original")
        )
    else:
        dialog.model.setText("discard")
    dialog.close_button.click()
    assert shell.confirmation is None
    assert not dialog.isVisible()
    shell.open_settings()
    if observation:
        assert load_settings(shell.path).observation_resolution == "hd"
        assert dialog.observation_resolution.currentData() == "hd"
    else:
        assert load_settings(shell.path).model == "applied"
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


def test_ok_normalizes_default_baud_once_then_applies_without_resaving(
    app: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, capture = app
    dialog = open_editor(shell, capture)
    assert load_settings(shell.path).serial_baudrate is None
    assert dialog.values().serial_baudrate == 9600
    assert not shell.dirty
    dialog.ok_button.click()
    qtbot.waitUntil(lambda: not dialog.isVisible())
    assert shell.operation is None
    assert load_settings(shell.path).serial_baudrate == 9600
    assert len(capture.connections) == 1

    dialog = open_editor(shell, capture)
    assert dialog.values() == shell.baseline
    original = shell.path.stat().st_mtime_ns
    dialog.ok_button.click()
    assert not dialog.isVisible()
    assert shell.operation is None
    assert shell.path.stat().st_mtime_ns == original
    assert len(capture.connections) == 1
