from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell


@pytest.fixture
def shell(qtbot: QtBot, tmp_path: Path) -> Iterator[ApplicationShell]:
    application = ApplicationShell(tmp_path / "settings.toml")
    qtbot.addWidget(application.window)
    application.start()
    yield application
    if application.dialog is not None:
        application.dialog.close()
    application.window.close()


def test_settings_reopens_same_dialog_with_edits(shell: ApplicationShell, qtbot: QtBot) -> None:
    shell.window.settings_button.click()
    dialog = shell.dialog
    assert dialog is not None
    qtbot.waitUntil(dialog.isVisible)
    dialog.model.setPlainText("edited")

    shell.window.settings_button.click()

    assert shell.dialog is dialog
    assert dialog.model.toPlainText() == "edited"
    assert not dialog.isModal()
