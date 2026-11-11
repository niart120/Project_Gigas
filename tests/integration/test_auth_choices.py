from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.storage.settings import Settings, load_settings, save_settings
from gigas.ui.settings_dialog import SettingsDialog


def test_new_settings_offer_only_supported_authentication(qtbot: QtBot, tmp_path: Path) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)
    dialog.set_values(Settings())
    assert dialog.auth_method.currentData() == "chatgpt"
    assert dialog.auth_method.findData("api_key") == -1


def test_legacy_authentication_is_retained_but_cannot_be_newly_selected(
    qtbot: QtBot, tmp_path: Path
) -> None:
    path = tmp_path / "settings.toml"
    save_settings(path, Settings(auth_method="api_key"))
    shell = ApplicationShell(path)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        shell.open_settings()
        dialog = shell.dialog
        assert dialog is not None
        combo = dialog.auth_method
        legacy = combo.findData("api_key")
        assert legacy >= 0
        assert combo.currentIndex() == legacy
        model = combo.model()
        assert not model.flags(model.index(legacy, 0)) & Qt.ItemFlag.ItemIsEnabled
        dialog.model.setText("edited-model")
        dialog.save_button.click()
        qtbot.waitUntil(lambda: shell.operation is None)
        assert load_settings(path) == Settings(model="edited-model", auth_method="api_key")

        combo.setCurrentIndex(combo.findData("chatgpt"))
        qtbot.keyClick(combo, Qt.Key.Key_Down)
        assert combo.currentData() == "chatgpt"
        dialog.save_button.click()
        qtbot.waitUntil(lambda: shell.operation is None)
        assert load_settings(path).auth_method == "chatgpt"
        assert combo.findData("api_key") == -1
    finally:
        if shell.dialog is not None:
            shell.dialog.set_values(shell.baseline)
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())
