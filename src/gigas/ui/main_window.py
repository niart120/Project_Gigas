"""Main application window."""

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget


class MainWindow(QMainWindow):
    """Present the settings action and application status."""

    settings_requested = Signal()
    close_requested = Signal()

    def __init__(self) -> None:
        """Create the main window without starting services."""
        super().__init__()
        self.allow_close = False
        self.setWindowTitle("GIGAS")
        self.resize(960, 640)
        central = QWidget(self)
        layout = QVBoxLayout(central)
        actions = QHBoxLayout()
        actions.addStretch()
        self.settings_button = QPushButton("設定…", central)
        self.settings_button.clicked.connect(self.settings_requested)
        actions.addWidget(self.settings_button)
        layout.addLayout(actions)
        layout.addStretch()
        self.setCentralWidget(central)
        self.status = QLabel("変更なし", self)
        self.statusBar().addWidget(self.status, 1)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 -- Qt override
        """Route window closure through application lifetime management."""
        if self.allow_close:
            event.accept()
        else:
            event.ignore()
            self.close_requested.emit()
