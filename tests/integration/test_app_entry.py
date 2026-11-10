import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("entry", ["module", "script"])
def test_entry_opens_window_and_exits(entry: str, tmp_path: Path) -> None:
    probe = '''
import runpy
from importlib.metadata import distribution
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow

timer = QTimer()
seen = []
def close_window():
    for window in QApplication.topLevelWidgets():
        if isinstance(window, QMainWindow) and window.isVisible():
            timer.stop()
            seen.append(window.windowTitle())
            window.close()
timer.timeout.connect(close_window)
original_exec = QApplication.exec
def run_loop():
    timer.start(10)
    code = original_exec()
    if seen:
        print('GIGAS_WINDOW_CLOSED')
    return code
QApplication.exec = staticmethod(run_loop)
'''
    probe += (
        "runpy.run_module('gigas', run_name='__main__')"
        if entry == "module"
        else "raise SystemExit(next(e for e in distribution('gigas').entry_points "
        "if e.name == 'gigas' and e.group == 'console_scripts').load()())"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        env={**os.environ, "LOCALAPPDATA": str(tmp_path), "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    # The module must actually enter Qt; a no-op entry never stops the timer.
    assert "GIGAS_WINDOW_CLOSED" in result.stdout
    assert list(tmp_path.iterdir()) == []
