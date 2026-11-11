from collections.abc import Iterator
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from gigas.app.settings_worker import SettingsIO
from gigas.app.shell import ApplicationShell
from gigas.storage.settings import Settings, SettingsError, load_settings, save_settings


class ControlledIO:
    """Block selected I/O until the test releases it."""

    def __init__(self) -> None:
        """Create synchronization events and counters."""
        self.entered = Event()
        self.release = Event()
        self.fail = False
        self.block_load = False
        self.saves = 0
        self.loads = 0
        self.thread: QThread | None = None

    def load(self, path: Path) -> Settings:
        """Read through M1, optionally waiting for the test's release."""
        self.loads += 1
        if self.block_load:
            self._wait()
        return load_settings(path)

    def save(self, path: Path, settings: Settings) -> None:
        """Wait for release, then save through M1 or inject a failure."""
        self.saves += 1
        self._wait()
        if self.fail:
            message = "injected failure"
            raise SettingsError(message)
        save_settings(path, settings)

    def _wait(self) -> None:
        self.thread = QThread.currentThread()
        self.entered.set()
        if not self.release.wait(10):
            message = "test did not release I/O"
            raise TimeoutError(message)


@pytest.fixture
def controlled(qtbot: QtBot, tmp_path: Path) -> Iterator[tuple[ApplicationShell, ControlledIO]]:
    io = ControlledIO()
    shell = ApplicationShell(tmp_path / "settings.toml", SettingsIO(io.load, io.save))
    shell.start()
    qtbot.waitUntil(lambda: shell.operation is None)
    shell.open_settings()
    yield shell, io
    io.release.set()
    qtbot.waitUntil(lambda: shell.operation is None)
    if shell.window.isVisible():
        if shell.dialog is not None:
            shell.dialog.set_values(shell.baseline)
        if shell.confirmation is not None:
            shell.confirmation.reject()
        shell.window.close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())


def test_slow_io_keeps_gui_responsive_and_rejects_duplicate_requests(
    controlled: tuple[ApplicationShell, ControlledIO], qtbot: QtBot
) -> None:
    shell, io = controlled
    assert shell.dialog is not None
    shell.dialog.model.setText("snapshot")
    shell.dialog.save_button.click()
    qtbot.waitUntil(io.entered.is_set)
    pulse = Event()
    QTimer.singleShot(0, pulse.set)
    qtbot.waitUntil(pulse.is_set)
    assert io.thread != QThread.currentThread()
    assert shell.dialog.model.isReadOnly()
    assert not shell.dialog.save_button.isEnabled()
    assert not shell.window.reload_action.isEnabled()
    shell.save()
    shell.reload()
    io.release.set()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert (io.loads, io.saves) == (1, 1)
    assert shell.baseline.model == "snapshot"
    assert not shell.dialog.model.isReadOnly()


@pytest.mark.parametrize("target", ["dialog", "app"])
@pytest.mark.parametrize("fail", [False, True])
def test_pending_close_follows_save_result(
    controlled: tuple[ApplicationShell, ControlledIO], qtbot: QtBot, target: str, fail: bool
) -> None:
    shell, io = controlled
    io.fail = fail
    assert shell.dialog is not None
    shell.dialog.model.setText("edited")
    if target == "dialog":
        shell.dialog.ok_button.click()
    else:
        shell.save()
    qtbot.waitUntil(io.entered.is_set)
    if target == "app":
        shell.window.close()
        shell.window.close()
    else:
        shell.dialog.close()
        shell.dialog.close()
    assert shell.window.isVisible()
    assert shell.dialog.isVisible()
    io.release.set()
    qtbot.waitUntil(lambda: shell.operation is None)
    if fail:
        assert shell.pending is None
        assert shell.window.isVisible()
        assert shell.dialog.isVisible()
        assert not shell.dialog.model.isReadOnly()
        assert shell.dialog.save_button.isEnabled()
        assert shell.dialog.model.text() == "edited"
    else:
        dialog = shell.dialog
        qtbot.waitUntil(lambda: not dialog.isVisible())
        assert shell.window.isVisible() == (target == "dialog")
    assert io.saves == 1


def test_close_during_reload_finishes_after_result(
    controlled: tuple[ApplicationShell, ControlledIO], qtbot: QtBot
) -> None:
    shell, io = controlled
    io.block_load = True
    shell.reload()
    qtbot.waitUntil(io.entered.is_set)
    shell.window.close()
    assert shell.window.isVisible()
    io.release.set()
    qtbot.waitUntil(lambda: not shell.window.isVisible())
    assert shell.baseline == Settings()


@pytest.mark.parametrize("operation", ["save", "load"])
def test_cancel_close_keeps_late_result_without_exiting(
    controlled: tuple[ApplicationShell, ControlledIO], qtbot: QtBot, operation: str
) -> None:
    shell, io = controlled
    assert shell.dialog is not None
    if operation == "save":
        shell.dialog.model.setText("late")
        shell.save()
    else:
        save_settings(shell.path, Settings(model="late"))
        io.block_load = True
        shell.reload()
    qtbot.waitUntil(io.entered.is_set)
    shell.window.close()
    assert shell.window.cancel_close_button.isVisible()
    shell.wait_timer.timeout.emit()
    assert str(shell.path) in shell.window.status.text()
    shell.window.cancel_close_button.click()
    assert not shell.window.cancel_close_button.isVisible()
    assert shell.operation == operation
    shell.save()
    shell.reload()
    io.release.set()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.window.isVisible()
    assert shell.baseline.model == "late"
    assert shell.dialog.model.text() == "late"
    assert (io.saves, io.loads) == ((1, 1) if operation == "save" else (0, 2))


def test_shutdown_commit_waits_for_thread_cleanup_and_cannot_be_cancelled(
    controlled: tuple[ApplicationShell, ControlledIO], qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    shell, _io = controlled
    cleanup_allowed = Event()
    wait_called = Event()
    real_wait = shell.io_thread.wait

    def delayed_wait(milliseconds: int) -> bool:
        assert milliseconds == 0
        wait_called.set()
        return cleanup_allowed.is_set() and real_wait(milliseconds)

    monkeypatch.setattr(shell.io_thread, "wait", delayed_wait)
    try:
        shell.window.close()
        qtbot.waitUntil(wait_called.is_set)
        assert shell.committed
        qtbot.waitUntil(shell.io_thread.isFinished)
        assert shell.window.isVisible()
        assert not shell.window.cancel_close_button.isVisible()
        shell.cancel_close()
        assert shell.committed
        assert not shell.window.settings_action.isEnabled()
        pulse = Event()
        QTimer.singleShot(0, pulse.set)
        qtbot.waitUntil(pulse.is_set)
        assert shell.window.isVisible()
    finally:
        cleanup_allowed.set()
        qtbot.waitUntil(lambda: not shell.window.isVisible())
    assert real_wait(0)


def test_save_from_close_confirmation_starts_wait_timer(
    controlled: tuple[ApplicationShell, ControlledIO], qtbot: QtBot
) -> None:
    shell, io = controlled
    assert shell.dialog is not None
    shell.dialog.model.setText("saved-on-close")
    shell.window.close()
    assert shell.confirmation is not None
    shell.confirmation.button(QMessageBox.StandardButton.Save).click()
    qtbot.waitUntil(io.entered.is_set)
    assert shell.wait_timer.isActive()
    shell.window.cancel_close_button.click()
    io.release.set()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.window.isVisible()
    assert load_settings(shell.path).model == "saved-on-close"


def test_long_wait_target_keeps_closure_controls_inside_screen(
    controlled: tuple[ApplicationShell, ControlledIO], qtbot: QtBot
) -> None:
    shell, io = controlled
    # No filesystem operation uses this synthetic long path.
    shell.path = Path("C:/") / ("長い保存先" * 100) / "settings.toml"
    io.block_load = True
    shell.reload()
    qtbot.waitUntil(io.entered.is_set)
    shell.window.close()
    shell.wait_timer.timeout.emit()
    laid_out = Event()
    QTimer.singleShot(0, laid_out.set)
    qtbot.waitUntil(laid_out.is_set)
    assert shell.window.screen().availableGeometry().contains(shell.window.frameGeometry())
    assert shell.dialog is not None
    assert shell.dialog.screen().availableGeometry().contains(shell.dialog.frameGeometry())
    assert shell.window.cancel_close_button.isVisible()
    assert shell.window.close_progress.isVisible()
    assert shell.window.close_progress.rect().contains(shell.window.cancel_close_button.geometry())
    shell.cancel_close()


def test_settings_worker_is_destroyed_on_gui_before_shutdown_completes(
    qtbot: QtBot, tmp_path: Path
) -> None:
    entered = Event()
    release = Event()
    events: list[tuple[str, QThread]] = []
    gui_thread = QThread.currentThread()

    def save(path: Path, settings: Settings) -> None:
        entered.set()
        if not release.wait(5):
            message = "save was not released"
            raise TimeoutError(message)
        save_settings(path, settings)
        events.append(("saved", QThread.currentThread()))

    def destroyed() -> None:
        events.append(("destroyed", QThread.currentThread()))

    shell = ApplicationShell(tmp_path / "settings.toml", SettingsIO(load_settings, save))
    shell._worker.destroyed.connect(destroyed, Qt.ConnectionType.DirectConnection)
    shell.stopped.connect(lambda: events.append(("stopped", QThread.currentThread())))
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        shell.open_settings()
        assert shell.dialog is not None
        shell.dialog.model.setText("saved before shutdown")
        shell.save()
        qtbot.waitUntil(entered.is_set)
        shell.request_close()
        pulse = Event()
        QTimer.singleShot(0, pulse.set)
        qtbot.waitUntil(pulse.is_set)
        assert shell.window.isVisible()
        assert not events
        release.set()
        qtbot.waitUntil(lambda: not shell.window.isVisible())
        assert [name for name, _thread in events] == ["saved", "destroyed", "stopped"]
        assert events[0][1] != gui_thread
        assert events[1][1] == gui_thread
        assert events[2][1] == gui_thread
        assert load_settings(shell.path).model == "saved before shutdown"
        assert shell.io_thread.wait(0)
    finally:
        release.set()
        qtbot.waitUntil(lambda: shell.operation is None)
        if shell.window.isVisible():
            if shell.dialog is not None:
                shell.dialog.set_values(shell.baseline)
            shell.request_close()
            qtbot.waitUntil(lambda: not shell.window.isVisible())
