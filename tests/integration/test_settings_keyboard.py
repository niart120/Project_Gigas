from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from gigas.storage.settings import Settings
from gigas.ui.settings_dialog import SettingsDialog


@pytest.fixture
def dialog(qtbot: QtBot, tmp_path: Path) -> Iterator[SettingsDialog]:
    parent = QWidget()
    qtbot.addWidget(parent)
    editor = SettingsDialog(tmp_path / "settings.toml", parent)
    editor.set_values(Settings(model="draft", effort="high", serial_port="COM3"))
    editor.show()
    qtbot.waitUntil(editor.isActiveWindow)
    yield editor
    editor.hide()


@pytest.mark.parametrize(
    ("field_name", "key"),
    [
        ("location", Qt.Key.Key_Return),
        ("serial", Qt.Key.Key_Enter),
        ("model", Qt.Key.Key_Return),
        ("effort", Qt.Key.Key_Enter),
    ],
)
def test_enter_in_a_field_never_submits_after_ok_had_focus(
    dialog: SettingsDialog, qtbot: QtBot, field_name: str, key: Qt.Key
) -> None:
    fields = {
        "location": (0, dialog.location),
        "serial": (1, dialog.serial_port),
        "model": (2, dialog.model),
        "effort": (2, dialog.effort),
    }
    submitted: list[str] = []
    dialog.ok_button.clicked.connect(lambda: submitted.append("ok"))
    dialog.save_button.clicked.connect(lambda: submitted.append("apply"))
    dialog.close_requested.connect(lambda: submitted.append("close"))
    dialog.ok_button.setFocus()
    assert dialog.ok_button.hasFocus()
    index, field = fields[field_name]
    dialog.tabs.setCurrentIndex(index)
    field.setFocus()
    assert field.hasFocus()
    before = dialog.values()

    qtbot.keyClick(field, key)

    assert not submitted
    assert dialog.values() == before
    assert dialog.isVisible()


@pytest.mark.parametrize(
    ("command", "key"),
    [("ok", Qt.Key.Key_Return), ("cancel", Qt.Key.Key_Enter), ("apply", Qt.Key.Key_Space)],
)
def test_focused_footer_button_runs_only_its_own_explicit_command(
    dialog: SettingsDialog, qtbot: QtBot, command: str, key: Qt.Key
) -> None:
    buttons = {"ok": dialog.ok_button, "cancel": dialog.close_button, "apply": dialog.save_button}
    clicked: list[str] = []
    for name, button in buttons.items():
        button.clicked.connect(lambda _checked=False, selected=name: clicked.append(selected))
    button = buttons[command]
    button.setFocus()
    assert button.hasFocus()

    qtbot.keyClick(button, key)

    assert clicked == [command]


@pytest.mark.parametrize(
    ("field_name", "key"),
    [("resolution", Qt.Key.Key_Return), ("preview_mode", Qt.Key.Key_Enter)],
)
def test_enter_accepts_combo_popup_selection_without_submitting_settings(
    dialog: SettingsDialog, qtbot: QtBot, field_name: str, key: Qt.Key
) -> None:
    fields = {
        "resolution": (1, dialog.capture_resolution, "auto"),
        "preview_mode": (0, dialog.preview_mode, False),
    }
    submitted: list[str] = []
    dialog.ok_button.clicked.connect(lambda: submitted.append("ok"))
    dialog.save_button.clicked.connect(lambda: submitted.append("apply"))
    dialog.close_requested.connect(lambda: submitted.append("close"))
    dialog.ok_button.setFocus()
    index, field, target = fields[field_name]
    dialog.tabs.setCurrentIndex(index)
    field.setFocus()
    field.showPopup()
    target_index = field.findData(target)
    assert target_index >= 0
    field.view().setCurrentIndex(field.model().index(target_index, 0))

    qtbot.keyClick(field.view(), key)

    assert field.currentData() == target
    assert not field.view().isVisible()
    assert not submitted
    assert dialog.isVisible()
