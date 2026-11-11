from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QDialogButtonBox, QLineEdit, QScrollArea, QWidget
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
    values = Settings(
        "old\r\npath",
        " COM3\r\n ",
        " model\nvalue ",
        " high\r\n ",
        auth_method="api_key",
        capture_resolution="auto",
        preview_letterbox=False,
    )
    dialog.set_values(values)
    dialog.set_capture_devices((DeviceInfo("Other", "path-other"),))

    assert dialog.values() == values
    assert (
        not dialog.auth_method.model().flags(
            dialog.auth_method.model().index(dialog.auth_method.currentIndex(), 0)
        )
        & Qt.ItemFlag.ItemIsEnabled
    )
    assert dialog.capture_choices.currentData() == values.capture_device
    assert values.capture_device not in dialog.capture_choices.currentText()
    for editor in (dialog.serial_port, dialog.model, dialog.effort):
        assert isinstance(editor, QLineEdit)
    dialog.model.setText("changed")
    assert dialog.values().capture_device == values.capture_device
    assert dialog.values().serial_port == values.serial_port
    assert dialog.values().model == "changed"
    dialog.capture_choices.setCurrentIndex(0)
    assert dialog.capture_target == ""
    assert dialog.values().capture_device == values.capture_device


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
        dialog.capture_resolution,
        dialog.preview_mode,
    ):
        assert not widget.isEnabled()
    assert dialog.model.isReadOnly()


def test_unselecting_camera_discards_unsaved_device_choice(qtbot: QtBot, tmp_path: Path) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)
    dialog.set_capture_devices((DeviceInfo("Camera", "path-a"),))
    dialog.capture_choices.setCurrentIndex(1)

    dialog.capture_choices.setCurrentIndex(0)

    assert dialog.values().capture_device == ""
    assert dialog.capture_target == ""


def test_settings_fields_are_grouped_by_display_io_and_codex(qtbot: QtBot, tmp_path: Path) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)
    groups = (
        (dialog.location, dialog.window_size_preset, dialog.preview_mode, dialog.preview_fps),
        (
            dialog.capture_choices,
            dialog.capture_refresh_button,
            dialog.capture_fps,
            dialog.capture_resolution,
            dialog.serial_port,
        ),
        (dialog.model, dialog.effort, dialog.auth_method),
    )

    for index, fields in enumerate(groups):
        tab = dialog.tabs.widget(index)
        assert isinstance(tab, QScrollArea)
        page = tab.widget()
        assert page is not None
        assert all(page.isAncestorOf(field) for field in fields)
    assert dialog.location.isReadOnly()
    assert dialog.location.text() == str(tmp_path / "settings.toml")
    buttons = dialog.findChild(QDialogButtonBox)
    assert buttons is not None
    assert buttons.standardButton(dialog.ok_button) == QDialogButtonBox.StandardButton.Ok
    assert buttons.standardButton(dialog.save_button) == QDialogButtonBox.StandardButton.Apply
    assert buttons.standardButton(dialog.close_button) == QDialogButtonBox.StandardButton.Cancel


@pytest.mark.parametrize("resolution", ["auto", "hd", "full_hd", "wqhd", "four_k"])
@pytest.mark.parametrize("letterbox", [True, False])
def test_image_settings_round_trip_without_touching_capture_target(
    qtbot: QtBot, tmp_path: Path, resolution: str, letterbox: bool
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)
    values = Settings(
        capture_device="saved-b",
        capture_resolution=resolution,
        preview_letterbox=letterbox,
    )
    dialog.set_values(values)

    assert dialog.values() == values
    assert dialog.capture_resolution.currentData() == resolution
    assert dialog.preview_mode.currentData() is letterbox
    assert dialog.capture_target == "saved-b"


def test_display_edits_preserve_saved_camera_while_another_camera_is_active(
    qtbot: QtBot, tmp_path: Path
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)
    devices = (DeviceInfo("Active A", "path-a"), DeviceInfo("Saved B", "path-b"))
    dialog.set_values(Settings(capture_device="path-b"))
    dialog.set_capture_devices(devices)
    dialog.set_capture_target("path-a")
    dialog.preview_mode.setCurrentIndex(dialog.preview_mode.findData(False))
    dialog.preview_fps.setCurrentIndex(dialog.preview_fps.findData(15))

    assert dialog.capture_target == "path-a"
    assert dialog.values() == Settings(
        capture_device="path-b", preview_letterbox=False, preview_fps=15
    )
    dialog.capture_choices.activated.emit(dialog.capture_choices.currentIndex())
    assert dialog.values().capture_device == "path-a"
    dialog.capture_choices.setCurrentIndex(0)
    assert dialog.capture_target == ""
    assert dialog.values().capture_device == "path-b"


def test_refresh_and_save_keep_pending_target_separate_from_saved_device(
    qtbot: QtBot, tmp_path: Path
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)
    dialog.set_values(Settings(capture_device="path-b"))
    devices = (DeviceInfo("A", "path-a"), DeviceInfo("B", "path-b"))
    dialog.set_capture_devices(devices)
    dialog.capture_choices.setCurrentIndex(1)
    dialog.model.setText("draft")
    dialog.set_capture_devices((devices[1],))
    assert dialog.capture_target == "path-a"
    assert dialog.values().capture_device == "path-a"
    assert dialog.values().model == "draft"

    dialog.set_capture_target("")
    saved = Settings(capture_device="path-b", model="saved")
    changed = QSignalSpy(dialog.changed)
    dialog.set_values(saved, preserve_target=True)
    assert changed.count() == 0
    assert dialog.capture_target == ""
    assert dialog.capture_choices.currentData() == ""
    assert dialog.values() == saved

    dialog.set_values(saved)
    assert dialog.capture_target == "path-b"
    assert dialog.values() == saved


def test_cancel_window_close_and_escape_wait_until_closing_is_allowed(
    qtbot: QtBot, tmp_path: Path
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)
    dialog.show()
    requested = QSignalSpy(dialog.close_requested)
    dialog.set_editable(False)

    assert not dialog.closing_allowed
    dialog.close_button.click()
    dialog.close()
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    assert requested.count() == 0
    assert dialog.isVisible()

    dialog.set_closing_allowed(True)
    dialog.close_button.click()
    dialog.close()
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    assert requested.count() == 3
    assert dialog.isVisible()


def test_ok_does_not_accept_before_the_owner_confirms_apply(qtbot: QtBot, tmp_path: Path) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)
    dialog.show()
    accepted = QSignalSpy(dialog.accepted)

    dialog.ok_button.click()

    assert accepted.count() == 0
    assert dialog.isVisible()


@pytest.mark.parametrize("checked", [False, True])
def test_disconnected_form_keeps_absent_saved_camera_selectable(
    qtbot: QtBot, tmp_path: Path, checked: bool
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)
    dialog.set_values(Settings(capture_device="saved-a"))
    dialog.set_capture_target("")

    dialog.set_capture_devices((), checked=checked)

    saved = dialog.capture_choices.findData("saved-a")
    assert saved >= 1
    assert dialog.capture_choices.currentData() == ""
    assert dialog.capture_target == ""
    assert dialog.values().capture_device == "saved-a"
    dialog.capture_choices.setCurrentIndex(saved)
    assert dialog.capture_target == "saved-a"
    assert dialog.values().capture_device == "saved-a"


def test_absent_saved_and_selected_cameras_remain_distinct_choices(
    qtbot: QtBot, tmp_path: Path
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    qtbot.addWidget(dialog)
    dialog.set_values(Settings(capture_device="saved-a"))
    dialog.set_capture_target("active-b")

    dialog.set_capture_devices(())

    saved = dialog.capture_choices.findData("saved-a")
    active = dialog.capture_choices.findData("active-b")
    assert saved >= 1
    assert active >= 1
    assert saved != active
    assert dialog.capture_choices.itemText(saved) != dialog.capture_choices.itemText(active)
    assert dialog.capture_choices.currentData() == "active-b"
    assert dialog.values().capture_device == "saved-a"
    dialog.capture_choices.setCurrentIndex(saved)
    assert dialog.capture_target == "saved-a"
    dialog.set_capture_devices(())
    assert dialog.capture_choices.count() == 2
    assert dialog.capture_choices.currentData() == "saved-a"
