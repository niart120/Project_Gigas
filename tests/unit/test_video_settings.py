import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from gigas.storage.settings import Settings, SettingsError, load_settings, save_settings


def test_legacy_settings_gain_video_defaults_without_changing_device(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    original = '# device comment\n[capture]\ndevice = "old name"\n'
    path.write_text(original, encoding="utf-8")

    values = load_settings(path)

    assert values.capture_device == "old name"
    assert (values.capture_fps, values.preview_fps) == (60, 30)
    assert path.read_text(encoding="utf-8") == original


def test_video_mode_round_trip_preserves_comments_and_unknown_values(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('# retained\n[capture]\ncustom = "unchanged"\n', encoding="utf-8")
    values = Settings(
        capture_device="device-path",
        capture_fps=30,
        preview_fps=15,
    )

    save_settings(path, values)

    assert load_settings(path) == values
    assert "# retained" in path.read_text(encoding="utf-8")
    assert 'custom = "unchanged"' in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("key", "invalid"),
    [
        ("fps", "0"),
        ("fps", "121"),
        ("fps", "30.0"),
        ("preview_fps", "0"),
        ("preview_fps", "61"),
        ("preview_fps", '"30"'),
    ],
)
def test_invalid_video_mode_cannot_be_loaded(tmp_path: Path, key: str, invalid: str) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(f"[capture]\n{key} = {invalid}\n", encoding="utf-8")

    with pytest.raises(SettingsError, match=key):
        load_settings(path)


@pytest.mark.parametrize("invalid", [0, True, 1.0])
def test_invalid_video_mode_cannot_overwrite_existing_settings(
    tmp_path: Path, invalid: object
) -> None:
    path = tmp_path / "settings.toml"
    original = '# keep\n[capture]\ndevice="original"\nfps=1\n'
    path.write_text(original, encoding="utf-8")
    values = replace(Settings(), capture_fps=invalid)

    with pytest.raises(SettingsError, match="fps"):
        save_settings(path, values)

    assert path.read_text(encoding="utf-8") == original


def test_old_dimensions_are_ignored_and_preserved_as_unrelated_values(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    original = '[capture]\nwidth = 0\nheight = "legacy"\n'
    path.write_text(original, encoding="utf-8")

    values = load_settings(path)

    assert values == Settings()
    save_settings(path, replace(values, capture_fps=30))
    saved = tomllib.loads(path.read_text(encoding="utf-8"))
    assert saved["capture"]["width"] == 0
    assert saved["capture"]["height"] == "legacy"


def test_fresh_settings_do_not_create_capture_dimensions(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"

    save_settings(path, Settings())

    saved = tomllib.loads(path.read_text(encoding="utf-8"))
    assert "width" not in saved["capture"]
    assert "height" not in saved["capture"]


def test_legacy_settings_default_to_full_hd_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    original = '# old file\n[gui]\ncustom = "preserve"\n'
    path.write_text(original, encoding="utf-8")

    assert load_settings(path).window_size_preset == "full_hd"
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("preset", ["hd", "full_hd", "wqhd", "four_k"])
def test_window_size_preset_round_trip_preserves_unrelated_values(
    tmp_path: Path, preset: str
) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(
        '# keep\n[gui]\nwindow_size_preset = "hd" # choice\ncustom = "untouched"\n',
        encoding="utf-8",
    )
    values = Settings(window_size_preset=preset)

    save_settings(path, values)

    assert load_settings(path) == values
    contents = path.read_text(encoding="utf-8")
    document = tomllib.loads(contents)
    assert document["gui"]["window_size_preset"] == preset
    assert document["gui"]["custom"] == "untouched"
    assert "# keep" in contents
    assert "# choice" in contents


@pytest.mark.parametrize("invalid", ['"unknown"', '""', "1", "true", "[]"])
def test_invalid_window_size_preset_cannot_be_loaded(tmp_path: Path, invalid: str) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(f"[gui]\nwindow_size_preset = {invalid}\n", encoding="utf-8")

    with pytest.raises(SettingsError, match="window_size_preset"):
        load_settings(path)


def test_gui_settings_require_a_table(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('gui = "full_hd"\n', encoding="utf-8")

    with pytest.raises(SettingsError, match="gui"):
        load_settings(path)


@pytest.mark.parametrize("invalid", ["unknown", 1, True, None])
def test_invalid_window_size_preset_cannot_overwrite_existing_file(
    tmp_path: Path, invalid: object
) -> None:
    path = tmp_path / "settings.toml"
    original = '# keep\n[gui]\nwindow_size_preset = "hd"\ncustom = 3\n'
    path.write_text(original, encoding="utf-8")
    values = replace(Settings(), window_size_preset=invalid)

    with pytest.raises(SettingsError, match="window_size_preset"):
        save_settings(path, values)

    assert path.read_text(encoding="utf-8") == original
