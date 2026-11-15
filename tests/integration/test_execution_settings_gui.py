from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.storage.settings import Settings, load_settings
from gigas.ui.settings_dialog import SettingsDialog
from tests.integration.test_app_settings import (
    shell as shell,  # noqa: PLC0414 -- shared isolated application fixture
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "original",
    [
        Settings(serial_baudrate=9600),
        Settings(
            serial_baudrate=9600,
            web_search_enabled=False,
            memory_enabled=False,
            approvals_reviewer="user",
        ),
    ],
)
def test_execution_editor_loads_edits_and_locks_the_complete_snapshot(
    qtbot: QtBot, tmp_path: "Path", original: Settings
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    changes: list[bool] = []
    dialog.changed.connect(lambda: changes.append(True))

    dialog.set_values(original)

    assert changes == []
    assert dialog.web_search_enabled.isChecked() is original.web_search_enabled
    assert dialog.memory_enabled.isChecked() is original.memory_enabled
    assert dialog.approvals_reviewer.currentData() == original.approvals_reviewer
    assert dialog.values() == original

    reviewer = "user" if original.approvals_reviewer == "auto_review" else "auto_review"
    dialog.web_search_enabled.click()
    dialog.memory_enabled.click()
    dialog.approvals_reviewer.setCurrentIndex(dialog.approvals_reviewer.findData(reviewer))
    edited = replace(
        original,
        web_search_enabled=not original.web_search_enabled,
        memory_enabled=not original.memory_enabled,
        approvals_reviewer=reviewer,
    )
    assert dialog.values() == edited
    assert len(changes) == 3

    dialog.set_editable(False)
    assert not dialog.web_search_enabled.isEnabled()
    assert not dialog.memory_enabled.isEnabled()
    assert not dialog.approvals_reviewer.isEnabled()
    dialog.web_search_enabled.click()
    dialog.memory_enabled.click()
    assert dialog.values() == edited
    dialog.set_editable(True)
    assert dialog.web_search_enabled.isEnabled()
    assert dialog.memory_enabled.isEnabled()
    assert dialog.approvals_reviewer.isEnabled()


@pytest.mark.parametrize("action", ["apply", "ok"])
def test_execution_controls_save_and_cancel_through_existing_dialog_actions(
    shell: ApplicationShell, qtbot: QtBot, action: str
) -> None:
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    dialog.tabs.setCurrentIndex(dialog.codex_tab)
    dialog.web_search_enabled.click()
    dialog.memory_enabled.click()
    dialog.approvals_reviewer.setCurrentIndex(dialog.approvals_reviewer.findData("user"))
    assert shell.dirty
    edited = dialog.values()
    assert not edited.web_search_enabled
    assert not edited.memory_enabled
    assert edited.approvals_reviewer == "user"

    (dialog.save_button if action == "apply" else dialog.ok_button).click()
    qtbot.waitUntil(lambda: shell.operation is None)

    assert load_settings(shell.path) == edited
    assert shell.baseline == edited
    assert not shell.dirty
    assert dialog.isVisible() is (action == "apply")
    shell.open_settings()
    assert not dialog.web_search_enabled.isChecked()
    assert not dialog.memory_enabled.isChecked()
    assert dialog.approvals_reviewer.currentData() == "user"

    dialog.web_search_enabled.click()
    dialog.memory_enabled.click()
    dialog.approvals_reviewer.setCurrentIndex(dialog.approvals_reviewer.findData("auto_review"))
    assert shell.dirty
    dialog.close_button.click()
    assert not dialog.isVisible()
    assert load_settings(shell.path) == edited
    shell.open_settings()
    assert dialog.values() == edited
    assert not shell.dirty
