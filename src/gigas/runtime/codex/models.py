"""Application-facing values independent of Codex wire schemas and Qt."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path

type ConnectionPhase = Literal[
    "connecting",
    "login_required",
    "authenticating",
    "ready",
    "starting",
    "running",
    "waiting",
    "stopping",
    "stopped",
    "error",
    "closed",
]


@dataclass(frozen=True, slots=True)
class ConnectionOptions:
    """Settings for one Codex connection.

    Attributes:
        cwd: Existing absolute directory for the conversation, separate from source code.
        model: Catalog model name, or empty to select the catalog default.
        effort: Catalog reasoning effort, or empty to select the model default.
        auth_method: Authentication preference. Only chatgpt is currently supported.
    """

    cwd: "Path"
    model: str = ""
    effort: str = ""
    auth_method: str = "chatgpt"


@dataclass(frozen=True, slots=True)
class SessionStatus:
    """Current application status derived from operations and runtime notifications.

    Attributes:
        phase: Connection or turn phase; stopped requires a terminal turn notification.
        model: Validated model name, empty before catalog validation.
        effort: Validated reasoning effort, empty before catalog validation.
        authenticated: Whether account/read confirmed the chosen authentication type.
        detail: A short application-owned reason safe for display.
    """

    phase: ConnectionPhase
    model: str = ""
    effort: str = ""
    authenticated: bool = False
    detail: str = ""


@dataclass(frozen=True, slots=True)
class TextEvent:
    """User or assistant text intended for the conversation view.

    Attributes:
        item_id: Stable item identifier within this connection.
        text: User-provided or runtime-provided conversation text.
        role: Source of the text.
        replace: Whether this text replaces earlier deltas for the same item.
    """

    item_id: str
    text: str
    role: Literal["assistant", "user"] = "assistant"
    replace: bool = False


@dataclass(frozen=True, slots=True)
class LoginEvent:
    """A login URL the user can choose to open in a browser.

    Attributes:
        url: HTTP or HTTPS URL from the explicit ChatGPT login operation.
    """

    url: str


type ConnectionEvent = SessionStatus | TextEvent | LoginEvent
