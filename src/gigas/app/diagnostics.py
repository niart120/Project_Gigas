"""Bridge diagnostic persistence failures and shutdown into the Qt event loop."""

import logging
import platform
import sys
import threading
from pathlib import Path
from threading import Thread
from types import TracebackType
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, Signal, Slot

from gigas import __version__
from gigas.storage.diagnostics import DiagnosticSession
from gigas.ui.notifications import Notice

if TYPE_CHECKING:
    from gigas.ui.notifications import NotificationCenter

logger = logging.getLogger(__name__)


class ApplicationDiagnostics(QObject):
    """Keep file I/O and failure delivery outside the GUI's execution paths."""

    failed = Signal(str, str)
    stopped = Signal()

    def __init__(self, path: Path) -> None:
        """Start logging before application services are constructed.

        Args:
            path: Application settings path.
        """
        super().__init__()
        self._notifications: NotificationCenter | None = None
        self._worker: Thread | None = None
        self._sys_hook = sys.excepthook
        self._thread_hook = threading.excepthook
        self.failed.connect(self._report_failure, Qt.ConnectionType.QueuedConnection)
        self.session = DiagnosticSession(path, lambda p, r: self.failed.emit(str(p), r))
        self.session.start()
        logger.info(
            "Application starting",
            extra={
                "event": "app.starting",
                "gigas_version": __version__,
                "python_version": platform.python_version(),
                "os_version": platform.platform(),
            },
        )

    def install_exception_hooks(self) -> None:
        """Capture uncaught GUI and Python-thread exceptions for the process entry point."""
        sys.excepthook = self._uncaught
        threading.excepthook = self._thread_uncaught

    def _uncaught(
        self, kind: type[BaseException], error: BaseException, trace: TracebackType | None
    ) -> None:
        logger.critical(
            "Uncaught application exception",
            exc_info=(kind, error, trace),
            extra={"event": "app.uncaught_exception"},
        )

    def _thread_uncaught(self, args: threading.ExceptHookArgs) -> None:
        if args.exc_type is not SystemExit and args.exc_value is not None:
            self._uncaught(args.exc_type, args.exc_value, args.exc_traceback)

    def attach(self, notifications: "NotificationCenter") -> None:
        """Attach the window's existing notification center before processing events.

        Args:
            notifications: GUI-owned notification destination.
        """
        self._notifications = notifications

    @Slot(str, str)
    def _report_failure(self, path: str, reason: str) -> None:
        if self._notifications is not None:
            self._notifications.publish(
                Notice(
                    "logging",
                    "診断ログ保存失敗",
                    "診断ログを保存できません。ゲーム操作は継続します。"
                    "保存先の空き容量と書き込み権限を確認してください。",
                    f"{path}\n{reason}",
                )
            )

    @Slot()
    def finish(self) -> None:
        """Drain logging in a worker after application services have stopped."""
        if self._worker is not None:
            return
        logger.info("Application services stopped", extra={"event": "app.stopped"})
        self._worker = Thread(target=self._close, name="gigas-log-close")
        self._worker.start()

    def _close(self) -> None:
        try:
            self.session.close()
        finally:
            self.stopped.emit()

    def join(self) -> None:
        """Release the worker after the Qt event loop has exited."""
        if self._worker is not None:
            self._worker.join()
        else:
            self.session.close()
        if sys.excepthook == self._uncaught:
            sys.excepthook = self._sys_hook
        if threading.excepthook == self._thread_uncaught:
            threading.excepthook = self._thread_hook
