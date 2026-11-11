"""Explicit, frozen capture diagnostics separate from failure notifications."""

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout, QWidget


class VideoInfoDialog(QDialog):
    """Show one read-only diagnostic snapshot in a modeless owned window.

    Args:
        parent: Widget owning this dialog and its lifetime.
    """

    def __init__(self, parent: QWidget) -> None:
        """Prepare copyable plain text without starting or controlling capture."""
        super().__init__(parent)
        self.setWindowTitle("映像情報")
        self.resize(620, 440)
        layout = QVBoxLayout(self)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 1)
        buttons = QDialogButtonBox(self)
        buttons.addButton("閉じる", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def display(self, details: str) -> None:
        """Replace diagnostics only for an explicit open or reopen action.

        Args:
            details: Plain-text snapshot retained unchanged until the next request.
        """
        self.details.setPlainText(details)
