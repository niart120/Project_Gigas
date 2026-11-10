"""Coordinate the application views and settings operations."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from gigas.app.settings_worker import OperationResult, SettingsIO, SettingsWorker
from gigas.storage.settings import Settings
from gigas.ui.main_window import MainWindow
from gigas.ui.settings_dialog import SettingsDialog

if TYPE_CHECKING:
    from pathlib import Path


class ApplicationShell(QObject):
    """Own the main window and the application's settings path.

    Args:
        path: Explicit settings path; tests must supply an isolated location.
        io: Synchronous settings operations isolated in the owned thread.
    """

    requested = Signal(str, object)
    stopped = Signal()

    def __init__(self, path: "Path", io: SettingsIO = SettingsIO()) -> None:
        """Construct views without displaying them."""
        super().__init__()
        self.path = path
        self.window = MainWindow()
        self.dialog: SettingsDialog | None = None
        self.baseline = Settings()
        self.operation: str | None = None
        self.ready = False
        self.closing = False
        self.thread = QThread(self)
        self._worker = SettingsWorker(path, io)
        self._worker.moveToThread(self.thread)
        self.requested.connect(self._worker.perform)
        self._worker.completed.connect(self._complete)
        self.thread.finished.connect(self._worker.deleteLater)
        self.thread.finished.connect(self._finish_shutdown)
        self.window.settings_requested.connect(self.open_settings)
        self.window.close_requested.connect(self.request_close)

    def start(self) -> None:
        """Display the main window."""
        self.window.show()
        self.thread.start()
        self._request("load")

    def open_settings(self) -> None:
        """Show the single settings editor without discarding its input."""
        if self.dialog is None:
            self.dialog = SettingsDialog(self.path, self.window)
            self.dialog.save_button.clicked.connect(self.save)
            self.dialog.reload_button.clicked.connect(self.reload)
        self._render()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def save(self) -> None:
        """Save the current settings through the worker."""
        if self.ready:
            self._request("save")

    def reload(self) -> None:
        """Reload settings through the worker."""
        self._request("load")

    def _request(self, operation: str) -> None:
        if self.operation is not None or self.closing:
            return
        self.operation = operation
        self._render()
        self.requested.emit(operation, self.baseline)

    @Slot(object)
    def _complete(self, result: object) -> None:
        if not isinstance(result, OperationResult):
            return
        self.operation = None
        if result.settings is not None:
            self.baseline = result.settings
            self.ready = True
        self._render()
        if self.closing:
            self.thread.quit()

    def _render(self) -> None:
        state = {"load": "読込中", "save": "保存中"}.get(self.operation, "変更なし")
        self.window.status.setText(state)
        if self.dialog is not None:
            self.dialog.status.setText(state)
            self.dialog.form.setEnabled(self.operation is None and self.ready)
            self.dialog.save_button.setEnabled(self.operation is None and self.ready)
            self.dialog.reload_button.setEnabled(self.operation is None)

    def request_close(self) -> None:
        """Finish pending I/O before releasing the owned worker."""
        self.closing = True
        if self.operation is None:
            self.thread.quit()

    @Slot()
    def _finish_shutdown(self) -> None:
        if not self.thread.wait(0):
            QTimer.singleShot(10, self._finish_shutdown)
            return
        if self.dialog is not None:
            self.dialog.hide()
        self.window.allow_close = True
        self.window.close()
        self.stopped.emit()
