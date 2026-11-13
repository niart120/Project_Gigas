from dataclasses import replace
from pathlib import Path

import pytest

from gigas.game_io.input.config import resolve_serial_config
from gigas.storage import settings
from gigas.storage.settings import Settings, load_settings


@pytest.mark.parametrize(
    ("protocol", "baud"), [("ch552", 9600), ("pokecon", 9600), ("3ds", 115200)]
)
def test_serial_defaults_resolve_without_rewriting_omitted_baud(
    tmp_path: Path, protocol: str, baud: int
) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(f'[controller.serial]\nport = "COM3"\nprotocol = "{protocol}"\n')
    values = load_settings(path)
    assert values.serial_protocol == protocol
    assert values.serial_baudrate is None
    config = resolve_serial_config(
        values.serial_port, values.serial_protocol, values.serial_baudrate
    )
    assert config is not None
    assert (config.port, config.protocol.key, config.baudrate) == ("COM3", protocol, baud)
    assert resolve_serial_config("", protocol, None) is None


@pytest.mark.parametrize(
    "source",
    [
        'protocol = "unknown"',
        'protocol = "ch552"\nbaudrate = 115200',
        'protocol = "3ds"\nbaudrate = 38400',
        'protocol = "pokecon"\nbaudrate = 0',
        "baudrate = true",
        "baudrate = 9600.0",
        'baudrate = "9600"',
    ],
)
def test_invalid_serial_protocol_or_baud_is_rejected(tmp_path: Path, source: str) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(f"[controller.serial]\n{source}\n")
    with pytest.raises(settings.SettingsError, match=r"controller\.serial"):
        load_settings(path)


def test_missing_settings_returns_defaults_without_creating_directory(tmp_path: Path) -> None:
    path = tmp_path / "GIGAS" / "settings.toml"

    values = load_settings(path)
    assert values == Settings("", "", "", "", "chatgpt")
    assert values.capture_resolution == "full_hd"
    assert values.preview_letterbox is True
    assert values.show_reasoning is True
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
        ('project = "folder"\n', "project"),
        ("[project]\nlast_path = 42\n", "last_path"),
        ("[gui]\nshow_reasoning = 1\n", "show_reasoning"),
        ('[gui]\nshow_reasoning = "false"\n', "show_reasoning"),
    ],
)
def test_invalid_known_setting_is_rejected(tmp_path: Path, source: str, field: str) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(source, encoding="utf-8")

    with pytest.raises(settings.SettingsError, match=field) as error:
        load_settings(path)

    assert str(path) in str(error.value)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [("auth_method", "invalid"), ("show_reasoning", 0), ("show_reasoning", "false")],
)
def test_invalid_setting_cannot_be_saved(tmp_path: Path, field: str, invalid: object) -> None:
    path = tmp_path / "settings.toml"

    with pytest.raises(settings.SettingsError, match=field):
        settings.save_settings(path, replace(Settings(), **{field: invalid}))

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
