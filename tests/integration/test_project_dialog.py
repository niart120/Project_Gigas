from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from gigas.runtime.codex.management import ProjectInfo
from gigas.ui.project_dialog import ProjectEditDialog, project_delete_confirmation


def test_missing_folder_editor_submits_once_and_remains_closable(
    qtbot: QtBot, tmp_path: Path
) -> None:
    project = ProjectInfo("project-1", "Original", tmp_path / "missing")
    dialog = ProjectEditDialog(project)
    qtbot.addWidget(dialog)
    submitted = []

    def receive(name: str, root: object) -> None:
        submitted.append((name, root))
        dialog.set_pending(True)

    dialog.save_requested.connect(receive)
    dialog.show()
    dialog.name.setText(" ")
    assert not dialog.save_button.isEnabled()
    dialog.name.setText(" New name ")
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert submitted == [("New name", project.root)]
    assert not dialog.delete_button.isEnabled()
    assert dialog.cancel_button.isEnabled()
    qtbot.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
    assert not dialog.isVisible()
    assert dialog.project == project
    assert not project.root.exists()


def test_delete_uses_saved_snapshot_and_enter_defaults_to_cancellation(
    qtbot: QtBot, tmp_path: Path
) -> None:
    project = ProjectInfo("project-1", "<Original>", tmp_path / "missing")
    parent = QWidget()
    qtbot.addWidget(parent)
    editor = ProjectEditDialog(project, parent)
    editor.name.setText("Unsaved name")
    box = project_delete_confirmation(editor.project, parent)
    qtbot.addWidget(box)
    assert box.textFormat() == Qt.TextFormat.PlainText
    assert "Unsaved name" not in box.text()
    assert project.name in box.text()
    assert str(project.root) in box.informativeText()
    assert box.defaultButton() is box.button(QMessageBox.StandardButton.Cancel)
    result = []
    box.finished.connect(result.append)
    box.show()
    qtbot.keyClick(box, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: bool(result))
    assert result == [QMessageBox.StandardButton.Cancel]
