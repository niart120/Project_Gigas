"""Nonblocking confirmation of unsaved settings."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget


def changes_confirmation(parent: QWidget, *, allow_save: bool) -> QMessageBox:
    """Build a confirmation using short labels, without running a nested loop.

    Args:
        parent: Window that owns the confirmation.
        allow_save: Whether the current settings can be saved.

    Returns:
        A message box whose finished signal supplies the user's choice.
    """
    box = QMessageBox(parent)
    box.setWindowTitle("設定")
    box.setText("未保存の変更")
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setIcon(QMessageBox.Icon.Question)
    buttons = QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel
    if allow_save:
        buttons |= QMessageBox.StandardButton.Save
    box.setStandardButtons(buttons)
    for role, label in (
        (QMessageBox.StandardButton.Save, "保存"),
        (QMessageBox.StandardButton.Discard, "破棄"),
        (QMessageBox.StandardButton.Cancel, "取消し"),
    ):
        button = box.button(role)
        if button is not None:
            button.setText(label)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    box.setEscapeButton(QMessageBox.StandardButton.Cancel)
    box.setWindowModality(Qt.WindowModality.WindowModal)
    return box


def draft_confirmation(parent: QWidget) -> QMessageBox:
    """Ask before discarding the current draft, without starting a nested loop."""
    box = QMessageBox(parent)
    box.setWindowTitle("会話の切替")
    box.setText("入力中の下書きを破棄しますか？")  # noqa: RUF001 -- agreed Japanese question.
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setIcon(QMessageBox.Icon.Question)
    box.setStandardButtons(QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
    button = box.button(QMessageBox.StandardButton.Discard)
    if button is not None:
        button.setText("破棄して切替")
    cancel = box.button(QMessageBox.StandardButton.Cancel)
    if cancel is not None:
        cancel.setText("キャンセル")
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    box.setEscapeButton(QMessageBox.StandardButton.Cancel)
    box.setWindowModality(Qt.WindowModality.WindowModal)
    return box
