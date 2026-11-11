"""Settings editor with labels and values, without instructional prose."""

from dataclasses import replace
from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeyEvent, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gigas.storage.settings import Settings
from gigas.ui.capture_choices import capture_device_choices
from gigas.ui.layout import WINDOW_SIZE_PRESETS

if TYPE_CHECKING:
    from pathlib import Path

    from gigas.game_io.capture.models import DeviceInfo


class SettingsDialog(QDialog):
    """Edit one settings snapshot in a modal window owned by the main window.

    Args:
        path: Settings location shown as a read-only value.
        parent: Main window that owns the dialog.
    """

    changed = Signal()
    capture_refresh_requested = Signal()
    close_requested = Signal()

    def __init__(self, path: "Path", parent: QWidget) -> None:
        """Create the form and action buttons."""
        super().__init__(parent)
        self._snapshot = Settings()
        self._loaded_text: dict[QLineEdit, tuple[str, str]] = {}
        self._devices: tuple[DeviceInfo, ...] = ()
        self.setModal(True)
        self.setWindowTitle("設定")
        self.resize(620, 440)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs, 1)
        general_form = self._tab("一般")
        self.location = QLineEdit(str(path), self)
        self.location.setReadOnly(True)
        location_row = QHBoxLayout()
        location_row.addWidget(self.location, 1)
        self.open_folder_button = QPushButton("フォルダを開く", self)
        self.open_folder_button.setAutoDefault(False)
        location_row.addWidget(self.open_folder_button)
        general_form.addRow("設定ファイル", location_row)
        display_form = self._group(general_form, "表示")
        self.window_size_preset = QComboBox(self)
        for preset in WINDOW_SIZE_PRESETS:
            self.window_size_preset.addItem(preset.label, preset.key)
        self.window_size_preset.setCurrentIndex(self.window_size_preset.findData("full_hd"))
        self.window_size_preset.currentIndexChanged.connect(self.changed)
        display_form.addRow("ウィンドウサイズ", self.window_size_preset)
        self.preview_mode = QComboBox(self)
        self.preview_mode.addItem("レターボックス", True)
        self.preview_mode.addItem("ストレッチ", False)
        self.preview_mode.currentIndexChanged.connect(self.changed)
        display_form.addRow("プレビュー方式", self.preview_mode)
        self.preview_fps = self._rate_field(30)
        display_form.addRow("表示FPS", self.preview_fps)
        io_form = self._tab("I/O")
        capture_form = self._group(io_form, "映像入力")
        self.capture_choices = QComboBox(self)
        self.capture_choices.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.capture_choices.setMinimumContentsLength(12)
        self.capture_choices.addItem("未接続", "")
        self.capture_choices.currentIndexChanged.connect(self.changed)
        self.capture_refresh_button = QPushButton("一覧更新", self)
        self.capture_refresh_button.clicked.connect(self.capture_refresh_requested)
        selection = QHBoxLayout()
        selection.addWidget(self.capture_choices, 1)
        selection.addWidget(self.capture_refresh_button)
        self.capture_discovery_status = QLabel(self)
        self.capture_discovery_status.setTextFormat(Qt.TextFormat.PlainText)
        self.capture_discovery_status.hide()
        selection.addWidget(self.capture_discovery_status)
        capture_form.addRow("機器", selection)
        self.capture_fps = self._rate_field(60)
        capture_form.addRow("取得FPS", self.capture_fps)
        self.capture_resolution = QComboBox(self)
        for label, key in (
            ("自動", "auto"),
            ("4K", "four_k"),
            ("WQHD", "wqhd"),
            ("FullHD", "full_hd"),
            ("HD", "hd"),
        ):
            self.capture_resolution.addItem(label, key)
        self.capture_resolution.setCurrentIndex(self.capture_resolution.findData("full_hd"))
        self.capture_resolution.currentIndexChanged.connect(self.changed)
        capture_form.addRow("取得解像度", self.capture_resolution)
        self.observation_resolution = QComboBox(self)
        for label, key in (("オリジナル", "original"), ("FullHD", "full_hd"), ("HD", "hd")):
            self.observation_resolution.addItem(label, key)
        self.observation_resolution.setCurrentIndex(self.observation_resolution.findData("full_hd"))
        self.observation_resolution.currentIndexChanged.connect(self.changed)
        capture_form.addRow("観測解像度", self.observation_resolution)
        controller_form = self._group(io_form, "コントローラー")
        self.serial_port = self._text_field()
        controller_form.addRow("シリアルポート", self.serial_port)
        agent_form = self._tab("Codex")
        self.model = self._text_field()
        self.effort = self._text_field()
        self.auth_method = QComboBox(self)
        self._auth_choices = QStandardItemModel(self.auth_method)
        self.auth_method.setModel(self._auth_choices)
        self._set_auth_method("chatgpt")
        self.auth_method.currentIndexChanged.connect(self.changed)
        agent_form.addRow("モデル", self.model)
        agent_form.addRow("エフォート", self.effort)
        agent_form.addRow("認証方式", self.auth_method)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.hide()
        error_row = QHBoxLayout()
        error_row.addWidget(self.status, 1)
        self.error_details_button = QPushButton("詳細…", self)
        self.error_details_button.setAutoDefault(False)
        self.error_details_button.hide()
        error_row.addWidget(self.error_details_button)
        layout.addLayout(error_row)
        actions = QDialogButtonBox(self)
        self.ok_button = actions.addButton(QDialogButtonBox.StandardButton.Ok)
        self.close_button = actions.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.save_button = actions.addButton(QDialogButtonBox.StandardButton.Apply)
        self.close_button.setText("キャンセル")
        self.save_button.setText("適用")
        for button in (self.ok_button, self.close_button, self.save_button):
            button.setAutoDefault(False)
            button.setDefault(False)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def _tab(self, title: str) -> QFormLayout:
        scroll = QScrollArea(self.tabs)
        scroll.setWidgetResizable(True)
        page = QWidget(scroll)
        form = QFormLayout(page)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, title)
        return form

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 -- Qt override
        """Handle Enter only for a focused button, never for an implicit default.

        Child widgets handle their own keys first, including combo-popup selection.
        Other keys retain the dialog's standard navigation and Escape behavior.
        """
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            focused = self.focusWidget()
            if isinstance(focused, QPushButton) and focused.isEnabled():
                focused.click()
            return
        super().keyPressEvent(event)

    def _group(self, parent_form: QFormLayout, title: str) -> QFormLayout:
        group = QGroupBox(title, self)
        form = QFormLayout(group)
        parent_form.addRow(group)
        return form

    def _text_field(self) -> QLineEdit:
        field = QLineEdit(self)
        field.textChanged.connect(self.changed)
        return field

    def _rate_field(self, default: int) -> QComboBox:
        field = QComboBox(self)
        self._set_rate(field, default)
        field.currentIndexChanged.connect(self.changed)
        return field

    def _set_auth_method(self, value: str) -> None:
        with QSignalBlocker(self.auth_method):
            self._auth_choices.clear()
            supported = QStandardItem("ChatGPT")
            supported.setData("chatgpt", Qt.ItemDataRole.UserRole)
            self._auth_choices.appendRow(supported)
            if value == "api_key":
                legacy = QStandardItem("APIキー (未対応)")
                legacy.setData("api_key", Qt.ItemDataRole.UserRole)
                legacy.setEnabled(False)
                self._auth_choices.appendRow(legacy)
            self.auth_method.setCurrentIndex(self.auth_method.findData(value))

    @staticmethod
    def _set_rate(field: QComboBox, value: int) -> None:
        with QSignalBlocker(field):
            field.clear()
            for rate in sorted({15, 30, 60, value}):
                field.addItem(str(rate), rate)
            field.setCurrentIndex(field.findData(value))

    def set_capture_devices(
        self,
        devices: "tuple[DeviceInfo, ...]",
        *,
        searching: bool = False,
        error: str = "",
    ) -> None:
        """Refresh candidates without changing the edited device or other fields.

        Args:
            devices: Latest successful catalog with stable identifiers.
            searching: Whether enumeration is in progress.
            error: Current enumeration failure, available through notification details.
        """
        state = "列挙失敗" if error else "検索中" if searching else ""
        self.capture_discovery_status.setText(state)
        self.capture_discovery_status.setVisible(bool(state))
        devices = tuple(device for device in devices if device.path)
        if devices != self._devices:
            selected = str(self.capture_choices.currentData() or "")
            self._devices = devices
            self._sync_capture_choice(selected)

    def _sync_capture_choice(self, selected: str) -> None:
        with QSignalBlocker(self.capture_choices):
            self.capture_choices.clear()
            self.capture_choices.addItem("未接続", "")
            for label, path in capture_device_choices(self._devices):
                self.capture_choices.addItem(label, path)
            if self.capture_choices.findData(selected) < 0:
                self.capture_choices.addItem(selected, selected)
            self.capture_choices.setCurrentIndex(self.capture_choices.findData(selected))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 -- Qt override
        """Request closure without discarding edits."""
        event.ignore()
        self.reject()

    def reject(self) -> None:
        """Route Escape through the same closure decision as the close button."""
        self.close_requested.emit()

    def values(self) -> Settings:
        """Return the edited snapshot, including the selected device or empty value."""
        return replace(
            self._snapshot,
            capture_device=str(self.capture_choices.currentData() or ""),
            serial_port=self._text_value(self.serial_port),
            model=self._text_value(self.model),
            effort=self._text_value(self.effort),
            auth_method=str(self.auth_method.currentData()),
            capture_fps=int(self.capture_fps.currentData()),
            preview_fps=int(self.preview_fps.currentData()),
            window_size_preset=str(self.window_size_preset.currentData()),
            capture_resolution=str(self.capture_resolution.currentData()),
            preview_letterbox=bool(self.preview_mode.currentData()),
            observation_resolution=self.observation_resolution.currentData(),
        )

    def set_values(self, values: Settings) -> None:
        """Replace the complete edited snapshot without emitting change notifications."""
        self._snapshot = values
        with QSignalBlocker(self):
            for field, value in (
                (self.serial_port, values.serial_port),
                (self.model, values.model),
                (self.effort, values.effort),
            ):
                field.setText(value)
                self._loaded_text[field] = (value, field.text())
            self._set_auth_method(values.auth_method)
            for field, value in (
                (self.capture_fps, values.capture_fps),
                (self.preview_fps, values.preview_fps),
            ):
                self._set_rate(field, value)
            self._sync_capture_choice(values.capture_device)
            self.window_size_preset.setCurrentIndex(
                self.window_size_preset.findData(values.window_size_preset)
            )
            self.capture_resolution.setCurrentIndex(
                self.capture_resolution.findData(values.capture_resolution)
            )
            self.preview_mode.setCurrentIndex(self.preview_mode.findData(values.preview_letterbox))
            self.observation_resolution.setCurrentIndex(
                self.observation_resolution.findData(values.observation_resolution)
            )

    def _text_value(self, field: QLineEdit) -> str:
        current = field.text()
        original, displayed = self._loaded_text.get(field, (current, current))
        return original if current == displayed else current

    def set_editable(self, enabled: bool) -> None:
        """Control editing during I/O while retaining copying and dialog closure."""
        for field in (self.serial_port, self.model, self.effort):
            field.setReadOnly(not enabled)
        self.auth_method.setEnabled(enabled)
        for field in (
            self.capture_choices,
            self.capture_fps,
            self.preview_fps,
            self.capture_refresh_button,
            self.window_size_preset,
            self.capture_resolution,
            self.observation_resolution,
            self.preview_mode,
        ):
            field.setEnabled(enabled)
