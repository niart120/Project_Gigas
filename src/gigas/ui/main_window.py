"""Main application window."""

from html import escape
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gigas.ui.capture_choices import capture_device_choices
from gigas.ui.connection_panel import ConnectionPanel
from gigas.ui.elided_label import ElidedLabel
from gigas.ui.geometry import fit_to_screen
from gigas.ui.layout import WINDOW_SIZE_PRESETS, fit_window_size_preset
from gigas.ui.notifications import NotificationCenter
from gigas.ui.video_panel import VideoPanel

if TYPE_CHECKING:
    from gigas.game_io.capture.models import DeviceInfo


class MainWindow(QMainWindow):
    """Present shared menus, video, conversation, and current status."""

    settings_requested = Signal()
    close_requested = Signal()
    cancel_close_requested = Signal()
    window_size_requested = Signal(str)
    video_menu_opened = Signal()
    video_menu_closed = Signal()
    capture_device_requested = Signal(str)

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
        self.file_menu = self.menuBar().addMenu("ファイル")
        self.settings_action = self.file_menu.addAction("設定…")
        self.settings_action.triggered.connect(self.settings_requested)
        self.exit_action = self.file_menu.addAction("終了")
        self.exit_action.triggered.connect(self.close)
        self.connection_menu = self.menuBar().addMenu("接続")
        self.video_menu = self.connection_menu.addMenu("映像")
        self.video_menu.setToolTipsVisible(True)
        self.video_menu.aboutToShow.connect(self.video_menu_opened)
        self.video_menu.aboutToHide.connect(self.video_menu_closed)
        self.codex_menu = self.connection_menu.addMenu("Codex")
        self.view_menu = self.menuBar().addMenu("表示")
        for preset in WINDOW_SIZE_PRESETS:
            action = self.view_menu.addAction(preset.label)
            action.setCheckable(True)
            action.setData(preset.key)
            action.triggered.connect(
                lambda _checked=False, key=preset.key: self.window_size_requested.emit(key)
            )
            self.window_size_group.addAction(action)
            self.window_size_actions[preset.key] = action
        central = QWidget(self)
        layout = QVBoxLayout(central)
        self.notifications = NotificationCenter(self)
        layout.addWidget(self.notifications.banner)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, central)
        self.splitter.setChildrenCollapsible(False)
        self.video = VideoPanel(self.splitter)
        conversation = QWidget(self.splitter)
        conversation_layout = QVBoxLayout(conversation)
        conversation_layout.addWidget(QLabel("Codex", conversation))
        self.connection = ConnectionPanel(conversation)
        self.connection.failed.connect(self.notifications.publish)
        for action in (
            self.connection.connect_action,
            self.connection.disconnect_action,
            self.connection.login_action,
        ):
            self.codex_menu.addAction(action)
        self.video_menu.addAction(self.video.connect_action)
        self.video_menu.addAction(self.video.disconnect_action)
        self.video_menu.addSeparator()
        self._catalog_status = self.video_menu.addAction("未確認")
        self._catalog_status.setEnabled(False)
        self.video_menu.addSeparator()
        self.video_device_actions: dict[str, QAction] = {}
        self._video_device_group = QActionGroup(self)
        self._video_device_group.setExclusive(True)
        self._video_choices: tuple[tuple[str, str], ...] = ()
        conversation_layout.addWidget(self.connection, 1)
        self.splitter.addWidget(self.video)
        self.splitter.addWidget(conversation)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)
        self.status = ElidedLabel(parent=self)
        self.statusBar().addWidget(self.status, 1)
        self.statusBar().addPermanentWidget(QLabel("映像", self))
        self.statusBar().addPermanentWidget(self.video.status)
        self.statusBar().addPermanentWidget(self.video.source, 2)
        self.video.status.show()
        self.video.source.show()
        self.statusBar().addPermanentWidget(self.notifications.entry_button)
        self.cancel_close_button = QPushButton("終了取消し", self)
        self.cancel_close_button.clicked.connect(self.cancel_close_requested)
        self.cancel_close_button.hide()
        self.statusBar().addPermanentWidget(self.cancel_close_button)

    def set_capture_devices(
        self,
        devices: "tuple[DeviceInfo, ...]",
        selected_device: str,
        *,
        checked: bool,
        searching: bool,
        error: str,
        enabled: bool,
    ) -> None:
        """Reflect saved selection without changing the active capture identity.

        Args:
            devices: Latest successful catalog, shared with the settings dialog.
            selected_device: Persisted path; missing identities remain represented.
            checked: Whether a catalog has been successfully received.
            searching: Whether discovery and its cleanup are pending.
            error: Current catalog failure, shown as a short state here.
            enabled: Whether selecting a device may save and apply settings.
        """
        choices = (("未選択", ""), *capture_device_choices(devices))
        if selected_device not in {path for _label, path in choices}:
            missing = "未検出" if checked else "未確認"
            choices += (("検索中" if searching else missing, selected_device),)
        if choices != self._video_choices:
            for action in self._video_device_group.actions():
                self.video_menu.removeAction(action)
                self._video_device_group.removeAction(action)
                action.deleteLater()
            self.video_device_actions.clear()
            for label, path in choices:
                text = self.video_menu.fontMetrics().elidedText(
                    label, Qt.TextElideMode.ElideMiddle, 320
                )
                action = QAction(text.replace("&", "&&"), self.video_menu)
                action.setToolTip(f"<qt>{escape(label)}</qt>")
                action.setData(path)
                action.setCheckable(True)
                action.triggered.connect(
                    lambda _checked=False, selected=path: self.capture_device_requested.emit(
                        selected
                    )
                )
                self._video_device_group.addAction(action)
                self.video_menu.addAction(action)
                self.video_device_actions[path] = action
            self._video_choices = choices
        self.video_device_actions[selected_device].setChecked(True)
        self._video_device_group.setEnabled(enabled)
        state = (
            "列挙失敗"
            if error
            else "検索中"
            if searching
            else "取得済み"
            if devices
            else "機器なし"
        )
        if not checked and not searching and not error:
            state = "未確認"
        self._catalog_status.setText(state)

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
            self.notifications.close_details()
            event.accept()
        else:
            event.ignore()
            self.close_requested.emit()
