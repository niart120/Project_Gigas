"""Settings editor with labels and values, without instructional prose."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker, Qt, Signal
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
    QVBoxLayout,
    QWidget,
)

from gigas.storage.settings import Settings

if TYPE_CHECKING:
    from pathlib import Path


class SettingsDialog(QDialog):
    """Edit settings in one modeless window owned by the main window.

    Args:
        path: Settings location shown as a read-only value.
        parent: Main window that owns the dialog.
    """

    changed = Signal()

    def __init__(self, path: "Path", parent: QWidget) -> None:
        """Create the form and action buttons."""
        super().__init__(parent)
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
        device_form.addRow("映像取得元", self.capture_device)
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
        actions = QHBoxLayout()
        actions.addStretch()
        self.save_button = QPushButton("保存", self)
        self.reload_button = QPushButton("再読込", self)
        self.close_button = QPushButton("閉じる", self)
        for button in (self.save_button, self.reload_button, self.close_button):
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

    def values(self) -> Settings:
        """Return the form's current values without stripping whitespace."""
        return Settings(
            capture_device=self.capture_device.toPlainText(),
            serial_port=self.serial_port.toPlainText(),
            model=self.model.toPlainText(),
            effort=self.effort.toPlainText(),
            auth_method=str(self.auth_method.currentData()),
        )

    def set_values(self, values: Settings) -> None:
        """Replace displayed values without emitting user-edit notifications."""
        with QSignalBlocker(self):
            self.capture_device.setPlainText(values.capture_device)
            self.serial_port.setPlainText(values.serial_port)
            self.model.setPlainText(values.model)
            self.effort.setPlainText(values.effort)
            self.auth_method.setCurrentIndex(self.auth_method.findData(values.auth_method))
