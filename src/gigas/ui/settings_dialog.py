"""Settings editor with labels and values, without instructional prose."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QToolButton,
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
    cancel_close_requested = Signal()

    def __init__(self, path: "Path", parent: QWidget) -> None:
        """Create the form and action buttons."""
        super().__init__(parent)
        self._loaded_text: dict[QLineEdit, tuple[str, str]] = {}
        self._selected_device = ""
        self._devices: tuple[DeviceInfo, ...] = ()
        self._devices_checked = False
        self._devices_searching = False
        self.setWindowTitle("設定")
        self.resize(560, 440)
        layout = QVBoxLayout(self)
        self.location = QLineEdit(str(path), self)
        self.location.setReadOnly(True)
        layout.addWidget(QLabel("保存先", self))
        layout.addWidget(self.location)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs, 1)
        self.video_form = self._tab("映像")
        self.capture_choices = QComboBox(self)
        self.capture_choices.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.capture_choices.setMinimumContentsLength(12)
        self.capture_choices.addItem("未選択", "")
        self.capture_choices.currentIndexChanged.connect(self._choose_capture_device)
        self.capture_refresh_button = QPushButton("一覧更新", self)
        self.capture_refresh_button.clicked.connect(self.capture_refresh_requested)
        selection = QHBoxLayout()
        selection.addWidget(self.capture_choices, 1)
        selection.addWidget(self.capture_refresh_button)
        self.video_form.addRow("機器", selection)
        self.capture_discovery_status = QLabel("未確認", self)
        self.capture_discovery_status.setTextFormat(Qt.TextFormat.PlainText)
        self.video_form.addRow("機器一覧", self.capture_discovery_status)
        self.capture_discovery_error = QPlainTextEdit(self)
        self.capture_discovery_error.setReadOnly(True)
        self.capture_discovery_error.setMaximumHeight(70)
        self.capture_discovery_error.hide()
        self.video_form.addRow(self.capture_discovery_error)
        connection = QHBoxLayout()
        self.capture_connect_button = QToolButton(self)
        self.capture_disconnect_button = QToolButton(self)
        self.capture_connection_status = QLabel("未接続", self)
        self.capture_connection_status.setTextFormat(Qt.TextFormat.PlainText)
        for widget in (
            self.capture_connect_button,
            self.capture_disconnect_button,
            self.capture_connection_status,
        ):
            connection.addWidget(widget)
        connection.addStretch()
        self.video_form.addRow("映像", connection)
        self.capture_connection_error = QPlainTextEdit(self)
        self.capture_connection_error.setReadOnly(True)
        self.capture_connection_error.setMaximumHeight(70)
        self.capture_connection_error.hide()
        self.video_form.addRow(self.capture_connection_error)
        self.capture_fps = self._rate_field(60)
        self.preview_fps = self._rate_field(30)
        for label, field in (
            ("取得FPS", self.capture_fps),
            ("表示FPS", self.preview_fps),
        ):
            self.video_form.addRow(label, field)
        self.window_size_preset = QComboBox(self)
        for preset in WINDOW_SIZE_PRESETS:
            self.window_size_preset.addItem(preset.label, preset.key)
        self.window_size_preset.setCurrentIndex(self.window_size_preset.findData("full_hd"))
        self.window_size_preset.currentIndexChanged.connect(self.changed)
        self.video_form.addRow("ウィンドウサイズ", self.window_size_preset)
        agent_form = self._tab("エージェント")
        self.model = self._text_field()
        self.effort = self._text_field()
        self.auth_method = QComboBox(self)
        self.auth_method.addItem("ChatGPT", "chatgpt")
        self.auth_method.addItem("APIキー", "api_key")
        self.auth_method.currentIndexChanged.connect(self.changed)
        agent_form.addRow("モデル", self.model)
        agent_form.addRow("エフォート", self.effort)
        agent_form.addRow("認証方式", self.auth_method)
        input_form = self._tab("入力")
        self.serial_port = self._text_field()
        input_form.addRow("シリアルポート", self.serial_port)
        self.status = QLabel("変更なし", self)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(90)
        self.details.hide()
        layout.addWidget(self.details)
        actions = QHBoxLayout()
        actions.addStretch()
        self.save_button = QPushButton("適用", self)
        self.reload_button = QPushButton("再読込", self)
        self.close_button = QPushButton("閉じる", self)
        self.cancel_close_button = QPushButton("閉じる取消し", self)
        self.cancel_close_button.clicked.connect(self.cancel_close_requested)
        self.cancel_close_button.hide()
        for button in (
            self.save_button,
            self.reload_button,
            self.close_button,
            self.cancel_close_button,
        ):
            button.setAutoDefault(False)
            actions.addWidget(button)
        self.close_button.clicked.connect(self.close)
        layout.addLayout(actions)

    def _tab(self, title: str) -> QFormLayout:
        scroll = QScrollArea(self.tabs)
        scroll.setWidgetResizable(True)
        page = QWidget(scroll)
        form = QFormLayout(page)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, title)
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
        elif checked:
            state = "取得済み" if self._devices else "機器なし"
        else:
            state = "未確認"
        self.capture_discovery_status.setText(state)
        if self.capture_discovery_error.toPlainText() != error:
            self.capture_discovery_error.setPlainText(error)
        self.capture_discovery_error.setVisible(bool(error))
        self._sync_capture_choice()

    def _sync_capture_choice(self) -> None:
        with QSignalBlocker(self.capture_choices):
            self.capture_choices.clear()
            self.capture_choices.addItem("未選択", "")
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
            self._selected_device = path
            self.changed.emit()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 -- Qt override
        """Request closure without discarding edits."""
        event.ignore()
        self.close_requested.emit()

    def reject(self) -> None:
        """Route Escape through the same closure decision as the close button."""
        self.close_requested.emit()

    def values(self) -> Settings:
        """Return the form's current values without stripping whitespace."""
        return Settings(
            capture_device=self._selected_device,
            serial_port=self._text_value(self.serial_port),
            model=self._text_value(self.model),
            effort=self._text_value(self.effort),
            auth_method=str(self.auth_method.currentData()),
            capture_fps=int(self.capture_fps.currentData()),
            preview_fps=int(self.preview_fps.currentData()),
            window_size_preset=str(self.window_size_preset.currentData()),
        )

    def set_values(self, values: Settings) -> None:
        """Replace displayed values without emitting user-edit notifications."""
        with QSignalBlocker(self):
            for field, value in (
                (self.serial_port, values.serial_port),
                (self.model, values.model),
                (self.effort, values.effort),
            ):
                field.setText(value)
                self._loaded_text[field] = (value, field.text())
            self.auth_method.setCurrentIndex(self.auth_method.findData(values.auth_method))
            for field, value in (
                (self.capture_fps, values.capture_fps),
                (self.preview_fps, values.preview_fps),
            ):
                self._set_rate(field, value)
            self._selected_device = values.capture_device
            self._sync_capture_choice()
            self.window_size_preset.setCurrentIndex(
                self.window_size_preset.findData(values.window_size_preset)
            )

    def _text_value(self, field: QLineEdit) -> str:
        current = field.text()
        original, displayed = self._loaded_text.get(field, (current, current))
        return original if current == displayed else current

    def set_editable(self, enabled: bool) -> None:
        """Control editing while retaining selection and copying of values."""
        for field in (self.serial_port, self.model, self.effort):
            field.setReadOnly(not enabled)
        self.auth_method.setEnabled(enabled)
        for field in (
            self.capture_choices,
            self.capture_fps,
            self.preview_fps,
            self.capture_refresh_button,
            self.window_size_preset,
        ):
            field.setEnabled(enabled)
