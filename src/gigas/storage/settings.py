"""Read and write global application settings."""

from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

import tomlkit

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    """Global settings values independent of the UI and device libraries.

    Attributes:
        capture_device: Selected capture source. An empty string leaves it unset.
        serial_port: Selected serial port. An empty string leaves it unset.
        model: Requested model. An empty string leaves it unset.
        effort: Requested reasoning effort. An empty string leaves it unset.
        auth_method: Authentication preference, either chatgpt or api_key.
    """

    capture_device: str = ""
    serial_port: str = ""
    model: str = ""
    effort: str = ""
    auth_method: str = "chatgpt"


class SettingsError(ValueError):
    """A settings operation failed with a message suitable for display."""


def load_settings(path: "Path") -> Settings:
    """Read settings, returning defaults if the file is absent.

    Args:
        path: Path to the UTF-8 TOML file.

    Returns:
        Settings values with defaults for omitted fields.

    Raises:
        SettingsError: A known setting has an invalid type or value.
    """
    try:
        document = tomlkit.parse(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return Settings()

    return _settings_from_document(document, path)


def _settings_from_document(document: MutableMapping[str, object], path: "Path") -> Settings:
    capture = _table(document, "capture", path)
    serial = _table(_table(document, "controller", path), "serial", path)
    agent = _table(document, "agent", path)
    auth_method = _text(agent, "auth_method", path, default="chatgpt")
    if auth_method not in ("chatgpt", "api_key"):
        msg = f"{path}: auth_method must be chatgpt or api_key"
        raise SettingsError(msg)

    return Settings(
        capture_device=_text(capture, "device", path),
        serial_port=_text(serial, "port", path),
        model=_text(agent, "model", path),
        effort=_text(agent, "effort", path),
        auth_method=auth_method,
    )


def save_settings(path: "Path", settings: Settings) -> None:
    """Write settings to a UTF-8 TOML file.

    Args:
        path: Target settings file.
        settings: Values to persist.

    Raises:
        SettingsError: A known setting has an invalid type or value.
    """
    document: dict[str, object] = {
        "capture": {"device": settings.capture_device},
        "controller": {"serial": {"port": settings.serial_port}},
        "agent": {
            "model": settings.model,
            "effort": settings.effort,
            "auth_method": settings.auth_method,
        },
    }
    _settings_from_document(document, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(document), encoding="utf-8")


def _table(
    parent: MutableMapping[str, object], key: str, path: "Path"
) -> MutableMapping[str, object]:
    table = parent.get(key, {})
    if not isinstance(table, MutableMapping):
        msg = f"{path}: {key} must be a table"
        raise SettingsError(msg)
    return table


def _text(table: MutableMapping[str, object], key: str, path: "Path", default: str = "") -> str:
    value = table.get(key, default)
    if not isinstance(value, str):
        msg = f"{path}: {key} must be a string"
        raise SettingsError(msg)
    return str(value)
