"""Settings editor with labels and values, without instructional prose."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QCloseEvent, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
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
    """Edit settings in one modeless window owned by the main window.

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
        self._loaded_text: dict[QLineEdit, tuple[str, str]] = {}
        self._saved_device = ""
        self._selected_device = ""
        self._edited_device: str | None = None
        self._closing_allowed = True
        self._devices: tuple[DeviceInfo, ...] = ()
        self._devices_checked = False
        self._devices_searching = False
        self.setWindowTitle("設定")
        self.resize(620, 440)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs, 1)
        general_form = self._tab("一般")
        self.location = QLineEdit(str(path), self)
        self.location.setReadOnly(True)
        general_form.addRow("設定ファイル", self.location)
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
        self.capture_choices.currentIndexChanged.connect(self._choose_capture_device)
        self.capture_choices.activated.connect(self._choose_capture_device)
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
        self.capture_discovery_error = QPlainTextEdit(self)
        self.capture_discovery_error.setReadOnly(True)
        self.capture_discovery_error.setMaximumHeight(70)
        self.capture_discovery_error.hide()
        self.capture_connection_status = QLabel(self)
        self.capture_connection_status.setTextFormat(Qt.TextFormat.PlainText)
        self.capture_connection_status.setWordWrap(True)
        self.capture_connection_status.hide()
        capture_form.addRow(self.capture_connection_status)
        self.capture_connection_error = QPlainTextEdit(self)
        self.capture_connection_error.setReadOnly(True)
        self.capture_connection_error.setMaximumHeight(70)
        self.capture_connection_error.hide()
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
        layout.addWidget(self.status)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(90)
        self.details.hide()
        layout.addWidget(self.details)
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
        checked: bool = True,
        searching: bool = False,
        error: str = "",
    ) -> None:
        """Replace selectable devices without changing any unsaved setting.

        Args:
            devices: Enumerated names and nonempty paths. Paths are stored unchanged.
            checked: Whether any successful enumeration has supplied these identities.
            searching: Whether a catalog request is still running.
            error: Latest catalog failure, retaining the prior successful candidates.
        """
        self._devices = tuple(device for device in devices if device.path)
        self._devices_checked = checked
        self._devices_searching = searching
        if error:
            state = "列挙失敗"
        elif searching:
            state = "検索中"
        else:
            state = ""
        self.capture_discovery_status.setText(state)
        self.capture_discovery_status.setVisible(bool(state))
        if self.capture_discovery_error.toPlainText() != error:
            self.capture_discovery_error.setPlainText(error)
        self._sync_capture_choice()

    def _sync_capture_choice(self) -> None:
        with QSignalBlocker(self.capture_choices):
            self.capture_choices.clear()
            self.capture_choices.addItem("未接続", "")
            for label, path in capture_device_choices(self._devices):
                self.capture_choices.addItem(label, path)
            selected = self.capture_choices.findData(self._selected_device)
            if selected < 0:
                missing = "未検出" if self._devices_checked else "未確認"
                self.capture_choices.addItem(
                    "検索中" if self._devices_searching else missing, self._selected_device
                )
                selected = self.capture_choices.count() - 1
            self.capture_choices.setCurrentIndex(selected)

    def _choose_capture_device(self) -> None:
        path = self.capture_choices.currentData()
        if isinstance(path, str):
            previous = self._selected_device, self._edited_device
            self._selected_device = path
            self._edited_device = path or None
            if previous != (self._selected_device, self._edited_device):
                self.changed.emit()

    @property
    def capture_target(self) -> str:
        """Return the requested live device path; an empty string requests disconnection."""
        return self._selected_device

    def set_capture_target(self, path: str) -> None:
        """Synchronize the live target without replacing the saved device.

        Args:
            path: Current live device path, or an empty string for disconnected state.
                This clears an explicit unsaved device choice without emitting changed.
        """
        self._selected_device = path
        self._edited_device = None
        self._sync_capture_choice()

    @property
    def closing_allowed(self) -> bool:
        """Return whether Cancel, Escape, and the window close control may request closure."""
        return self._closing_allowed

    def set_closing_allowed(self, allowed: bool) -> None:
        """Enable or suppress closure requests without accepting or rejecting the dialog."""
        self._closing_allowed = allowed
        self.close_button.setEnabled(allowed)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 -- Qt override
        """Request closure without discarding edits."""
        event.ignore()
        self.reject()

    def reject(self) -> None:
        """Route Escape through the same closure decision as the close button."""
        if self._closing_allowed:
            self.close_requested.emit()

    def values(self) -> Settings:
        """Return settings, retaining the saved device unless a real device was chosen.

        The connection target is separate: selecting disconnection cancels an unsaved
        device choice and preserves the last saved device. Text whitespace is retained.
        """
        return Settings(
            capture_device=self._edited_device or self._saved_device,
            serial_port=self._text_value(self.serial_port),
            model=self._text_value(self.model),
            effort=self._text_value(self.effort),
            auth_method=str(self.auth_method.currentData()),
            capture_fps=int(self.capture_fps.currentData()),
            preview_fps=int(self.preview_fps.currentData()),
            window_size_preset=str(self.window_size_preset.currentData()),
            capture_resolution=str(self.capture_resolution.currentData()),
            preview_letterbox=bool(self.preview_mode.currentData()),
        )

    def set_values(self, values: Settings, *, preserve_target: bool = False) -> None:
        """Replace settings and saved-device baseline without emitting edit notifications.

        Args:
            values: Saved settings to display, replacing unsaved field edits.
            preserve_target: Keep the current live target when true. Otherwise use the
                saved device as the target. Both paths clear an explicit device edit.
        """
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
            self._saved_device = values.capture_device
            self._edited_device = None
            if not preserve_target:
                self._selected_device = values.capture_device
            self._sync_capture_choice()
            self.window_size_preset.setCurrentIndex(
                self.window_size_preset.findData(values.window_size_preset)
            )
            self.capture_resolution.setCurrentIndex(
                self.capture_resolution.findData(values.capture_resolution)
            )
            self.preview_mode.setCurrentIndex(self.preview_mode.findData(values.preview_letterbox))

    def _text_value(self, field: QLineEdit) -> str:
        current = field.text()
        original, displayed = self._loaded_text.get(field, (current, current))
        return original if current == displayed else current

    def set_editable(self, enabled: bool) -> None:
        """Control editing and closure while retaining selection and copying of values.

        Call set_closing_allowed separately when read-only fields may still be closed,
        such as after a settings load failure.
        """
        self.set_closing_allowed(enabled)
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
            self.preview_mode,
        ):
            field.setEnabled(enabled)
