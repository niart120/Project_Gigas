from dataclasses import replace
from pathlib import Path

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
