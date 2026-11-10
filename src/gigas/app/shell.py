"""Coordinate the application views and settings operations."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject

from gigas.ui.main_window import MainWindow
from gigas.ui.settings_dialog import SettingsDialog

if TYPE_CHECKING:
    from pathlib import Path


class ApplicationShell(QObject):
    """Own the main window and the application's settings path.

    Args:
        path: Explicit settings path; tests must supply an isolated location.
    """

    def __init__(self, path: "Path") -> None:
        """Construct views without displaying them."""
        super().__init__()
        self.path = path
        self.window = MainWindow()
        self.dialog: SettingsDialog | None = None
        self.window.settings_requested.connect(self.open_settings)

    def start(self) -> None:
        """Display the main window."""
        self.window.show()

    def open_settings(self) -> None:
        """Show the single settings editor without discarding its input."""
        if self.dialog is None:
            self.dialog = SettingsDialog(self.path, self.window)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
