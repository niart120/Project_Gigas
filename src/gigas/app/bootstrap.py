"""Qt process entry point."""

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from gigas.app.shell import ApplicationShell
from gigas.storage.settings import default_settings_path


def run() -> int:
    """Create the application, display its window, and run the Qt event loop."""
    path = default_settings_path()
    application = QApplication(sys.argv)
    application.setQuitOnLastWindowClosed(False)
    shell = ApplicationShell(path)
    shell.stopped.connect(application.quit, Qt.ConnectionType.QueuedConnection)
    failure: Exception | None = None
    try:
        shell.start()
    except Exception as error:  # noqa: BLE001 -- release partially started services
        failure = error
        shell.request_close()
    code = application.exec()
    if failure is not None:
        raise failure
    return code
