"""Qt process entry point."""

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from gigas.app.diagnostics import ApplicationDiagnostics
from gigas.app.shell import ApplicationShell
from gigas.storage.settings import default_settings_path


def run() -> int:
    """Create the application, display its window, and run the Qt event loop."""
    path = default_settings_path()
    application = QApplication(sys.argv)
    application.setQuitOnLastWindowClosed(False)
    diagnostics = ApplicationDiagnostics(path)
    try:
        shell = ApplicationShell(path)
    except Exception:
        logging.getLogger(__name__).exception(
            "Application construction failed", extra={"event": "app.failed"}
        )
        diagnostics.join()
        raise
    diagnostics.attach(shell.window.notifications)
    shell.stopped.connect(diagnostics.finish, Qt.ConnectionType.QueuedConnection)
    diagnostics.stopped.connect(application.quit, Qt.ConnectionType.QueuedConnection)
    failure: Exception | None = None
    try:
        shell.start()
    except Exception as error:
        logging.getLogger(__name__).exception(
            "Application startup failed", extra={"event": "app.failed"}
        )
        failure = error
        shell.request_close()
    code = application.exec()
    diagnostics.join()
    if failure is not None:
        raise failure
    return code
