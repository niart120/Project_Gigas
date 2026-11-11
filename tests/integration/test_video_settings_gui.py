from pathlib import Path

from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from gigas.game_io.models import DeviceInfo
from gigas.storage.settings import Settings
from gigas.ui.settings_dialog import SettingsDialog


def test_device_selection_and_video_mode_preserve_unsaved_values(
    qtbot: QtBot, tmp_path: Path
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)
    baseline = Settings(
        capture_device="path-b",
        capture_width=1280,
        capture_height=720,
        capture_fps=30,
        preview_fps=15,
    )
    dialog.set_values(baseline)
    assert dialog.values() == baseline
    dialog.model.setPlainText("unsaved")
    devices = (DeviceInfo("Same camera", "path-a"), DeviceInfo("Same camera", "path-b"))

    dialog.set_capture_devices(devices)

    assert dialog.capture_choices.currentData() == "path-b"
    assert dialog.capture_choices.itemText(1) != dialog.capture_choices.itemText(2)
    assert dialog.values().model == "unsaved"
    dialog.capture_choices.setCurrentIndex(1)
    dialog.capture_width.setValue(640)
    dialog.capture_height.setValue(480)
    dialog.capture_fps.setValue(25)
    dialog.preview_fps.setValue(10)
    assert dialog.values() == Settings(
        capture_device="path-a",
        model="unsaved",
        capture_width=640,
        capture_height=480,
        capture_fps=25,
        preview_fps=10,
    )
    assert not (tmp_path / "settings.toml").exists()
    dialog.set_capture_devices((devices[1],))
    assert dialog.values().capture_device == "path-a"
    dialog.capture_device.setPlainText("path-b")
    assert dialog.capture_choices.currentData() == "path-b"
    dialog.capture_device.setPlainText("another-path")
    assert dialog.capture_choices.currentIndex() == 0
    dialog.capture_choices.setCurrentIndex(1)
    assert dialog.values().capture_device == "path-b"


def test_video_settings_are_read_only_during_save(qtbot: QtBot, tmp_path: Path) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)

    dialog.set_editable(False)

    for widget in (
        dialog.capture_choices,
        dialog.capture_width,
        dialog.capture_height,
        dialog.capture_fps,
        dialog.preview_fps,
        dialog.capture_refresh_button,
    ):
        assert not widget.isEnabled()
    assert dialog.capture_device.isReadOnly()


def test_unselecting_camera_clears_saved_selection(qtbot: QtBot, tmp_path: Path) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)
    dialog.set_capture_devices((DeviceInfo("Camera", "path-a"),))
    dialog.capture_choices.setCurrentIndex(1)

    dialog.capture_choices.setCurrentIndex(0)

    assert dialog.values().capture_device == ""
