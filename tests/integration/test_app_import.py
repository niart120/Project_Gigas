import os
import subprocess
import sys
from pathlib import Path


def test_import_does_not_initialize_application(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import threading; import gigas.app; import gigas.ui; import gigas.__main__; "
            "assert len(threading.enumerate()) == 1; "
            "assert 'PySide6.QtWidgets' not in __import__('sys').modules",
        ],
        env={**os.environ, "LOCALAPPDATA": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
