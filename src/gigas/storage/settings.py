"""Read and write global application settings."""

import os
import tempfile
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from tomlkit.exceptions import ParseError, TOMLKitError

if TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument


@dataclass(frozen=True, slots=True)
class Settings:
    """Global settings values independent of the UI and device libraries.

    Attributes:
        capture_device: DirectShow device path. An empty string leaves it unset.
        serial_port: Selected serial port. An empty string leaves it unset.
        model: Requested model. An empty string leaves it unset.
        effort: Requested reasoning effort. An empty string leaves it unset.
        auth_method: Authentication preference, either chatgpt or api_key.
        capture_fps: Requested integer capture rate, from 1 to 120 frames per second.
        preview_fps: GUI update limit, from 1 to 60 frames per second.
    """

    capture_device: str = ""
    serial_port: str = ""
    model: str = ""
    effort: str = ""
    auth_method: str = "chatgpt"
    capture_fps: int = 60
    preview_fps: int = 30


class SettingsError(ValueError):
    """A settings operation failed with a message suitable for display."""


def default_settings_path() -> Path:
    """Determine the per-user Windows settings path.

    Returns:
        The path under LOCALAPPDATA. This call creates no files or directories.

    Raises:
        SettingsError: LOCALAPPDATA is missing, empty, or not absolute.
    """
    directory = Path(os.environ.get("LOCALAPPDATA", ""))
    if not directory.is_absolute():
        msg = "LOCALAPPDATA must contain an absolute directory path"
        raise SettingsError(msg)
    return directory / "GIGAS" / "settings.toml"


def load_settings(path: "Path") -> Settings:
    """Read settings, returning defaults if the file is absent.

    Args:
        path: Path to the UTF-8 TOML file.

    Returns:
        Settings values with defaults for omitted fields.

    Raises:
        SettingsError: The file cannot be read or contains invalid settings.
    """
    return _settings_from_document(_read_document(path), path)


def _read_document(path: "Path") -> "TOMLDocument":
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return tomlkit.document()
    except OSError as exc:
        msg = f"{path}: could not read settings ({exc.strerror})"
        raise SettingsError(msg) from exc

    try:
        return tomlkit.parse(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        msg = f"{path}: invalid UTF-8 at byte offset {exc.start}"
        raise SettingsError(msg) from None
    except ParseError as exc:
        msg = f"{path}: invalid TOML at line {exc.line}, column {exc.col}"
        raise SettingsError(msg) from None
    except TOMLKitError:
        msg = f"{path}: invalid TOML"
        raise SettingsError(msg) from None


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
        capture_fps=_integer(capture, "fps", path, 60, 1, 120),
        preview_fps=_integer(capture, "preview_fps", path, 30, 1, 60),
    )


def save_settings(path: "Path", settings: Settings) -> None:
    """Update settings while retaining comments and unrelated TOML entries.

    Args:
        path: Target settings file.
        settings: Values to persist.

    Raises:
        SettingsError: The existing file is invalid, a value is invalid, or saving fails.
    """
    document = _read_document(path)
    _settings_from_document(document, path)

    capture = _ensure_table(document, "capture", path)
    controller = _ensure_table(document, "controller", path)
    serial = _ensure_table(controller, "serial", path)
    agent = _ensure_table(document, "agent", path)
    _set_text(capture, "device", settings.capture_device)
    for key, value in (
        ("fps", settings.capture_fps),
        ("preview_fps", settings.preview_fps),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or capture.get(key) != value:
            capture[key] = value
    _set_text(serial, "port", settings.serial_port)
    _set_text(agent, "model", settings.model)
    _set_text(agent, "effort", settings.effort)
    _set_text(agent, "auth_method", settings.auth_method)
    _settings_from_document(document, path)
    _replace_file(path, tomlkit.dumps(document))


def _replace_file(path: Path, contents: str) -> None:
    try:
        data = contents.encode("utf-8")
    except UnicodeEncodeError:
        msg = f"{path}: settings cannot be encoded as UTF-8"
        raise SettingsError(msg) from None

    temporary_path = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(data)
        temporary_path.replace(path)
    except OSError as exc:
        msg = f"{path}: could not save settings ({exc.strerror})"
        error = SettingsError(msg)
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as cleanup:
                error.add_note(
                    f"{temporary_path}: could not remove temporary file ({cleanup.strerror})"
                )
        raise error from exc


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


def _integer(
    table: MutableMapping[str, object],
    key: str,
    path: Path,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        msg = f"{path}: capture.{key} must be an integer from {minimum} to {maximum}"
        raise SettingsError(msg)
    return int(value)


def _ensure_table(
    parent: MutableMapping[str, object], key: str, path: "Path"
) -> MutableMapping[str, object]:
    if key not in parent:
        parent[key] = tomlkit.table()
    return _table(parent, key, path)


def _set_text(table: MutableMapping[str, object], key: str, value: str) -> None:
    if table.get(key) != value:
        table[key] = value
