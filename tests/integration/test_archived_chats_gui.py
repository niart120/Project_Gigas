from pathlib import Path

from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from gigas.runtime.codex.management_models import ThreadInfo, ThreadPage
from gigas.storage.settings import load_settings, save_settings
from gigas.ui.settings_dialog import SettingsDialog


def test_archive_tab_lists_on_connection_and_restores_only_the_selected_id(
    qtbot: QtBot, tmp_path: Path
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(tmp_path / "settings.toml", parent)
    dialog.show()
    requested: list[str | None] = []
    dialog.archived_requested.connect(requested.append)
    dialog.tabs.setCurrentIndex(dialog.archive_tab)
    assert requested == []
    tab = dialog.archive
    assert not tab.restore.isEnabled()
    tab.days.setCurrentIndex(tab.days.findData(14))
    save_settings(tmp_path / "settings.toml", dialog.values())
    assert load_settings(tmp_path / "settings.toml").auto_archive_days == 14

    dialog.set_archive_availability(True)
    assert requested == [None]
    thread = ThreadInfo("first", "会話", "", tmp_path, None, 1, 2, None, None, {})
    dialog.set_archive_availability(True, busy=True)
    dialog.set_archived_page(ThreadPage((thread,), "opaque", None))
    dialog.set_archive_availability(True)
    assert requested == [None]
    first_row = tab.rows.topLevelItem(0)
    assert first_row is not None
    assert first_row.text(1) == "未所属"
    tab.rows.setCurrentItem(first_row)
    dialog.set_archive_availability(True, can_restore=False)
    assert not tab.restore.isEnabled()
    tab.more.click()
    assert requested == [None, "opaque"]
    dialog.set_archived_page(ThreadPage((thread,), None, None), append=True)
    assert tab.rows.topLevelItemCount() == 1
    assert tab.more.isHidden()
    dialog.set_archive_availability(True)
    with qtbot.waitSignal(dialog.restore_requested) as signal:
        tab.restore.click()
    assert signal.args == ["first"]

    dialog.set_archive_availability(False)
    assert tab.rows.topLevelItemCount() == 0
    assert not tab.restore.isEnabled()
    dialog.set_archive_availability(True)
    assert requested == [None, "opaque", None]
