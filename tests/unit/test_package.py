import importlib.metadata
from pathlib import Path

import gigas


def test_package_version_matches_metadata() -> None:
    assert gigas.__version__ == importlib.metadata.version("gigas")


def test_tmp_path_stays_in_workspace(tmp_path: Path) -> None:
    assert tmp_path.is_relative_to(Path.cwd() / "tmp" / "pytest")
