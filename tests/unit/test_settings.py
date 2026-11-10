from pathlib import Path

import pytest

from gigas.storage import settings
from gigas.storage.settings import Settings, load_settings


def test_missing_settings_returns_defaults_without_creating_directory(tmp_path: Path) -> None:
    path = tmp_path / "GIGAS" / "settings.toml"

    assert load_settings(path) == Settings("", "", "", "", "chatgpt")
    assert not path.parent.exists()


def test_omitted_and_unknown_fields_use_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('legacy_device = "unused"\n[agent]\nmodel = "selected"\n', encoding="utf-8")

    assert load_settings(path) == Settings(model="selected")


@pytest.mark.parametrize(
    ("source", "field"),
    [
        ('capture = "camera"\n', "capture"),
        ("[controller]\nserial = []\n", "serial"),
        ("[agent]\nmodel = 10\n", "model"),
        ('[agent]\nauth_method = "invalid"\n', "auth_method"),
    ],
)
def test_invalid_known_setting_is_rejected(tmp_path: Path, source: str, field: str) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(source, encoding="utf-8")

    with pytest.raises(settings.SettingsError, match=field) as error:
        load_settings(path)

    assert str(path) in str(error.value)


def test_invalid_auth_method_cannot_be_saved(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"

    with pytest.raises(settings.SettingsError, match="auth_method"):
        settings.save_settings(path, Settings(auth_method="invalid"))

    assert not path.exists()


def test_default_settings_path_uses_local_appdata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert settings.default_settings_path() == tmp_path / "GIGAS" / "settings.toml"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("value", [None, "", "relative"])
def test_invalid_local_appdata_has_no_working_directory_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    monkeypatch.chdir(tmp_path)
    if value is None:
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
    else:
        monkeypatch.setenv("LOCALAPPDATA", value)

    with pytest.raises(settings.SettingsError, match="LOCALAPPDATA"):
        settings.default_settings_path()

    assert list(tmp_path.iterdir()) == []
