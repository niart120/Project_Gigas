"""Read and write global application settings."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

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
    """Read settings, returning defaults if the file is absent."""
    try:
        path.read_bytes()
    except FileNotFoundError:
        return Settings()
    raise NotImplementedError
