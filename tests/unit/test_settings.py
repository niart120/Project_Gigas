from pathlib import Path

from gigas.storage.settings import Settings, load_settings


def test_missing_settings_returns_defaults_without_creating_directory(tmp_path: Path) -> None:
    path = tmp_path / "GIGAS" / "settings.toml"

    assert load_settings(path) == Settings("", "", "", "", "chatgpt")
    assert not path.parent.exists()
