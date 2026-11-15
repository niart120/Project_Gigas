import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from gigas.storage.settings import Settings, SettingsError, load_settings, save_settings


def test_execution_preferences_default_on_and_round_trip_without_deleting_memory(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.toml"
    defaults = load_settings(path)
    assert defaults.web_search_enabled is True
    assert defaults.memory_enabled is True
    assert defaults.approvals_reviewer == "auto_review"
    memory = tmp_path / "codex_home" / "memories" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("retained memory", encoding="utf-8")
    path.write_text('[agent]\n# keep this comment\nmodel = "chosen"\ncustom = 42\n')
    selected = replace(
        load_settings(path),
        web_search_enabled=False,
        memory_enabled=False,
        approvals_reviewer="user",
    )
    save_settings(path, selected)
    assert load_settings(path) == selected
    stored = tomllib.loads(path.read_text(encoding="utf-8"))["agent"]
    assert stored == {
        "model": "chosen",
        "custom": 42,
        "effort": "",
        "auth_method": "chatgpt",
        "web_search_enabled": False,
        "memory_enabled": False,
        "approvals_reviewer": "user",
    }
    assert "# keep this comment" in path.read_text(encoding="utf-8")
    save_settings(path, replace(selected, model="another"))
    assert load_settings(path).approvals_reviewer == "user"
    assert load_settings(path).memory_enabled is False
    assert memory.read_text(encoding="utf-8") == "retained memory"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("web_search_enabled", 1),
        ("web_search_enabled", "live"),
        ("memory_enabled", 0),
        ("memory_enabled", "false"),
        ("approvals_reviewer", True),
        ("approvals_reviewer", "never"),
    ],
)
def test_invalid_execution_preferences_do_not_replace_saved_settings(
    tmp_path: Path, key: str, value: object
) -> None:
    path = tmp_path / "settings.toml"
    save_settings(path, Settings())
    original = path.read_bytes()
    with pytest.raises(SettingsError, match=key):
        save_settings(path, replace(Settings(), **{key: value}))
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "entry",
    [
        "web_search_enabled = 1",
        'memory_enabled = "false"',
        'approvals_reviewer = "never"',
    ],
)
def test_invalid_execution_preferences_on_disk_are_reported(tmp_path: Path, entry: str) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(f"[agent]\n{entry}\n", encoding="utf-8")
    with pytest.raises(SettingsError, match=entry.split(maxsplit=1)[0]):
        load_settings(path)
