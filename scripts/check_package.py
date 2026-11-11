"""Build and smoke test GIGAS in a fresh environment outside the repository."""

import hashlib
import subprocess
import sys
from pathlib import Path
from tarfile import open as open_tar
from tempfile import TemporaryDirectory
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
RESOURCE = "gigas/prompts/play.md"

RESOURCE_PROBE = """
import hashlib
import sys
from importlib.resources import files
from pathlib import Path

import gigas
from PySide6.QtCore import qVersion

installed = Path(sys.argv[1]).resolve()
origin = Path(gigas.__file__).resolve()
assert Path(sys.prefix).resolve() == installed, sys.prefix
assert origin.is_relative_to(installed), origin
resource = files("gigas.prompts").joinpath("play.md")
assert Path(str(resource)).resolve().is_relative_to(installed), resource
text = resource.read_text(encoding="utf-8")
assert text.strip(), resource
assert hashlib.sha256(text.encode("utf-8")).hexdigest() == sys.argv[2], resource
print(f"Installed from {origin} (Python {sys.version.split()[0]}, Qt {qVersion()})")
"""

APP_PROBE = """
import os
import runpy
import sys
from importlib.metadata import distribution

os.environ["LOCALAPPDATA"] = sys.argv[2]
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow

shown = []
closed = False

def close_window():
    for window in QApplication.topLevelWidgets():
        if isinstance(window, QMainWindow) and window.isVisible():
            shown.append(window)
            window.close()
            break

original_exec = QApplication.exec

def run_loop():
    global closed
    QTimer.singleShot(0, close_window)
    code = original_exec()
    closed = bool(shown) and not shown[0].isVisible()
    return code

QApplication.exec = staticmethod(run_loop)
entry = sys.argv[1]
if entry == "module":
    try:
        runpy.run_module("gigas", run_name="__main__")
    except SystemExit as result:
        assert result.code == 0, result.code
else:
    (script,) = distribution("gigas").entry_points.select(group="console_scripts", name="gigas")
    assert script.load()() == 0
assert closed, f"{entry}: no main window completed the Qt close cycle"
print(f"{entry}: main window opened and closed")
"""

CAPTURE_PROBE = """
import multiprocessing
import sys
import time

from gigas.game_io.capture import CaptureService
from gigas.game_io.models import CaptureOptions

if __name__ == "__main__":
    service = CaptureService()
    service.start()
    try:
        # Resolve an impossible DirectShow path; never open a physical device.
        assert service.connect(CaptureOptions("gigas:package-probe-missing-device"))
        deadline = time.monotonic() + 20
        while service.snapshot().phase not in ("error", "closed"):
            assert time.monotonic() < deadline, service.snapshot()
            time.sleep(0.02)
        state = service.snapshot()
        assert state.phase == "error", state
        assert state.error == "映像取得元が見つからない", state
    finally:
        service.shutdown()
        service.join(6)
    assert not service.is_alive(), service.snapshot()
    assert not multiprocessing.active_children()
    assert not any(name.startswith("PySide6") for name in sys.modules)
    print("Installed capture worker spawned, rejected missing device, and exited")
"""


def check_resources(sdist: Path, wheel: Path) -> str:
    """Check both archives and return the expected installed text digest."""
    with ZipFile(wheel) as archive:
        wheel_text = archive.read(RESOURCE).decode("utf-8")
    with open_tar(sdist, "r:gz") as archive:
        member = f"{sdist.name.removesuffix('.tar.gz')}/src/{RESOURCE}"
        stream = archive.extractfile(member)
        if stream is None:
            message = f"Expected a file in the sdist: {member}"
            raise ValueError(message)
        sdist_text = stream.read().decode("utf-8")
    original = (ROOT / "src" / RESOURCE).read_bytes().decode("utf-8")
    if not original.strip() or original != wheel_text or original != sdist_text:
        message = f"Resource is empty or differs between source and archives: {RESOURCE}"
        raise ValueError(message)
    # importlib.resources.read_text normalizes Windows line endings.
    normalized = original.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def run(command: list[str], cwd: Path, *, timeout: int = 180) -> None:
    """Run a local packaging command with a bounded wait."""
    subprocess.run(command, cwd=cwd, check=True, timeout=timeout)  # noqa: S603 -- local tools


def main() -> None:
    """Build once and check resources and startup from the installed wheel."""
    with TemporaryDirectory(prefix="gigas-package-") as directory:
        work = Path(directory).resolve()
        if work.is_relative_to(ROOT):
            message = f"Use a temporary directory outside {ROOT}"
            raise ValueError(message)
        artifacts = work / "dist"
        run(["uv", "build", "--out-dir", str(artifacts)], ROOT)
        (sdist,) = artifacts.glob("*.tar.gz")
        (wheel,) = artifacts.glob("*.whl")
        digest = check_resources(sdist, wheel)

        venv = work / "venv"
        run(["uv", "venv", "--python", sys.executable, str(venv)], work)
        python = str(venv / "Scripts" / "python.exe")
        run(["uv", "pip", "install", "--python", python, "--link-mode", "copy", str(wheel)], work)
        run([python, "-I", "-c", RESOURCE_PROBE, str(venv), digest], work)
        print(f"Resource verified in {sdist.name} and {wheel.name}", flush=True)  # noqa: T201 -- CLI
        run([python, "-I", "-c", CAPTURE_PROBE], work, timeout=30)
        for entry in ("module", "script"):
            run(
                [python, "-I", "-c", APP_PROBE, entry, str(work / f"settings-{entry}")],
                work,
                timeout=15,
            )


if __name__ == "__main__":
    main()
