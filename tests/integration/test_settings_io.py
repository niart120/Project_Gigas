from pathlib import Path

from gigas.storage import settings


def test_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "設定 ファイル" / "settings.toml"
    chosen = settings.Settings("映像入力", "COM3", "sample-model", "medium", "api_key")

    settings.save_settings(path, chosen)

    assert settings.load_settings(path) == chosen
