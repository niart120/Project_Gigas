from pathlib import Path

from PySide6.QtWidgets import QLineEdit, QWidget
from pytestqt.qtbot import QtBot

from gigas.game_io.capture.models import DeviceInfo
from gigas.storage.settings import Settings
from gigas.ui.settings_dialog import SettingsDialog


def test_device_selection_and_rates_preserve_unsaved_values(qtbot: QtBot, tmp_path: Path) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)
    baseline = Settings(capture_device="path-b", capture_fps=25, preview_fps=10)
    dialog.set_values(baseline)
    assert dialog.values() == baseline
    assert dialog.tabs.count() == 3
    assert not dialog.capture_choices.isEditable()
    dialog.model.setText("unsaved")
    devices = (DeviceInfo("Same camera", "path-a"), DeviceInfo("Same camera", "path-b"))

    dialog.set_capture_devices(devices)

    assert dialog.capture_choices.currentData() == "path-b"
    assert dialog.capture_choices.itemText(1) != dialog.capture_choices.itemText(2)
    assert all(
        device.path not in dialog.capture_choices.itemText(index)
        for device in devices
        for index in range(dialog.capture_choices.count())
    )
    assert dialog.values().model == "unsaved"
    dialog.capture_choices.setCurrentIndex(1)
    dialog.capture_fps.setCurrentIndex(dialog.capture_fps.findData(30))
    dialog.preview_fps.setCurrentIndex(dialog.preview_fps.findData(15))
    assert dialog.values() == Settings(
        capture_device="path-a", model="unsaved", capture_fps=30, preview_fps=15
    )
    assert not (tmp_path / "settings.toml").exists()
    dialog.set_capture_devices((devices[1],))
    assert dialog.capture_choices.currentData() == "path-a"
    assert dialog.values().capture_device == "path-a"
    dialog.set_capture_devices(devices)
    assert dialog.capture_choices.currentData() == "path-a"
    dialog.capture_choices.setCurrentIndex(2)
    assert dialog.values().capture_device == "path-b"


def test_missing_device_and_legacy_text_are_preserved_until_explicit_edit(
    qtbot: QtBot, tmp_path: Path
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)
    values = Settings("old\r\npath", " COM3\r\n ", " model\nvalue ", " high\r\n ")
    dialog.set_values(values)
    dialog.set_capture_devices((DeviceInfo("Other", "path-other"),))

    assert dialog.values() == values
    assert dialog.capture_choices.currentData() == values.capture_device
    assert values.capture_device not in dialog.capture_choices.currentText()
    for editor in (dialog.serial_port, dialog.model, dialog.effort):
        assert isinstance(editor, QLineEdit)
    dialog.model.setText("changed")
    assert dialog.values().capture_device == values.capture_device
    assert dialog.values().serial_port == values.serial_port
    assert dialog.values().model == "changed"
    dialog.capture_choices.setCurrentIndex(0)
    assert dialog.values().capture_device == ""


def test_video_settings_are_read_only_during_save(qtbot: QtBot, tmp_path: Path) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)
    dialog.set_values(Settings(capture_fps=25, preview_fps=10))
    assert dialog.capture_fps.currentData() == 25
    assert dialog.preview_fps.currentData() == 10

    dialog.set_editable(False)

    for widget in (
        dialog.capture_choices,
        dialog.capture_fps,
        dialog.preview_fps,
        dialog.capture_refresh_button,
    ):
        assert not widget.isEnabled()
    assert dialog.model.isReadOnly()


def test_unselecting_camera_clears_saved_selection(qtbot: QtBot, tmp_path: Path) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)
    dialog.set_capture_devices((DeviceInfo("Camera", "path-a"),))
    dialog.capture_choices.setCurrentIndex(1)

    dialog.capture_choices.setCurrentIndex(0)

    assert dialog.values().capture_device == ""
