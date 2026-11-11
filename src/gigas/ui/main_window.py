"""Main application window."""

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QActionGroup, QCloseEvent
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
from gigas.ui.geometry import fit_to_screen
from gigas.ui.layout import WINDOW_SIZE_PRESETS, fit_window_size_preset
from gigas.ui.video_panel import VideoPanel

if TYPE_CHECKING:
    from PySide6.QtGui import QAction


class MainWindow(QMainWindow):
    """Present the settings action and application status."""

    settings_requested = Signal()
    close_requested = Signal()
    cancel_close_requested = Signal()
    window_size_requested = Signal(str)

    def __init__(self) -> None:
        """Create the main window without starting services."""
        super().__init__()
        self.allow_close = False
        self.setWindowTitle("GIGAS")
        self.resize(960, 640)
        self.current_window_size_preset: str | None = None
        self.window_size_actions: dict[str, QAction] = {}
        self.window_size_group = QActionGroup(self)
        self.window_size_group.setExclusive(True)
        view_menu = self.menuBar().addMenu("表示")
        self.window_size_menu = view_menu.addMenu("ウィンドウサイズ")
        for preset in WINDOW_SIZE_PRESETS:
            action = self.window_size_menu.addAction(preset.label)
            action.setCheckable(True)
            action.setData(preset.key)
            action.triggered.connect(
                lambda _checked=False, key=preset.key: self.window_size_requested.emit(key)
            )
            self.window_size_group.addAction(action)
            self.window_size_actions[preset.key] = action
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

    def set_window_size_state(self, key: str, *, enabled: bool) -> None:
        """Reflect the saved selection and whether a new save can be requested."""
        self.window_size_actions[key].setChecked(True)
        self.window_size_group.setEnabled(enabled)

    def apply_window_size_preset(self, key: str) -> None:
        """Fit the requested window and preview to the available screen.

        Args:
            key: A validated saved preset. Controls may reduce the actual preview
                below its scaled target, preserving the source image's aspect ratio.
        """
        screen = self.screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        extra_width = self.frameGeometry().width() - self.width()
        extra_height = self.frameGeometry().height() - self.height()
        target = fit_window_size_preset(
            key, available.width() - extra_width, available.height() - extra_height
        )
        self.current_window_size_preset = key
        self.resize(target.window_width, target.window_height)
        self.video.view.set_preview_size(target.preview_width, target.preview_height)
        self.splitter.setSizes([target.preview_width, target.window_width - target.preview_width])
        fit_to_screen(self)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 -- Qt override
        """Route window closure through application lifetime management."""
        if self.allow_close:
            event.accept()
        else:
            event.ignore()
            self.close_requested.emit()
