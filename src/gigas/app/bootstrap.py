"""Qt process entry point."""

import sys

from PySide6.QtWidgets import QApplication

from gigas.app.shell import ApplicationShell
from gigas.storage.settings import default_settings_path


def run() -> int:
    """Create the application, display its window, and run the Qt event loop."""
    path = default_settings_path()
    application = QApplication(sys.argv)
    shell = ApplicationShell(path)
    shell.start()
    return application.exec()
