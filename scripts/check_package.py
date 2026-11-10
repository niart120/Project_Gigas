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


def run(command: list[str], cwd: Path) -> None:
    """Run a local packaging command with a bounded wait."""
    subprocess.run(command, cwd=cwd, check=True, timeout=180)  # noqa: S603 -- local tools


def main() -> None:
    """Build once, install the resulting wheel and read its resource."""
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


if __name__ == "__main__":
    main()
