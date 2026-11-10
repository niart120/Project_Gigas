"""Read and write global application settings."""

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


def load_settings(path: "Path") -> Settings:
    """Read settings, returning defaults if the file is absent.

    Args:
        path: Path to the UTF-8 TOML file.

    Returns:
        Settings values with defaults for omitted fields.
    """
    try:
        document = tomlkit.parse(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return Settings()

    capture = document.get("capture", {})
    serial = document.get("controller", {}).get("serial", {})
    agent = document.get("agent", {})
    return Settings(
        capture_device=str(capture.get("device", "")),
        serial_port=str(serial.get("port", "")),
        model=str(agent.get("model", "")),
        effort=str(agent.get("effort", "")),
        auth_method=str(agent.get("auth_method", "chatgpt")),
    )


def save_settings(path: "Path", settings: Settings) -> None:
    """Write settings to a UTF-8 TOML file.

    Args:
        path: Target settings file.
        settings: Values to persist.
    """
    document = {
        "capture": {"device": settings.capture_device},
        "controller": {"serial": {"port": settings.serial_port}},
        "agent": {
            "model": settings.model,
            "effort": settings.effort,
            "auth_method": settings.auth_method,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomlkit.dumps(document), encoding="utf-8")
