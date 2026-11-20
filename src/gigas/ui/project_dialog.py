"""Project editing and deletion confirmations using native, nonblocking dialogs."""

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from gigas.runtime.codex.management import ProjectInfo


class ProjectEditDialog(QDialog):
    """Edit a project snapshot without selecting or starting a conversation.

    Args:
        project: Saved registration; deletion always uses this snapshot.
        parent: Owning main window.
    """

    save_requested = Signal(str, object)
    delete_requested = Signal()

    def __init__(self, project: "ProjectInfo", parent: QWidget | None = None) -> None:
        """Build the name, single-folder picker and separated destructive action."""
        super().__init__(parent)
        self.project = project
        self._pending = False
        self.setWindowTitle("プロジェクトを編集")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(620, 260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(QLabel("表示名", self))
        self.name = QLineEdit(project.name, self)
        self.name.setAccessibleName("表示名")
        self.name.setMinimumHeight(34)
        layout.addWidget(self.name)
        layout.addWidget(QLabel("プロジェクトフォルダー", self))
        row = QHBoxLayout()
        self.folder = QLineEdit(str(project.root), self)
        self.folder.setReadOnly(True)
        self.folder.setCursorPosition(0)
        self.folder.setAccessibleName("プロジェクトフォルダー")
        self.folder.setMinimumHeight(34)
        row.addWidget(self.folder, 1)
        self.choose_button = QPushButton("変更", self)
        self.choose_button.setAutoDefault(False)
        self.choose_button.clicked.connect(self._choose_folder)
        row.addWidget(self.choose_button)
        layout.addLayout(row)
        self.status = QLabel("保存中", self)
        self.status.hide()
        layout.addWidget(self.status)
        layout.addStretch(1)
        actions = QHBoxLayout()
        self.delete_button = QPushButton("プロジェクトを削除", self)
        self.delete_button.setAutoDefault(False)
        self.delete_button.setStyleSheet("color: #e05252;")
        self.delete_button.clicked.connect(self.delete_requested)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.buttons.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("保存")
        self.save_button.setDefault(True)
        self.cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button.setText("キャンセル")
        self.cancel_button.setAutoDefault(False)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        actions.addWidget(self.buttons)
        layout.addLayout(actions)
        self.name.textChanged.connect(self._render)
        self.name.selectAll()
        self.name.setFocus()
        self._render()

    def _choose_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "プロジェクトフォルダー", self.folder.text()
        )
        if selected:
            self.folder.setText(str(Path(selected).resolve()))
            self.folder.setCursorPosition(0)

    def _save(self) -> None:
        if not self._pending and self.name.text().strip():
            self.save_requested.emit(self.name.text().strip(), Path(self.folder.text()))

    def set_pending(self, pending: bool) -> None:
        """Disable edits after submission while leaving the window closable."""
        self._pending = pending
        self._render()

    def _render(self) -> None:
        self.save_button.setEnabled(not self._pending and bool(self.name.text().strip()))
        self.name.setEnabled(not self._pending)
        self.choose_button.setEnabled(not self._pending)
        self.delete_button.setEnabled(not self._pending)
        self.status.setVisible(self._pending)
        self.cancel_button.setText("閉じる" if self._pending else "キャンセル")


def project_delete_confirmation(project: "ProjectInfo", parent: QWidget) -> QMessageBox:
    """Build a confirmation for saved metadata with cancellation as the default."""
    box = QMessageBox(parent)
    box.setWindowTitle("プロジェクトを削除")
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(f"{project.name}を削除しますか？")  # noqa: RUF001 -- Japanese question.
    box.setInformativeText(
        f"{project.root}\n\nプロジェクトの登録を削除します。既存のチャットは未所属に移ります。"
        "コンピューター上のファイルは削除されません。"
    )
    box.setStandardButtons(QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
    delete_button = box.button(QMessageBox.StandardButton.Discard)
    if delete_button is not None:
        delete_button.setText("プロジェクトを削除")
        delete_button.setStyleSheet("color: #e05252;")
    cancel_button = box.button(QMessageBox.StandardButton.Cancel)
    if cancel_button is not None:
        cancel_button.setText("キャンセル")
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    box.setEscapeButton(QMessageBox.StandardButton.Cancel)
    buttons = box.findChild(QDialogButtonBox)
    if buttons is not None:
        buttons.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        buttons.setCenterButtons(True)
    box.setWindowModality(Qt.WindowModality.NonModal)
    return box


def archive_confirmation(
    *, name: str, count: int | None, discard_draft: bool, parent: QWidget
) -> QMessageBox:
    """Confirm a chat or a project's fetched count, including draft disposal when necessary.

    Args:
        name: Name of the chat or project affected.
        count: Number of chats in a project, or None for a single chat.
        discard_draft: Whether confirming will discard the current message draft.
        parent: Main window owning the nonmodal confirmation.

    Returns:
        A dialog that reports Yes only after explicit confirmation.
    """
    box = QMessageBox(parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setWindowTitle("チャットをアーカイブ" if count is None else "チャットを一括アーカイブ")
    target = f"「{name}」" if count is None else f"「{name}」のチャット {count} 件"
    box.setText(f"{target}をアーカイブしますか？")  # noqa: RUF001 -- Japanese question.
    if discard_draft:
        box.setInformativeText("開いているチャットの未送信の原稿を破棄します。")
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
    box.button(QMessageBox.StandardButton.Yes).setText("アーカイブ")
    box.button(QMessageBox.StandardButton.Cancel).setText("キャンセル")
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    box.setEscapeButton(QMessageBox.StandardButton.Cancel)
    box.setWindowModality(Qt.WindowModality.NonModal)
    return box
