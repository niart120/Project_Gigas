"""Read and write global application settings."""

import os
import tempfile
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from tomlkit.exceptions import ParseError, TOMLKitError

from gigas.game_io.input.config import get_protocol, resolve_baudrate

if TYPE_CHECKING:
    from tomlkit.toml_document import TOMLDocument

    from gigas.game_io.observation import ObservationResolution


@dataclass(frozen=True, slots=True)
class Settings:
    """Global settings values independent of the UI and device libraries.

    Attributes:
        capture_device: Opaque capture-device identifier. An empty string leaves it unset.
        serial_port: Selected serial port. An empty string leaves it unset.
        serial_protocol: Firmware protocol: ch552, pokecon, or 3ds.
        serial_baudrate: Supported rate, or None for the protocol default.
        model: Requested model. An empty string leaves it unset.
        effort: Requested reasoning effort. An empty string leaves it unset.
        auth_method: Authentication preference, either chatgpt or api_key.
        capture_fps: Requested integer capture rate, from 1 to 120 frames per second.
        preview_fps: GUI update limit, from 1 to 60 frames per second.
        window_size_preset: Display size key: hd, full_hd, wqhd, or four_k.
        capture_resolution: Capture size request: auto, hd, full_hd, wqhd, or four_k.
            The auto value leaves the device dimensions unchanged.
        observation_resolution: Agent image size: original, full_hd, or hd.
            Independent of capture resolution and preview display.
        preview_letterbox: Preserve the preview aspect ratio with black padding when
            true; stretch to the preview area when false. Capture pixels are unchanged.
        last_project_path: Last successfully selected project directory, or empty.
        show_reasoning: Display public reasoning summaries when true. Hiding them
            does not stop receiving updates or change model reasoning settings.
    """

    capture_device: str = ""
    serial_port: str = ""
    model: str = ""
    effort: str = ""
    auth_method: str = "chatgpt"
    capture_fps: int = 60
    preview_fps: int = 30
    window_size_preset: str = "full_hd"
    capture_resolution: str = "full_hd"
    preview_letterbox: bool = True
    observation_resolution: "ObservationResolution" = "full_hd"
    last_project_path: str = ""
    serial_protocol: str = "ch552"
    serial_baudrate: int | None = None
    show_reasoning: bool = True


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
    observation = _table(document, "observation", path)
    project = _table(document, "project", path)
    serial = _table(_table(document, "controller", path), "serial", path)
    agent = _table(document, "agent", path)
    gui = _table(document, "gui", path)
    auth_method = _text(agent, "auth_method", path, default="chatgpt")
    if auth_method not in ("chatgpt", "api_key"):
        msg = f"{path}: auth_method must be chatgpt or api_key"
        raise SettingsError(msg)

    try:
        protocol = get_protocol(serial.get("protocol", "ch552"))
        baudrate = serial.get("baudrate")
        effective_baudrate = resolve_baudrate(protocol, baudrate)
    except ValueError as error:
        message = f"{path}: {error}"
        raise SettingsError(message) from error

    return Settings(
        capture_device=_text(capture, "device", path),
        serial_port=_text(serial, "port", path),
        serial_protocol=protocol.key,
        serial_baudrate=effective_baudrate if baudrate is not None else None,
        model=_text(agent, "model", path),
        effort=_text(agent, "effort", path),
        auth_method=auth_method,
        capture_fps=_integer(capture, "fps", path, 60, 1, 120),
        preview_fps=_integer(capture, "preview_fps", path, 30, 1, 60),
        window_size_preset=_window_size_preset(gui.get("window_size_preset", "full_hd"), path),
        capture_resolution=_capture_resolution(capture.get("resolution", "full_hd"), path),
        preview_letterbox=_gui_boolean(
            gui.get("preview_letterbox", True), path, "preview_letterbox"
        ),
        show_reasoning=_gui_boolean(gui.get("show_reasoning", True), path, "show_reasoning"),
        observation_resolution=_observation_resolution(
            observation.get("resolution", "full_hd"), path
        ),
        last_project_path=_text(project, "last_path", path),
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
    observation = _ensure_table(document, "observation", path)
    project = _ensure_table(document, "project", path)
    controller = _ensure_table(document, "controller", path)
    serial = _ensure_table(controller, "serial", path)
    agent = _ensure_table(document, "agent", path)
    gui = _ensure_table(document, "gui", path)
    _set_text(gui, "window_size_preset", _window_size_preset(settings.window_size_preset, path))
    for key, value in (
        ("preview_letterbox", settings.preview_letterbox),
        ("show_reasoning", settings.show_reasoning),
    ):
        boolean = _gui_boolean(value, path, key)
        if gui.get(key) != boolean:
            gui[key] = boolean
    _set_text(capture, "resolution", _capture_resolution(settings.capture_resolution, path))
    _set_text(
        observation, "resolution", _observation_resolution(settings.observation_resolution, path)
    )
    _set_text(project, "last_path", settings.last_project_path)
    _set_text(capture, "device", settings.capture_device)
    for key, value in (
        ("fps", settings.capture_fps),
        ("preview_fps", settings.preview_fps),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or capture.get(key) != value:
            capture[key] = value
    _set_text(serial, "port", settings.serial_port)
    _set_text(serial, "protocol", settings.serial_protocol)
    if settings.serial_baudrate is None:
        serial.pop("baudrate", None)
    else:
        serial["baudrate"] = settings.serial_baudrate
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


def _window_size_preset(value: object, path: Path) -> str:
    if not isinstance(value, str) or value not in ("hd", "full_hd", "wqhd", "four_k"):
        msg = f"{path}: gui.window_size_preset must be hd, full_hd, wqhd or four_k"
        raise SettingsError(msg)
    return str(value)


def _capture_resolution(value: object, path: Path) -> str:
    if not isinstance(value, str) or value not in ("auto", "hd", "full_hd", "wqhd", "four_k"):
        msg = f"{path}: capture.resolution must be auto, hd, full_hd, wqhd or four_k"
        raise SettingsError(msg)
    return str(value)


def _observation_resolution(value: object, path: Path) -> "ObservationResolution":
    match value:
        case "original":
            return "original"
        case "full_hd":
            return "full_hd"
        case "hd":
            return "hd"
        case _:
            msg = f"{path}: observation.resolution must be original, full_hd or hd"
            raise SettingsError(msg)


def _gui_boolean(value: object, path: Path, key: str) -> bool:
    if not isinstance(value, bool):
        msg = f"{path}: gui.{key} must be a boolean"
        raise SettingsError(msg)
    return value


def _set_text(table: MutableMapping[str, object], key: str, value: str) -> None:
    if table.get(key) != value:
        table[key] = value
