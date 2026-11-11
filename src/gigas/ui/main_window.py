"""Main application window."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gigas.ui.connection_panel import ConnectionPanel
from gigas.ui.video_panel import VideoPanel


class MainWindow(QMainWindow):
    """Present the settings action and application status."""

    settings_requested = Signal()
    close_requested = Signal()
    cancel_close_requested = Signal()

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
        self.splitter = QSplitter(Qt.Orientation.Horizontal, central)
        self.splitter.setChildrenCollapsible(False)
        self.video = VideoPanel(self.splitter)
        conversation = QWidget(self.splitter)
        conversation_layout = QVBoxLayout(conversation)
        conversation_layout.addWidget(QLabel("Codex", conversation))
        self.connection = ConnectionPanel(conversation)
        conversation_layout.addWidget(self.connection, 1)
        self.splitter.addWidget(self.video)
        self.splitter.addWidget(conversation)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)
        self.status = QLabel("変更なし", self)
        self.statusBar().addWidget(self.status, 1)
        self.cancel_close_button = QPushButton("終了取消し", self)
        self.cancel_close_button.clicked.connect(self.cancel_close_requested)
        self.cancel_close_button.hide()
        self.statusBar().addPermanentWidget(self.cancel_close_button)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 -- Qt override
        """Route window closure through application lifetime management."""
        if self.allow_close:
            event.accept()
        else:
            event.ignore()
            self.close_requested.emit()
