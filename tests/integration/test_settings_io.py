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
