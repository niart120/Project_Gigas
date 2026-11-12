"""Coordinate the application views and settings operations."""

from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from gigas.app.settings_worker import OperationResult, SettingsIO, SettingsWorker
from gigas.app.video import VideoController
from gigas.game_io.input.config import get_protocol, resolve_baudrate, resolve_serial_config
from gigas.game_io.input.serial import SerialPort
from gigas.runtime.codex.models import ConnectionOptions
from gigas.services.codex import CodexServiceThread, InputConnectionResult, ProjectResult
from gigas.storage.settings import Settings
from gigas.ui.confirmation import changes_confirmation
from gigas.ui.geometry import fit_to_screen
from gigas.ui.main_window import MainWindow
from gigas.ui.notifications import Notice
from gigas.ui.request_card import AnswerSubmission
from gigas.ui.settings_dialog import SettingsDialog

if TYPE_CHECKING:
    from gigas.app.video import CapturePort


@dataclass(frozen=True)
class _SettingsRequest:
    """One settings I/O submission and its post-save effects.

    Attributes:
        snapshot: Settings to save, or None for the initial load.
        capture: Apply the saved capture configuration after successful saving.
        input_apply: Apply saved input settings unless explicitly stopped before completion.
        close_dialog: Close a still-visible editor after successful saving.
    """

    snapshot: Settings | None = None
    capture: bool = False
    input_apply: bool = False
    close_dialog: bool = False


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
        self._serial_ports: tuple[SerialPort, ...] = ()
        self._serial_searching = False
        self._serial_discovery_error = ""
        self.baseline = Settings()
        self._io_request: _SettingsRequest | None = None
        self._project_pending = False
        self.ready = False
        self._settings_error_notice: Notice | None = None
        self._exit_requested = False
        self.committed = False
        self._stopped = False
        self._worker_released = False
        self._service_unavailable = False
        self._shutdown_failure = ""
        self.video = VideoController(self.window.video, capture_service, self)
        self.service = (
            CodexServiceThread(path.parent, self.video.service.reader())
            if service is None
            else service
        )
        self.video.changed.connect(self._video_changed)
        self.video.failed.connect(self.window.notifications.publish)
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
        self.window.new_project_requested.connect(self._new_project)
        self.window.open_project_requested.connect(self._open_project)
        self.window.input_menu.aboutToShow.connect(self.refresh_input_ports)
        self.window.input_port_requested.connect(self._select_input_port)
        self.window.input_protocol_requested.connect(self._select_input_protocol)
        self.window.video_menu_opened.connect(self.refresh_video_devices)
        self.window.capture_device_requested.connect(self._select_video_device)
        self.window.window_size_requested.connect(self.change_window_size)
        self.window.close_requested.connect(self.request_close)
        panel = self.window.connection
        panel.connect_requested.connect(self.connect_codex)
        panel.disconnect_requested.connect(self.service.disconnect_runtime)
        panel.login_requested.connect(self.service.login)
        panel.send_requested.connect(self.service.send)
        panel.interrupt_requested.connect(self.interrupt)
        panel.answer_requested.connect(self._answer_codex)
        self.service.received.connect(panel.handle_event)
        self.service.completed.connect(panel.handle_result)
        self.service.received.connect(self._render)
        self.service.completed.connect(self._render)
        self.service.project_completed.connect(self._project_complete)
        self.service.recording_failed.connect(self._recording_failed)
        self.service.input_ports.connect(self._input_ports_received)
        self.service.input_changed.connect(self._input_changed)
        self.service.finished.connect(self._service_finished)

    def start(self) -> None:
        """Display the window and load settings without connecting capture."""
        self.window.show()
        fit_to_screen(self.window)
        self.io_thread.start()
        self._worker.moveToThread(self.io_thread)
        self.service.start()
        self.video.start()
        self._request(_SettingsRequest())

    @property
    def operation(self) -> str | None:
        """Return the active settings I/O kind, if any."""
        request = self._io_request
        return None if request is None else "load" if request.snapshot is None else "save"

    @property
    def error(self) -> str:
        """Return the current settings failure details, if any."""
        return (
            self._settings_error_notice.details if self._settings_error_notice is not None else ""
        )

    @property
    def dirty(self) -> bool:
        """Whether the visible editor differs from the last successful settings."""
        return (
            self.dialog is not None
            and self.dialog.isVisible()
            and _effective_settings(self.dialog.values()) != _effective_settings(self.baseline)
        )

    @property
    def _can_edit(self) -> bool:
        return (
            self.ready
            and self._io_request is None
            and not self._project_pending
            and not self._exit_requested
            and not self.committed
            and self.confirmation is None
        )

    def interrupt(self) -> None:
        """Interrupt execution without cancelling submitted settings application."""
        self.service.interrupt()

    def refresh_input_ports(self) -> None:
        """Discover serial identifiers without opening them."""
        if self._can_edit and not self._serial_searching:
            self._serial_searching = True
            self._serial_discovery_error = ""
            self._update_serial_ports()
            self.service.refresh_input_ports()

    def _update_serial_ports(self) -> None:
        if self.dialog is not None:
            self.dialog.set_serial_ports(
                self._serial_ports,
                searching=self._serial_searching,
                error=self._serial_discovery_error,
            )

    @Slot(object, str)
    def _input_ports_received(self, ports: object, error: str) -> None:
        self._serial_searching = False
        self._serial_discovery_error = error
        if not error and isinstance(ports, tuple):
            self._serial_ports = tuple(port for port in ports if isinstance(port, SerialPort))
        if error:
            self.window.notifications.publish(
                Notice(
                    "input",
                    "列挙失敗",
                    "入力ポートを取得できませんでした。一覧更新で再試行してください。",
                    error,
                )
            )
        self._update_serial_ports()
        self._render()

    @Slot(object)
    def _input_changed(self, result: InputConnectionResult) -> None:
        if result.error:
            self.window.notifications.publish(
                Notice(
                    "input",
                    "接続失敗",
                    "入力接続を変更できませんでした。設定を確認して再度適用してください。",
                    result.error,
                )
            )
        self._render()

    def _select_input_port(self, port: str) -> None:
        self.apply_settings(
            replace(self.baseline, serial_port=port), capture=False, input_apply=True
        )

    def _select_input_protocol(self, protocol: str) -> None:
        self.apply_settings(
            replace(
                self.baseline,
                serial_protocol=protocol,
                serial_baudrate=(
                    self.baseline.serial_baudrate
                    if protocol == self.baseline.serial_protocol
                    else get_protocol(protocol).default_baudrate
                ),
            ),
            capture=False,
            input_apply=True,
        )

    def refresh_video_devices(self) -> None:
        """Discover now if possible, without reserving a later refresh."""
        if self._can_edit:
            self.video.refresh_devices()

    @Slot(str)
    def _select_video_device(self, path: str) -> None:
        if self.dialog is None or not self.dialog.isVisible():
            self.apply_settings(
                replace(self.baseline, capture_device=path), capture=True, input_apply=False
            )
        self._render()

    @Slot()
    def _video_changed(self) -> None:
        self._update_capture_devices()
        self._render()
        if self.committed:
            self._finish_shutdown()

    @Slot()
    def _update_capture_devices(self) -> None:
        if self.dialog is not None and self.dialog.isVisible():
            self.dialog.set_capture_devices(
                self.video.devices,
                searching=self.video.discovering,
                error=self.video.discovery_error,
            )

    def _render_capture_menu(self) -> None:
        self.window.set_capture_devices(
            self.video.devices,
            self.baseline.capture_device,
            searching=self.video.discovering,
            error=self.video.discovery_error,
            enabled=self._can_edit and (self.dialog is None or not self.dialog.isVisible()),
        )

    def connect_codex(self) -> None:
        """Connect using the last successfully loaded or saved settings snapshot."""
        if not self.ready or self.committed or self._exit_requested or self._project_pending:
            return
        options = ConnectionOptions(
            (self.path.parent / "runtime").resolve(),
            self.baseline.model,
            self.baseline.effort,
            self.baseline.auth_method,
        )
        self.window.connection.activate(self.service.connect_runtime(options))
        self._render()

    @Slot()
    def _new_project(self) -> None:
        if not self.window.project_menu.isEnabled():
            return
        dialog = QFileDialog(self.window, "プロジェクトを新規作成", str(self.path.parent))
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setLabelText(QFileDialog.DialogLabel.Accept, "作成")
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setOption(QFileDialog.Option.DontConfirmOverwrite)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._select_project(Path(dialog.selectedFiles()[0]), create=True)
        dialog.deleteLater()

    @Slot()
    def _open_project(self) -> None:
        if not self.window.project_menu.isEnabled():
            return
        selected = QFileDialog.getExistingDirectory(
            self.window, "プロジェクトを開く", str(self.path.parent)
        )
        if selected:
            self._select_project(Path(selected))

    def _select_project(self, path: Path, *, create: bool = False) -> None:
        self._project_pending = True
        self._render()
        if create:
            self.service.create_project(path)
        else:
            self.service.open_project(path)

    @Slot(object)
    def _project_complete(self, result: object) -> None:
        if not isinstance(result, ProjectResult):
            return
        self._project_pending = False
        if result.error:
            self.window.notifications.publish(
                Notice(
                    "settings",
                    "プロジェクト選択失敗",
                    "プロジェクトを選択できませんでした。保存先と詳細を確認して、"
                    "「プロジェクト」から選び直してください。",
                    f"{result.path}\n{result.error}",
                )
            )
        else:
            self.window.setWindowTitle(f"GIGAS · {result.path}")
            if str(result.path) != self.baseline.last_project_path:
                self._request(
                    _SettingsRequest(replace(self.baseline, last_project_path=str(result.path)))
                )
        if self._exit_requested and self._io_request is None:
            self._resolve_close()
        self._render()

    @Slot(str, str, str)
    def _recording_failed(self, identifier: str, path: str, reason: str) -> None:
        self.window.notifications.publish(
            Notice(
                "recording",
                "記録失敗",
                "記録処理に失敗しました。保存先と詳細を確認してください。",
                f"{identifier}\n{path}\n{reason}",
            )
        )

    @Slot(object)
    def _answer_codex(self, submission: object) -> None:
        if isinstance(submission, AnswerSubmission) and not self.committed:
            self.service.answer(
                submission.token, decision=submission.decision, answers=submission.answers
            )

    def open_settings(self) -> None:
        """Open one modal editor from the saved snapshot when I/O is idle."""
        if (
            self.committed
            or self._exit_requested
            or self._io_request is not None
            or self._project_pending
        ):
            return
        if self.dialog is None:
            self.dialog = SettingsDialog(self.path, self.window)
            self.dialog.save_button.clicked.connect(lambda _checked: self.apply_settings())
            self.dialog.ok_button.clicked.connect(lambda: self.apply_settings(close_after=True))
            self.dialog.changed.connect(self._render)
            self.dialog.close_requested.connect(self._cancel_settings)
            self.dialog.capture_refresh_requested.connect(self.refresh_video_devices)
            self.dialog.serial_refresh_requested.connect(self.refresh_input_ports)
            self.dialog.error_details_button.clicked.connect(self._show_settings_error)
            self.dialog.open_folder_button.clicked.connect(self._open_settings_location)
        if self.dialog.isVisible():
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        self.dialog.set_values(self.baseline)
        self.dialog.show()
        self._update_capture_devices()
        self._update_serial_ports()
        self._render()
        fit_to_screen(self.dialog)
        self.dialog.raise_()
        self.dialog.activateWindow()
        self.refresh_video_devices()
        self.refresh_input_ports()

    def _open_settings_location(self) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path.parent))):
            notice = Notice(
                "settings",
                "設定フォルダーを開けない",
                "設定フォルダーを開けませんでした。パスとフォルダーの存在を確認してください。",
                str(self.path.parent),
            )
            self.window.notifications.publish(notice)
            self.window.notifications.show_snapshot(notice, parent=self.dialog)

    def _show_settings_error(self) -> None:
        if self._settings_error_notice is not None:
            self.window.notifications.show_snapshot(self._settings_error_notice, parent=self.dialog)
        else:
            self.window.notifications.show_details(parent=self.dialog)

    def _cancel_settings(self) -> None:
        if self.dialog is not None and not self.committed and not self._exit_requested:
            self.window.notifications.close_details()
            self.dialog.hide()
            self.dialog.set_values(self.baseline)
            self._render()

    def apply_settings(
        self,
        settings: Settings | None = None,
        *,
        close_after: bool = False,
        capture: bool | None = None,
        input_apply: bool | None = None,
    ) -> bool:
        """Save and apply one snapshot without waiting for capture success.

        Args:
            capture: Override capture application for a device-menu selection.
            input_apply: Override input application for a device-menu selection.
            settings: Explicit snapshot, or None for the visible editor's values.
            close_after: Close the visible editor after saving succeeds, including
                when capture subsequently fails. Saving failure keeps edits visible.

        Returns:
            Whether the request was accepted. Capture completion is independent.
        """
        if not self._can_edit:
            return False
        target = (
            settings
            if settings is not None
            else (
                self.dialog.values()
                if self.dialog is not None and self.dialog.isVisible()
                else self.baseline
            )
        )
        before, after = _effective_settings(self.baseline), _effective_settings(target)
        changed = {
            field.name
            for field in fields(Settings)
            if getattr(after, field.name) != getattr(before, field.name)
        }
        serial_fields = {"serial_port", "serial_protocol", "serial_baudrate"}
        request = _SettingsRequest(
            target,
            capture=(not changed or bool(changed - serial_fields - {"observation_resolution"}))
            if capture is None
            else capture,
            input_apply=(not changed or bool(changed & serial_fields))
            if input_apply is None
            else input_apply,
            close_dialog=close_after,
        )
        self._settings_error_notice = None
        if target != self.baseline or not self.path.exists():
            self._request(request)
        else:
            self._apply_saved(request, target)
        return True

    def change_window_size(self, key: str) -> None:
        """Save and apply a display preset without initiating video capture."""
        if (
            self._can_edit
            and (self.dialog is None or not self.dialog.isVisible())
            and key != self.baseline.window_size_preset
        ):
            self._request(_SettingsRequest(replace(self.baseline, window_size_preset=key)))

    def _request(self, request: _SettingsRequest) -> None:
        if self._io_request is not None or self.committed:
            return
        self._io_request = request
        self._settings_error_notice = None
        self._render()
        self.requested.emit(self.operation, request.snapshot or self.baseline)

    def _apply_saved(self, request: _SettingsRequest, settings: Settings) -> None:
        self.baseline = settings
        self.ready = True
        if self.dialog is not None and self.dialog.isVisible():
            self.dialog.set_values(settings)
        self.video.set_preview_fps(settings.preview_fps)
        self.service.set_observation_resolution(settings.observation_resolution)
        self.window.video.view.set_letterbox(settings.preview_letterbox)
        if self.window.current_window_size_preset != settings.window_size_preset:
            self.window.apply_window_size_preset(settings.window_size_preset)
        if request.capture and not self._exit_requested and not self.committed:
            self.video.apply_capture(settings)
        if request.input_apply and not self._exit_requested and not self.committed:
            config = resolve_serial_config(
                settings.serial_port, settings.serial_protocol, settings.serial_baudrate
            )
            self.service.apply_input(config)
        if request.close_dialog and self.dialog is not None:
            self.window.notifications.close_details()
            self.dialog.hide()
        if (
            request.snapshot is None
            and settings.last_project_path
            and not self._exit_requested
            and not self.committed
        ):
            self._select_project(Path(settings.last_project_path))
        self._render()

    @Slot(object)
    def _complete(self, result: object) -> None:
        if not isinstance(result, OperationResult) or self._io_request is None:
            return
        request, self._io_request = self._io_request, None
        if result.settings is not None:
            self._apply_saved(request, result.settings)
        else:
            title = "読込失敗" if request.snapshot is None else "保存失敗"
            if request.snapshot is None:
                self.ready = False
                explanation = (
                    "設定を読み込めませんでした。設定ファイルを修正して再起動してください。"
                )
            else:
                explanation = (
                    "プロジェクトは選択済みですが、次回起動時の復元先を保存できませんでした。"
                    "設定ファイルと詳細を確認してから、プロジェクトを選び直してください。"
                    if request.snapshot.last_project_path != self.baseline.last_project_path
                    else "設定を保存できず、適用しませんでした。編集内容と詳細を確認してください。"
                )
                if request.capture and not request.snapshot.capture_device:
                    explanation += "映像取得は停止していません。"
                self._exit_requested = False
                self.wait_timer.stop()
                self.wait_expired = False
            self._settings_error_notice = Notice("settings", title, explanation, result.error)
            self.window.notifications.publish(self._settings_error_notice)
        if self._exit_requested and not self._project_pending:
            self._resolve_close()
        self._render()

    @Slot()
    def _render(self) -> None:
        closing = self._exit_requested or self.committed
        state = "終了待ち" if closing else ""
        if closing and self.wait_expired:
            targets = []
            if self.operation is not None:
                targets.append(f"設定I/O · {self.path}")
            else:
                if not self.io_thread.wait(0) or not self._worker_released:
                    targets.append("設定終了")
                if not self.service.wait(0):
                    targets.append("Codex終了")
                if not self.video.shutdown_complete:
                    targets.append("映像終了")
            state = " · ".join([state, *targets])
        self.window.status.setText(state)
        self.window.refresh_close_progress()
        self.window.settings_action.setEnabled(
            not closing and self._io_request is None and not self._project_pending
        )
        self.window.exit_action.setEnabled(not self.committed)
        self.window.set_window_size_state(
            self.baseline.window_size_preset,
            enabled=self._can_edit and (self.dialog is None or not self.dialog.isVisible()),
        )
        self.window.connection.set_available(
            not closing and not self._service_unavailable,
            settings_ready=self.ready and not closing and not self._project_pending,
        )
        self.window.project_menu.setEnabled(
            self._can_edit
            and self.window.connection.connect_action.isEnabled()
            and (self.dialog is None or not self.dialog.isVisible())
        )
        self.video.set_available(not closing)
        self._render_capture_menu()
        self.window.set_input_choices(
            self._serial_ports,
            self.baseline.serial_port,
            self.baseline.serial_protocol,
            enabled=self._can_edit and (self.dialog is None or not self.dialog.isVisible()),
        )
        if self.dialog is not None and self.dialog.isVisible():
            message = (
                self._settings_error_notice.title
                if self._settings_error_notice is not None
                else "保存中"
                if self.operation == "save"
                else ""
            )
            self.dialog.status.setText(message)
            self.dialog.status.setVisible(bool(message))
            self.dialog.set_editable(self._can_edit)
            self.dialog.capture_refresh_button.setEnabled(self._can_edit and self.video.can_refresh)
            self.dialog.save_button.setEnabled(self._can_edit)
            self.dialog.ok_button.setEnabled(self._can_edit)
            self.dialog.close_button.setEnabled(not closing)
            self.dialog.error_details_button.setVisible(
                bool(
                    self.error
                    or self.video.discovery_error
                    or self._serial_discovery_error
                    or "input" in self.window.notifications.notices
                )
                or self.video.apply_state == "failed"
                or self.video.cleanup_unconfirmed
            )

    def request_close(self) -> None:
        """Interrupt Codex and wait for submitted I/O before confirming shutdown."""
        if self.committed or self._exit_requested:
            return
        self.interrupt()
        self.video.cancel_pending()
        self._exit_requested = True
        if self._io_request is None and not self._project_pending:
            self._resolve_close()
        else:
            self.wait_timer.start()
        self._render()

    def _resolve_close(self) -> None:
        self.wait_timer.stop()
        self.wait_expired = False
        if self.dirty:
            parent = self.dialog if self.dialog is not None else self.window
            self.confirmation = changes_confirmation(parent, allow_save=self.ready)
            self.confirmation.finished.connect(self._confirmed)
            self.confirmation.show()
        else:
            self._commit_close()

    @Slot(int)
    def _confirmed(self, result: int) -> None:
        box, self.confirmation = self.confirmation, None
        if box is not None:
            box.deleteLater()
        if result == QMessageBox.StandardButton.Save and self.ready and self.dialog is not None:
            self._request(_SettingsRequest(self.dialog.values()))
            self.wait_timer.start()
        elif result == QMessageBox.StandardButton.Discard:
            self._commit_close()
        else:
            self._exit_requested = False
        self._render()

    def _commit_close(self) -> None:
        self.committed = True
        if self.dialog is not None:
            self.dialog.hide()
        self.window.notifications.close_details()
        self.wait_timer.start()
        self.service.shutdown()
        self.video.shutdown()
        if self.io_thread.isRunning():
            self.io_thread.quit()
        else:
            if not self.io_thread.isFinished():
                self._worker.retire()
            self._finish_shutdown()

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


def _effective_settings(settings: Settings) -> Settings:
    return replace(
        settings,
        serial_baudrate=resolve_baudrate(
            get_protocol(settings.serial_protocol), settings.serial_baudrate
        ),
    )
