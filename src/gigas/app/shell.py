"""Coordinate the application views and settings operations."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import QMessageBox

from gigas.app.settings_worker import OperationResult, SettingsIO, SettingsWorker
from gigas.storage.settings import Settings
from gigas.ui.confirmation import changes_confirmation
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

    def __init__(self, path: "Path", io: SettingsIO | None = None) -> None:
        """Construct views without displaying them."""
        super().__init__()
        self.path = path
        self.window = MainWindow()
        self.dialog: SettingsDialog | None = None
        self.baseline = Settings()
        self.operation: str | None = None
        self.ready = False
        self.error = ""
        self.error_operation = ""
        self.pending: str | None = None
        self.committed = False
        self.confirmation: QMessageBox | None = None
        self.wait_expired = False
        self.wait_timer = QTimer(self)
        self.wait_timer.setSingleShot(True)
        self.wait_timer.setInterval(5000)
        self.wait_timer.timeout.connect(self._show_wait_details)
        self.io_thread = QThread(self)
        self._worker = SettingsWorker(path, SettingsIO() if io is None else io)
        self._worker.moveToThread(self.io_thread)
        self.requested.connect(self._worker.perform)
        self._worker.completed.connect(self._complete)
        self.io_thread.finished.connect(self._worker.deleteLater)
        self.io_thread.finished.connect(self._finish_shutdown)
        self.window.settings_requested.connect(self.open_settings)
        self.window.close_requested.connect(self.request_close)
        self.window.cancel_close_requested.connect(self.cancel_close)

    def start(self) -> None:
        """Display the main window."""
        self.window.show()
        self.io_thread.start()
        self._request("load")

    def open_settings(self) -> None:
        """Show the single settings editor without discarding its input."""
        if self.dialog is None:
            self.dialog = SettingsDialog(self.path, self.window)
            self.dialog.set_values(self.baseline)
            self.dialog.save_button.clicked.connect(self.save)
            self.dialog.reload_button.clicked.connect(self.reload)
            self.dialog.changed.connect(self._render)
            self.dialog.close_requested.connect(lambda: self._close("dialog"))
            self.dialog.cancel_close_requested.connect(self.cancel_close)
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
        if self.operation is not None or self.pending is not None or self.committed:
            return
        if self.dirty:
            self._close("reload")
        else:
            self._request("load")

    def _request(self, operation: str, *, continuing: bool = False) -> None:
        if (
            self.operation is not None
            or self.committed
            or self.confirmation is not None
            or (self.pending is not None and not continuing)
        ):
            return
        self.operation = operation
        self.error = ""
        self._render()
        snapshot = self.dialog.values() if self.dialog is not None else self.baseline
        self.requested.emit(operation, snapshot)

    @Slot(object)
    def _complete(self, result: object) -> None:
        if not isinstance(result, OperationResult):
            return
        operation = self.operation
        self.operation = None
        if result.settings is not None:
            self.baseline = result.settings
            self.ready = True
            if self.dialog is not None:
                self.dialog.set_values(result.settings)
        else:
            self.error = result.error
            self.error_operation = "読込失敗" if operation == "load" else "保存失敗"
            if operation == "load":
                self.ready = False
        if self.error and operation == "save":
            self.pending = None
            self.wait_timer.stop()
            self.wait_expired = False
        elif self.pending is not None:
            self._resolve_close()
        self._render()

    @property
    def dirty(self) -> bool:
        """Whether the current editor differs from the last successful snapshot."""
        return self.dialog is not None and self.dialog.values() != self.baseline

    def _render(self) -> None:
        state = {"load": "読込中", "save": "保存中"}.get(
            self.operation, "未保存" if self.dirty else "変更なし"
        )
        if self.error:
            state = self.error_operation
        cancelable = self.pending in ("app", "dialog") and self.operation is not None
        if cancelable or self.committed:
            state = "閉じる待ち" if self.pending == "dialog" else "終了待ち"
            if self.wait_expired:
                target = {"load": "設定読込", "save": "設定保存"}.get(
                    self.operation, "ワーカー終了"
                )
                state = f"{state} · {target} · {self.path}"
        self.window.status.setText(state)
        self.window.cancel_close_button.setVisible(cancelable)
        self.window.cancel_close_button.setText(
            "閉じる取消し" if self.pending == "dialog" else "終了取消し"
        )
        self.window.settings_button.setEnabled(not self.committed)
        if self.dialog is not None:
            self.dialog.status.setText(f"{state}\n{self.error}" if self.error else state)
            idle = self.operation is None and self.pending is None and not self.committed
            self.dialog.set_editable(idle and self.ready)
            self.dialog.save_button.setEnabled(idle and self.ready)
            self.dialog.reload_button.setEnabled(idle)
            self.dialog.cancel_close_button.setVisible(cancelable)
            self.dialog.cancel_close_button.setText(self.window.cancel_close_button.text())

    def request_close(self) -> None:
        """Finish pending I/O before releasing the owned worker."""
        self._close("app")

    def _close(self, target: str) -> None:
        if self.committed or self.confirmation is not None:
            return
        if self.pending != "app":
            self.pending = target
        if self.operation is None:
            self._resolve_close()
        elif not self.wait_timer.isActive():
            self.wait_timer.start()
        self._render()

    def _resolve_close(self) -> None:
        self.wait_timer.stop()
        self.wait_expired = False
        if self.dirty:
            parent = self.dialog if self.dialog is not None else self.window
            self.confirmation = changes_confirmation(
                parent, allow_save=self.ready and self.pending != "reload"
            )
            self.confirmation.finished.connect(self._confirmed)
            self.confirmation.show()
        else:
            self._finish_action()

    @Slot(int)
    def _confirmed(self, result: int) -> None:
        box = self.confirmation
        self.confirmation = None
        if box is not None:
            box.deleteLater()
        if result == QMessageBox.StandardButton.Save and self.ready:
            self._request("save", continuing=True)
        elif result == QMessageBox.StandardButton.Discard:
            if self.dialog is not None and self.pending != "reload":
                self.dialog.set_values(self.baseline)
            self._finish_action()
        else:
            self.pending = None
        self._render()

    def _finish_action(self) -> None:
        target = self.pending
        self.pending = None
        if target == "app":
            self.committed = True
            self.wait_timer.start()
            if self.io_thread.isRunning():
                self.io_thread.quit()
            else:
                self._finish_shutdown()
        elif target == "dialog" and self.dialog is not None:
            self.dialog.hide()
        elif target == "reload":
            self._request("load")

    def cancel_close(self) -> None:
        """Cancel a pending window closure without interrupting file I/O."""
        if self.committed or self.confirmation is not None:
            return
        self.pending = None
        self.wait_timer.stop()
        self.wait_expired = False
        self._render()

    @Slot()
    def _show_wait_details(self) -> None:
        self.wait_expired = True
        self._render()

    @Slot()
    def _finish_shutdown(self) -> None:
        if not self.io_thread.wait(0):
            QTimer.singleShot(10, self._finish_shutdown)
            return
        self.wait_timer.stop()
        if self.dialog is not None:
            self.dialog.hide()
        self.window.allow_close = True
        self.window.close()
        self.stopped.emit()
