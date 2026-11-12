"""Main application window."""

from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gigas.game_io.input.config import PROTOCOLS
from gigas.game_io.input.serial import SerialPort
from gigas.ui.capture_choices import capture_device_choices
from gigas.ui.connection_panel import ConnectionPanel
from gigas.ui.elided_label import ElidedLabel
from gigas.ui.geometry import fit_to_screen
from gigas.ui.layout import WINDOW_SIZE_PRESETS, fit_window_size_preset
from gigas.ui.notifications import NotificationCenter
from gigas.ui.video_panel import VideoPanel

if TYPE_CHECKING:
    from PySide6.QtCore import QObject
    from PySide6.QtGui import QResizeEvent

    from gigas.game_io.capture.models import DeviceInfo


class _PaneStatuses(QWidget):
    """Align two status rows with the panes without identifier width pressure."""

    def __init__(
        self, splitter: QSplitter, video: VideoPanel, connection: ConnectionPanel, parent: QWidget
    ) -> None:
        super().__init__(parent)
        self._splitter = splitter
        self._rows: list[QWidget] = []
        for title, status, identifier in (
            ("映像", video.status, video.source),
            ("Codex", connection.status, connection.model),
        ):
            row = QWidget(self)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(QLabel(title, row))
            layout.addWidget(status)
            layout.addWidget(identifier, 1)
            status.show()
            identifier.show()
            row.installEventFilter(self)
            self._rows.append(row)
        for index in range(splitter.count()):
            pane = splitter.widget(index)
            if pane is not None:
                pane.installEventFilter(self)
        splitter.installEventFilter(self)
        splitter.splitterMoved.connect(self._align)

    def sizeHint(self) -> QSize:  # noqa: N802 -- Qt override
        """Reserve line height while letting the pane widths determine truncation."""
        return QSize(0, max(row.sizeHint().height() for row in self._rows))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 -- Qt override
        """Avoid making long identifiers constrain the window width."""
        return self.sizeHint()

    def resizeEvent(self, event: "QResizeEvent") -> None:  # noqa: N802 -- Qt override
        """Realign when the status bar receives its final allocation."""
        super().resizeEvent(event)
        self._align()

    def eventFilter(self, watched: "QObject", event: QEvent) -> bool:  # noqa: N802
        """Follow pane moves and font or state changes without copying status text."""
        if event.type() in {QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.LayoutRequest}:
            if event.type() == QEvent.Type.LayoutRequest:
                self.updateGeometry()
            self._align()
        return super().eventFilter(watched, event)

    def _align(self) -> None:
        for index, row in enumerate(self._rows):
            pane = self._splitter.widget(index)
            if pane is None:
                continue
            minimum = max(0, row.minimumSizeHint().width())
            if pane.minimumWidth() != minimum:
                pane.setMinimumWidth(minimum)
            left = self.mapFromGlobal(pane.mapToGlobal(QPoint())).x()
            row.setGeometry(left, 0, min(pane.width(), max(0, self.width() - left)), self.height())


class MainWindow(QMainWindow):
    """Present shared menus, video, conversation, and current status."""

    settings_requested = Signal()
    new_project_requested = Signal()
    open_project_requested = Signal()
    close_requested = Signal()
    window_size_requested = Signal(str)
    input_port_requested = Signal(str)
    input_protocol_requested = Signal(str)
    video_menu_opened = Signal()
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
        self.settings_action = QAction("設定", self)
        self.addAction(self.settings_action)
        self.settings_action.setShortcut(QKeySequence("Ctrl+,"))
        self.settings_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.settings_action.setAutoRepeat(False)
        self.settings_action.triggered.connect(self.settings_requested)
        self.settings_action.setIcon(QIcon(str(Path(__file__).with_name("settings.svg"))))
        self.settings_button = QToolButton(self)
        self.settings_button.setDefaultAction(self.settings_action)
        self.settings_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.settings_button.setAccessibleName("設定")
        self.settings_button.setToolTip("")
        self.settings_button.setAutoRaise(True)
        self.menuBar().setCornerWidget(self.settings_button, Qt.Corner.TopLeftCorner)
        self.project_menu = self.menuBar().addMenu("プロジェクト")
        self.new_project_action = self.project_menu.addAction("新規作成")
        self.new_project_action.triggered.connect(self.new_project_requested)
        self.open_project_action = self.project_menu.addAction("開く")
        self.open_project_action.triggered.connect(self.open_project_requested)
        self.connection_menu = self.menuBar().addMenu("接続")
        self.video_menu = self.connection_menu.addMenu("映像")
        self.video_menu.setToolTipsVisible(True)
        self.video_menu.aboutToShow.connect(self.video_menu_opened)
        self.input_menu = self.connection_menu.addMenu("入力")
        self.input_port_menu = self.input_menu.addMenu("ポート")
        self.input_protocol_menu = self.input_menu.addMenu("プロトコル")
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
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.notifications.entry_action)
        layout.addWidget(self.notifications.banner)
        self.close_progress = QWidget(central)
        progress_layout = QHBoxLayout(self.close_progress)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        self.status = ElidedLabel(parent=self.close_progress)
        progress_layout.addWidget(self.status, 1)
        self.close_progress.hide()
        layout.addWidget(self.close_progress)
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
        self.statusBar().setSizeGripEnabled(False)
        self._pane_statuses = _PaneStatuses(
            self.splitter, self.video, self.connection, self.statusBar()
        )
        self.statusBar().addWidget(self._pane_statuses, 1)

    def set_input_choices(
        self, ports: tuple[SerialPort, ...], port: str, protocol: str, *, enabled: bool
    ) -> None:
        """Show saved input selections; absent saved ports remain selectable."""
        choices = [("未接続", ""), *((p.identifier, p.identifier) for p in ports)]
        if port not in {value for _, value in choices}:
            choices.append((port, port))
        for menu, items, selected, signal in (
            (self.input_port_menu, choices, port, self.input_port_requested),
            (
                self.input_protocol_menu,
                [(p.label, p.key) for p in PROTOCOLS],
                protocol,
                self.input_protocol_requested,
            ),
        ):
            if [a.data() for a in menu.actions()] == [value for _, value in items]:
                menu.setEnabled(enabled)
                for action in menu.actions():
                    action.setChecked(action.data() == selected)
                continue
            menu.clear()
            menu.setEnabled(enabled)
            for label, value in items:
                action = menu.addAction(label.replace("&", "&&"))
                action.setData(value)
                action.setCheckable(True)
                action.setChecked(value == selected)
                action.triggered.connect(lambda _checked=False, v=value, sig=signal: sig.emit(v))

    def refresh_close_progress(self) -> None:
        """Show pending shutdown status without owning shutdown state."""
        self.close_progress.setVisible(bool(self.status.text()))

    def set_capture_devices(
        self,
        devices: "tuple[DeviceInfo, ...]",
        selected_device: str,
        *,
        searching: bool,
        error: str,
        enabled: bool,
    ) -> None:
        """Reflect the saved device independently of capture connection state.

        Args:
            devices: Latest successful catalog, shared with the settings dialog.
            selected_device: Saved device path, or empty for no capture. A saved
                identity missing from the catalog remains represented.
            searching: Whether discovery and its cleanup are pending.
            error: Current catalog failure, shown as a short state here.
            enabled: Whether selecting a device may save and apply settings.
        """
        choices = (("未接続", ""), *capture_device_choices(devices))
        if selected_device not in {path for _label, path in choices}:
            choices += ((selected_device, selected_device),)
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
        state = "列挙失敗" if error else "検索中" if searching else ""
        self._catalog_status.setText(state)
        self._catalog_status.setVisible(bool(state))

    def set_window_size_state(self, key: str, *, enabled: bool) -> None:
        """Reflect the saved selection and whether a new save can be requested."""
        self.window_size_actions[key].setChecked(True)
        self.window_size_group.setEnabled(enabled)

    def apply_window_size_preset(self, key: str) -> None:
        """Fit the requested window and preview to the available screen.

        Args:
            key: A validated saved preset. Controls may reduce the actual preview
                below its scaled target; the selected preview fitting mode stays active.
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
