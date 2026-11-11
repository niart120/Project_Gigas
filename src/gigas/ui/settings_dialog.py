"""Settings editor with labels and values, without instructional prose."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gigas.storage.settings import Settings

if TYPE_CHECKING:
    from pathlib import Path

    from gigas.game_io.models import DeviceInfo


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
        self._loaded_text: dict[QPlainTextEdit, tuple[str, str]] = {}
        self.setWindowTitle("設定")
        self.resize(560, 440)
        layout = QVBoxLayout(self)
        self.location = QLineEdit(str(path), self)
        self.location.setReadOnly(True)
        layout.addWidget(QLabel("保存先", self))
        layout.addWidget(self.location)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        self.form = QWidget(scroll)
        body = QVBoxLayout(self.form)
        devices = QGroupBox("映像・入力", self.form)
        device_form = QFormLayout(devices)
        self.capture_device = self._text_field()
        self.serial_port = self._text_field()
        self.capture_choices = QComboBox(devices)
        self.capture_choices.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.capture_choices.setMinimumContentsLength(12)
        self.capture_choices.addItem("未選択", "")
        self.capture_choices.currentIndexChanged.connect(self._choose_capture_device)
        self.capture_device.textChanged.connect(self._sync_capture_choice)
        self.capture_refresh_button = QPushButton("一覧更新", devices)
        self.capture_refresh_button.clicked.connect(self.capture_refresh_requested)
        selection = QHBoxLayout()
        selection.addWidget(self.capture_choices, 1)
        selection.addWidget(self.capture_refresh_button)
        device_form.addRow("映像機器", selection)
        device_form.addRow("映像取得元", self.capture_device)
        self.capture_width = self._number_field(16, 3840, 1920)
        self.capture_height = self._number_field(16, 2160, 1080)
        self.capture_fps = self._number_field(1, 120, 60)
        self.preview_fps = self._number_field(1, 60, 30)
        for label, field in (
            ("幅", self.capture_width),
            ("高さ", self.capture_height),
            ("取得FPS", self.capture_fps),
            ("表示FPS", self.preview_fps),
        ):
            device_form.addRow(label, field)
        device_form.addRow("シリアルポート", self.serial_port)
        body.addWidget(devices)
        agent = QGroupBox("エージェント", self.form)
        agent_form = QFormLayout(agent)
        self.model = self._text_field()
        self.effort = self._text_field()
        self.auth_method = QComboBox(agent)
        self.auth_method.addItem("ChatGPT", "chatgpt")
        self.auth_method.addItem("APIキー", "api_key")
        self.auth_method.currentIndexChanged.connect(self.changed)
        agent_form.addRow("モデル", self.model)
        agent_form.addRow("エフォート", self.effort)
        agent_form.addRow("認証方式", self.auth_method)
        body.addWidget(agent)
        scroll.setWidget(self.form)
        layout.addWidget(scroll, 1)
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
        self.save_button = QPushButton("保存", self)
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

    def _text_field(self) -> QPlainTextEdit:
        field = QPlainTextEdit(self.form)
        field.setTabChangesFocus(True)
        field.setFixedHeight(44)
        field.textChanged.connect(self.changed)
        return field

    def _number_field(self, minimum: int, maximum: int, default: int) -> QSpinBox:
        field = QSpinBox(self.form)
        field.setRange(minimum, maximum)
        field.setValue(default)
        field.valueChanged.connect(self.changed)
        return field

    def set_capture_devices(self, devices: "tuple[DeviceInfo, ...]") -> None:
        """Replace selectable devices without changing any unsaved setting.

        Args:
            devices: Enumerated names and nonempty paths. Paths are stored unchanged.
        """
        with QSignalBlocker(self.capture_choices):
            self.capture_choices.clear()
            self.capture_choices.addItem("未選択", "")
            for index, device in enumerate(devices, 1):
                if device.path:
                    self.capture_choices.addItem(f"{index}: {device.name}", device.path)
            self._sync_capture_choice()

    def _sync_capture_choice(self) -> None:
        path = self._text_value(self.capture_device)
        with QSignalBlocker(self.capture_choices):
            selected = self.capture_choices.findData(path)
            self.capture_choices.setItemText(0, "一覧外" if selected < 0 else "未選択")
            self.capture_choices.setCurrentIndex(max(0, selected))

    def _choose_capture_device(self) -> None:
        path = self.capture_choices.currentData()
        if isinstance(path, str):
            self.capture_device.setPlainText(path)

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
            capture_device=self._text_value(self.capture_device),
            serial_port=self._text_value(self.serial_port),
            model=self._text_value(self.model),
            effort=self._text_value(self.effort),
            auth_method=str(self.auth_method.currentData()),
            capture_width=self.capture_width.value(),
            capture_height=self.capture_height.value(),
            capture_fps=self.capture_fps.value(),
            preview_fps=self.preview_fps.value(),
        )

    def set_values(self, values: Settings) -> None:
        """Replace displayed values without emitting user-edit notifications."""
        with QSignalBlocker(self):
            for field, value in (
                (self.capture_device, values.capture_device),
                (self.serial_port, values.serial_port),
                (self.model, values.model),
                (self.effort, values.effort),
            ):
                field.setPlainText(value)
                self._loaded_text[field] = (value, field.toPlainText())
            self.auth_method.setCurrentIndex(self.auth_method.findData(values.auth_method))
            for field, value in (
                (self.capture_width, values.capture_width),
                (self.capture_height, values.capture_height),
                (self.capture_fps, values.capture_fps),
                (self.preview_fps, values.preview_fps),
            ):
                field.setValue(value)
            with QSignalBlocker(self.capture_choices):
                selected = self.capture_choices.findData(values.capture_device)
                self.capture_choices.setCurrentIndex(max(0, selected))

    def _text_value(self, field: QPlainTextEdit) -> str:
        current = field.toPlainText()
        original, displayed = self._loaded_text.get(field, (current, current))
        return original if current == displayed else current

    def set_editable(self, enabled: bool) -> None:
        """Control editing while retaining selection and copying of values."""
        for field in (self.capture_device, self.serial_port, self.model, self.effort):
            field.setReadOnly(not enabled)
        self.auth_method.setEnabled(enabled)
        for field in (
            self.capture_choices,
            self.capture_width,
            self.capture_height,
            self.capture_fps,
            self.preview_fps,
            self.capture_refresh_button,
        ):
            field.setEnabled(enabled)
