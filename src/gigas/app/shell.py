"""Coordinate the application views and settings operations."""

from dataclasses import replace
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from gigas.app.settings_worker import OperationResult, SettingsIO, SettingsWorker
from gigas.app.video import VideoController
from gigas.runtime.codex.models import ConnectionOptions
from gigas.services.codex import CodexServiceThread
from gigas.storage.settings import Settings
from gigas.ui.confirmation import changes_confirmation
from gigas.ui.geometry import fit_to_screen
from gigas.ui.main_window import MainWindow
from gigas.ui.notifications import Notice
from gigas.ui.request_card import AnswerSubmission
from gigas.ui.settings_dialog import SettingsDialog

if TYPE_CHECKING:
    from pathlib import Path

    from gigas.app.video import CapturePort


class ApplicationShell(QObject):
    """Own the main window and the application's settings path.

    Args:
        path: Explicit settings path; tests must supply an isolated location.
        io: Synchronous settings operations isolated in the owned thread.
        service: Optional unstarted Codex service owned until application shutdown.
        capture_service: Optional unstarted capture service owned until confirmed cleanup.
    """

    requested = Signal(str, object)
    stopped = Signal()

    def __init__(
        self,
        path: "Path",
        io: SettingsIO | None = None,
        *,
        service: CodexServiceThread | None = None,
        capture_service: "CapturePort | None" = None,
    ) -> None:
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
        self._settings_error_notice: Notice | None = None
        self.pending: str | None = None
        self.committed = False
        self._stopped = False
        self._worker_released = False
        self._service_unavailable = False
        self._refresh_on_open = False
        self._refresh_menu_on_open = False
        self._capture_after_save: Settings | None = None
        self._applying_settings = False
        self._close_after_apply = False
        self._settings_saved = False
        self._shutdown_failure = ""
        self.service = CodexServiceThread() if service is None else service
        self.video = VideoController(self.window.video, capture_service, self)
        self.video.changed.connect(self._video_changed)
        self.video.devices_changed.connect(self._update_capture_devices)
        self.video.failed.connect(self.window.notifications.publish)
        self.window.video.connect_requested.connect(self.connect_video)
        self.window.video.disconnect_requested.connect(
            lambda: self.disconnect_video(refresh_devices=True)
        )
        self.confirmation: QMessageBox | None = None
        self.wait_expired = False
        self.wait_timer = QTimer(self)
        self.wait_timer.setSingleShot(True)
        self.wait_timer.setInterval(5000)
        self.wait_timer.timeout.connect(self._show_wait_details)
        self.io_thread = QThread(self)
        self._worker = SettingsWorker(path, SettingsIO() if io is None else io)
        self.requested.connect(self._worker.perform)
        self._worker.completed.connect(self._complete)
        self._worker.destroyed.connect(
            self._settings_worker_destroyed, Qt.ConnectionType.QueuedConnection
        )
        self.io_thread.finished.connect(self._worker.retire)
        self.io_thread.finished.connect(self._finish_shutdown)
        self.window.settings_requested.connect(self.open_settings)
        self.window.reload_action.triggered.connect(self.reload)
        self.window.settings_location_action.triggered.connect(self._open_settings_location)
        self.window.video_menu_opened.connect(self._open_video_menu)
        self.window.video_menu_closed.connect(self._close_video_menu)
        self.window.capture_device_requested.connect(self._select_video_device)
        self.window.notifications.settings_requested.connect(self.open_settings)
        self.window.window_size_requested.connect(self.change_window_size)
        self.window.close_requested.connect(self.request_close)
        self.window.cancel_close_requested.connect(self.cancel_close)
        panel = self.window.connection
        panel.connect_requested.connect(self.connect_codex)
        panel.disconnect_requested.connect(self.service.disconnect_runtime)
        panel.login_requested.connect(self.service.login)
        panel.send_requested.connect(self.service.send)
        panel.interrupt_requested.connect(self.service.interrupt)
        panel.answer_requested.connect(self._answer_codex)
        self.service.received.connect(panel.handle_event)
        self.service.completed.connect(panel.handle_result)
        self.service.finished.connect(self._service_finished)

    def start(self) -> None:
        """Display the main window."""
        self.window.show()
        fit_to_screen(self.window)
        self.io_thread.start()
        self._worker.moveToThread(self.io_thread)
        self.service.start()
        self.video.start()
        self._request("load")

    def connect_video(self) -> None:
        """Connect video using the last successful settings snapshot."""
        if self._can_start_video and not self.dirty and self.baseline.capture_device:
            self.video.connect_capture(self.baseline)

    @property
    def _can_start_video(self) -> bool:
        return (
            self.ready
            and not self.committed
            and self.pending is None
            and self.operation is None
            and self._capture_after_save is None
            and not self.video.applying
        )

    def disconnect_video(self, *, refresh_devices: bool = False) -> None:
        """Cancel a queued application and stop video without waiting for settings I/O.

        Args:
            refresh_devices: Discover once after an explicit UI disconnection.
        """
        self._capture_after_save = None
        self._applying_settings = False
        self._close_after_apply = False
        self._refresh_on_open = False
        self._refresh_menu_on_open = False
        if self.dialog is not None:
            self.dialog.set_capture_target("")
        self.video.disconnect_capture(refresh_devices=refresh_devices)
        self._render()

    def refresh_video_devices(self) -> None:
        """Refresh only when settings and acquisition transitions permit new work."""
        if self._can_start_video:
            self.video.refresh_devices()

    @Slot()
    def _open_video_menu(self) -> None:
        # aboutToShow runs before isVisible becomes true; ready requests start now.
        self._refresh_menu_on_open = not self.ready or self.operation is not None
        if not self._refresh_menu_on_open:
            self.refresh_video_devices()

    @Slot()
    def _close_video_menu(self) -> None:
        self._refresh_menu_on_open = False

    def _refresh_open_video_menu(self) -> None:
        if not self._refresh_menu_on_open:
            return
        if not self.window.video_menu.isVisible() or self.committed or self.pending is not None:
            self._refresh_menu_on_open = False
        elif self.ready and self.operation is None:
            self._refresh_menu_on_open = False
            self.refresh_video_devices()

    @Slot(str)
    def _select_video_device(self, path: str) -> None:
        self.apply_settings(replace(self.baseline, capture_device=path))
        self._render()

    @Slot()
    def _video_changed(self) -> None:
        self._update_capture_devices()
        self._finish_settings_apply()
        self._render()
        if self.committed:
            self._finish_shutdown()

    @Slot()
    def _update_capture_devices(self) -> None:
        self._render_capture_menu()
        if self.dialog is not None:
            self.dialog.set_capture_devices(
                self.video.devices,
                checked=self.video.devices_checked,
                searching=self.video.discovering,
                error=self.video.discovery_error,
            )

    def _render_capture_menu(self) -> None:
        self.window.set_capture_devices(
            self.video.devices,
            self._active_capture_device,
            checked=self.video.devices_checked,
            searching=self.video.discovering,
            error=self.video.discovery_error,
            enabled=self._can_start_video and not self.dirty and self.confirmation is None,
        )

    def connect_codex(self) -> None:
        """Connect using the last successfully loaded or saved settings snapshot."""
        if not self.ready or self.committed or self.pending is not None:
            return
        options = ConnectionOptions(
            (self.path.parent / "runtime").resolve(),
            self.baseline.model,
            self.baseline.effort,
            self.baseline.auth_method,
        )
        self.window.connection.activate(self.service.connect_runtime(options))

    @Slot(object)
    def _answer_codex(self, submission: object) -> None:
        if isinstance(submission, AnswerSubmission) and not self.committed:
            self.service.answer(
                submission.token, decision=submission.decision, answers=submission.answers
            )

    def open_settings(self) -> None:
        """Show the single settings editor without discarding its input."""
        if self.committed or self.pending == "app":
            return
        newly_shown = self.dialog is None or not self.dialog.isVisible()
        if self.dialog is None:
            self.dialog = SettingsDialog(self.path, self.window)
            self.dialog.set_values(self.baseline)
            self.dialog.set_capture_target(self._active_capture_device)
            self.dialog.save_button.clicked.connect(self._apply_editor)
            self.dialog.ok_button.clicked.connect(lambda: self.apply_settings(close_after=True))
            self.dialog.changed.connect(self._render)
            self.dialog.close_requested.connect(self._cancel_settings)
            self.dialog.capture_refresh_requested.connect(self.refresh_video_devices)
            self.dialog.error_details_button.clicked.connect(self._show_settings_error)
        elif newly_shown:
            self.dialog.set_values(self.baseline)
            self.dialog.set_capture_target(self._active_capture_device)
        self._update_capture_devices()
        self._render()
        self.dialog.show()
        fit_to_screen(self.dialog)
        self.dialog.raise_()
        self.dialog.activateWindow()
        if newly_shown:
            self._refresh_on_open = True
        self._refresh_open_settings()

    def _open_settings_location(self) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path.parent))):
            self.window.notifications.publish(
                Notice(
                    "settings",
                    "設定フォルダーを開けない",
                    "設定フォルダーを開けませんでした。パスとフォルダーの存在を確認してください。",
                    str(self.path.parent),
                )
            )

    def _show_settings_error(self) -> None:
        if self.error and self._settings_error_notice is not None:
            self.window.notifications.show_snapshot(self._settings_error_notice)
        else:
            self.window.notifications.show_details("settings" if self.error else "video")

    def _refresh_open_settings(self) -> None:
        if not self._refresh_on_open:
            return
        if (
            self.dialog is None
            or not self.dialog.isVisible()
            or self.committed
            or self.pending is not None
        ):
            self._refresh_on_open = False
            return
        if self.operation is not None or not self.ready:
            return
        self._refresh_on_open = False
        self.refresh_video_devices()

    def apply_settings(
        self, settings: Settings | None = None, *, close_after: bool = False
    ) -> bool:
        """Save changed settings and reconcile the explicitly requested capture target.

        Args:
            settings: Menu-selected snapshot, rejected while the editor is dirty.
                An empty device requests disconnection without forgetting the saved
                device. None uses the editor's separate target, or the active target.
            close_after: Close the settings dialog only after saving and any capture
                transition succeed. Any failure or explicit stop discards this intent.

        Returns:
            Whether applying was accepted; capture completion is reported separately.
        """
        if not self._can_start_video or self.confirmation is not None:
            return False
        if settings is not None and self.dirty:
            return False
        self._applying_settings = True
        self._close_after_apply = close_after
        self.error = ""
        self._settings_error_notice = None
        target = (
            settings
            if settings is not None
            else (self.dialog.values() if self.dialog is not None else self.baseline)
        )
        path = (
            settings.capture_device
            if settings is not None
            else self.dialog.capture_target
            if self.dialog is not None
            else self._active_capture_device
        )
        if settings is not None and not path:
            target = replace(target, capture_device=self.baseline.capture_device)
        capture_target = replace(target, capture_device=path)
        self._capture_after_save = capture_target if self._capture_differs(capture_target) else None
        self._refresh_on_open = False
        self._refresh_menu_on_open = False
        self.video.cancel_pending()
        if target != self.baseline or not self.path.exists():
            self._request("save", settings=target)
        else:
            capture_target, self._capture_after_save = self._capture_after_save, None
            if capture_target is not None:
                self.video.apply_capture(capture_target)
            self._finish_settings_apply()
            self._render()
        return True

    def _finish_settings_apply(self) -> None:
        if (
            not self._applying_settings
            or self.operation is not None
            or self._capture_after_save is not None
            or self.video.applying
        ):
            return
        close = (
            self._close_after_apply
            and not self.error
            and self.video.apply_state != "failed"
            and not self.video.cleanup_unconfirmed
        )
        self._applying_settings = False
        self._close_after_apply = False
        if close:
            self._hide_settings()

    def _hide_settings(self) -> None:
        self._refresh_on_open = False
        if self.dialog is not None:
            self.dialog.set_values(self.baseline)
            self.dialog.set_capture_target(self._active_capture_device)
            self.dialog.hide()

    def _cancel_settings(self) -> None:
        if (
            self.operation != "save"
            and self.pending is None
            and not self.committed
            and not self.video.applying
            and not self._applying_settings
        ):
            self._close_after_apply = False
            self._hide_settings()
            self._render()

    @property
    def _active_capture_device(self) -> str:
        active = self.video.active_settings
        return active.capture_device if active is not None and self.video.has_connection else ""

    def _capture_differs(self, target: Settings) -> bool:
        active = self.video.active_settings if self.video.has_connection else None
        if self.video.cleanup_unconfirmed:
            return True
        if not target.capture_device:
            return active is not None
        return active is None or (
            target.capture_device,
            target.capture_fps,
            target.capture_resolution,
        ) != (active.capture_device, active.capture_fps, active.capture_resolution)

    @property
    def _editor_capture_differs(self) -> bool:
        return self.dialog is not None and self._capture_differs(
            replace(self.dialog.values(), capture_device=self.dialog.capture_target)
        )

    @Slot()
    def _apply_editor(self) -> None:
        self.apply_settings()

    def save(self) -> None:
        """Save the current settings through the worker."""
        if self.ready and not self.video.applying:
            self._request("save")

    def change_window_size(self, key: str) -> None:
        """Save a menu selection without overwriting any settings editor changes."""
        if (
            self.ready
            and self.operation is None
            and self.pending is None
            and not self.committed
            and not self.dirty
            and not self.video.applying
            and key != self.baseline.window_size_preset
        ):
            self._request("save", settings=replace(self.baseline, window_size_preset=key))

    def reload(self) -> None:
        """Reload settings through the worker."""
        if (
            self.operation is not None
            or self.pending is not None
            or self.committed
            or self.video.applying
        ):
            return
        if self.dirty:
            self._close("reload")
        else:
            self._request("load")

    def _request(
        self, operation: str, *, continuing: bool = False, settings: Settings | None = None
    ) -> None:
        if (
            self.operation is not None
            or self.committed
            or self.confirmation is not None
            or (self.pending is not None and not continuing)
        ):
            return
        self.operation = operation
        self.error = ""
        self._settings_error_notice = None
        self._render()
        snapshot = settings
        if snapshot is None:
            snapshot = self.dialog.values() if self.dialog is not None else self.baseline
        self.requested.emit(operation, snapshot)

    @Slot(object)
    def _complete(self, result: object) -> None:
        if not isinstance(result, OperationResult):
            return
        operation = self.operation
        self.operation = None
        capture_target, self._capture_after_save = self._capture_after_save, None
        if result.settings is not None:
            self.baseline = result.settings
            self.ready = True
            self._settings_saved = operation == "save"
            if self.dialog is not None:
                self.dialog.set_values(result.settings, preserve_target=operation == "save")
                if operation == "load":
                    self.dialog.set_capture_target(self._active_capture_device)
            self.video.set_preview_fps(self.baseline.preview_fps)
            self.window.video.view.set_letterbox(self.baseline.preview_letterbox)
            if self.window.current_window_size_preset != self.baseline.window_size_preset:
                self.window.apply_window_size_preset(self.baseline.window_size_preset)
        else:
            self.error = result.error
            self._applying_settings = False
            self._close_after_apply = False
            self.error_operation = "読込失敗" if operation == "load" else "保存失敗"
            explanation = (
                "設定を読み込めませんでした。設定ファイルと詳細を確認して、再読込してください。"
                if operation == "load"
                else (
                    "保存の完了を確認できず、設定を適用しませんでした。"
                    "編集内容と詳細を確認してください。"
                )
            )
            self._settings_error_notice = Notice(
                "settings", self.error_operation, explanation, result.error
            )
            self.window.notifications.publish(self._settings_error_notice)
            if operation == "load":
                self.ready = False
        if self.error and operation == "save":
            self.pending = None
            self.wait_timer.stop()
            self.wait_expired = False
        elif self.pending is not None:
            self._resolve_close()
        self._render()
        if (
            result.settings is not None
            and capture_target is not None
            and not self.committed
            and self.pending != "app"
        ):
            self.video.apply_capture(capture_target)
        self._finish_settings_apply()
        self._render()
        self._refresh_open_settings()
        self._refresh_open_video_menu()

    @property
    def dirty(self) -> bool:
        """Whether the current editor differs from the last successful snapshot."""
        return self.dialog is not None and self.dialog.values() != self.baseline

    def _render(self) -> None:
        state = {"load": "読込中", "save": "保存中"}.get(
            self.operation,
            "未保存" if self.dirty else "保存済み" if self._settings_saved else "変更なし",
        )
        if self.error:
            state = self.error_operation
        cancelable = self.pending in ("app", "dialog") and self.operation is not None
        if cancelable or self.committed:
            state = "閉じる待ち" if self.pending == "dialog" else "終了待ち"
            if self.wait_expired:
                targets = []
                if self.operation is not None:
                    targets.append(
                        f"{'設定読込' if self.operation == 'load' else '設定保存'} · {self.path}"
                    )
                else:
                    if not self.io_thread.wait(0) or not self._worker_released:
                        targets.append(f"設定終了 · {self.path}")
                    if not self.service.wait(0):
                        targets.append("Codex終了")
                    if not self.video.shutdown_complete:
                        targets.append("映像終了")
                state = " · ".join([state, *targets])
        self.window.status.setText(state if cancelable or self.committed else "")
        self.window.cancel_close_button.setVisible(cancelable)
        self.window.cancel_close_button.setText(
            "閉じる取消し" if self.pending == "dialog" else "終了取消し"
        )
        self.window.refresh_close_progress()
        self.window.settings_action.setEnabled(not self.committed and self.pending != "app")
        self.window.exit_action.setEnabled(not self.committed)
        self.window.notifications.set_settings_available(
            not self.committed and self.pending != "app"
        )
        self.window.set_window_size_state(
            self.baseline.window_size_preset,
            enabled=(
                self.ready
                and self.operation is None
                and self.pending is None
                and not self.committed
                and not self.dirty
                and not self.video.applying
            ),
        )
        self.window.connection.set_available(
            not self.committed and self.pending != "app" and not self._service_unavailable,
            settings_ready=self.ready and self.pending is None,
        )
        self.video.set_available(
            not self.committed and self.pending != "app",
            settings_ready=self._can_start_video
            and not self.dirty
            and bool(self.baseline.capture_device),
            commands_ready=self._can_start_video,
            pending_connection=self._capture_after_save is not None,
        )
        self._render_capture_menu()
        self.window.reload_action.setEnabled(
            self.operation is None
            and self.pending is None
            and not self.committed
            and not self.video.applying
        )
        if self.dialog is not None:
            message = state if self.operation or self.error else ""
            self.dialog.status.setText(message)
            self.dialog.status.setVisible(bool(message))
            if self.dialog.details.toPlainText() != self.error:
                self.dialog.details.setPlainText(self.error)
            self.dialog.details.hide()
            idle = (
                self.operation is None
                and self.pending is None
                and not self.committed
                and not self.video.applying
                and not self._applying_settings
            )
            self.dialog.set_editable(idle and self.ready)
            self.dialog.set_closing_allowed(
                idle or (self.operation == "load" and self.pending is None and not self.committed)
            )
            self.dialog.capture_refresh_button.setEnabled(
                idle and self.ready and self.video.can_refresh
            )
            self.dialog.save_button.setEnabled(
                idle
                and self.ready
                and (self.dirty or self._editor_capture_differs or not self.path.exists())
            )
            self.dialog.ok_button.setEnabled(idle and self.ready)
            device_differs = self.dialog.capture_target != self._active_capture_device
            capture_state = (
                "接続失敗"
                if self.video.apply_state == "failed" and not self.video.cleanup_unconfirmed
                else self.window.video.status.text()
                if self.video.applying or self.video.cleanup_unconfirmed
                else f"現在: {self.window.video.source.text()}"
                if device_differs and self._active_capture_device
                else "未接続"
                if device_differs
                else ""
            )
            self.dialog.capture_connection_status.setText(capture_state)
            self.dialog.capture_connection_status.setVisible(bool(capture_state))
            if self.dialog.capture_connection_error.toPlainText() != self.video.apply_error:
                self.dialog.capture_connection_error.setPlainText(self.video.apply_error)
            self.dialog.capture_connection_error.hide()
            self.dialog.error_details_button.setVisible(
                bool(self.error or self.video.discovery_error)
                or self.video.apply_state == "failed"
                or self.video.cleanup_unconfirmed
            )

    def request_close(self) -> None:
        """Interrupt the turn, then finish settings and service shutdown."""
        self._capture_after_save = None
        self._applying_settings = False
        self._close_after_apply = False
        self._refresh_on_open = False
        self._refresh_menu_on_open = False
        self.video.cancel_pending()
        if not self.committed:
            self.window.connection.interrupt()
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
            self.wait_timer.start()
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
            self.service.shutdown()
            self.video.shutdown()
            if self.io_thread.isRunning():
                self.io_thread.quit()
            else:
                if not self.io_thread.isFinished():
                    self._worker.retire()
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
    def _service_finished(self) -> None:
        if not self.committed:
            self._service_unavailable = True
            self.window.connection.service_finished()
        self._finish_shutdown()

    @Slot()
    def _settings_worker_destroyed(self) -> None:
        self._worker_released = True
        self._finish_shutdown()

    @Slot()
    def _finish_shutdown(self) -> None:
        if not self.committed or self._stopped:
            return
        failure = "\n".join(
            reason for reason in (self.video.shutdown_error, self.service.shutdown_error) if reason
        )
        if failure and failure != self._shutdown_failure:
            self._shutdown_failure = failure
            self.window.notifications.publish(
                Notice(
                    "shutdown",
                    "終了未確認",
                    "終了処理の完了を確認できないため、画面を閉じていません。詳細を確認してください。",
                    failure,
                )
            )
        if failure:
            self.window.status.setText("終了未確認")
            self.window.refresh_close_progress()
        if not self.io_thread.wait(0) or not self._worker_released or not self.service.wait(0):
            QTimer.singleShot(10, self._finish_shutdown)
            return
        if not self.video.shutdown_complete:
            if self.video.shutdown_error:
                self.window.status.setText("終了未確認")
                self.window.refresh_close_progress()
            else:
                QTimer.singleShot(10, self._finish_shutdown)
            return
        if self.service.shutdown_error:
            self.window.status.setText("終了未確認")
            self.window.refresh_close_progress()
            return
        self._stopped = True
        self.wait_timer.stop()
        if self.dialog is not None:
            self.dialog.hide()
        self.window.allow_close = True
        self.window.close()
        self.stopped.emit()
