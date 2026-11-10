import errno
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Never

import pytest

from gigas.storage import settings


def test_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "設定 ファイル" / "settings.toml"
    chosen = settings.Settings("映像入力", "COM3", "sample-model", "medium", "api_key")

    settings.save_settings(path, chosen)

    assert settings.load_settings(path) == chosen


@pytest.mark.parametrize(
    ("source", "reason"),
    [(b'[agent\nmodel = "private-value"', "TOML"), (b"\xff", "UTF-8")],
)
def test_invalid_document_is_reported_and_unchanged(
    tmp_path: Path, source: bytes, reason: str
) -> None:
    path = tmp_path / "settings.toml"
    path.write_bytes(source)

    with pytest.raises(settings.SettingsError, match=reason) as error:
        settings.load_settings(path)

    assert str(path) in str(error.value)
    assert "private-value" not in str(error.value)
    assert path.read_bytes() == source


def test_unreadable_settings_reports_path(tmp_path: Path) -> None:
    with pytest.raises(settings.SettingsError, match="read") as error:
        settings.load_settings(tmp_path)

    assert str(tmp_path) in str(error.value)


def test_save_preserves_comments_and_unedited_values(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(
        "# user settings\n"
        "[capture]\ndevice = 'capture card' # source\n"
        "[agent]\nmodel = 'selected' # keep spelling\neffort = \"high\" # reasoning\n"
        "[local]\nextra = [1, 2] # personal note\n",
        encoding="utf-8",
    )
    chosen = replace(settings.load_settings(path), effort="")

    settings.save_settings(path, chosen)

    saved = path.read_text(encoding="utf-8")
    assert "# user settings" in saved
    assert "device = 'capture card' # source" in saved
    assert "model = 'selected' # keep spelling" in saved
    assert 'effort = "" # reasoning' in saved
    assert "[local]\nextra = [1, 2] # personal note" in saved
    assert settings.load_settings(path) == chosen


@pytest.mark.parametrize("original", [b"[broken\n", b"[agent]\nmodel = 1\n"])
def test_save_rejects_invalid_existing_document(tmp_path: Path, original: bytes) -> None:
    path = tmp_path / "settings.toml"
    path.write_bytes(original)

    with pytest.raises(settings.SettingsError):
        settings.save_settings(path, settings.Settings())

    assert path.read_bytes() == original


@pytest.mark.parametrize("stage", ["create", "replace"])
def test_failed_save_preserves_original_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    path = tmp_path / "settings.toml"
    original = b'[agent]\nmodel = "saved"\n'
    path.write_bytes(original)

    def fail(*_args: object, **_kwargs: object) -> Never:
        raise PermissionError(errno.EACCES, "injected failure")

    if stage == "create":
        monkeypatch.setattr(tempfile, "NamedTemporaryFile", fail)
    else:
        monkeypatch.setattr(Path, "replace", fail)

    with pytest.raises(settings.SettingsError, match="injected failure") as error:
        settings.save_settings(path, settings.Settings(model="edited"))

    assert str(path) in str(error.value)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
