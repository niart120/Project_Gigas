"""Application-facing values independent of Codex wire schemas and Qt."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path

    from gigas.runtime.codex.transport import RpcId

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


@dataclass(frozen=True, slots=True)
class QuestionOption:
    """One model-provided choice with its description."""

    label: str
    description: str


@dataclass(frozen=True, slots=True)
class Question:
    """One user question retaining its identifier and allowed answer controls.

    Attributes:
        id: Key used to send the answer to Codex.
        header: Short model-provided heading.
        question: Model-provided question text.
        options: Selectable model-provided choices; empty allows text input.
        is_other: Whether text outside the supplied options is allowed.
        is_secret: Whether answer entry should hide the typed text.
    """

    id: str
    header: str
    question: str
    options: tuple[QuestionOption, ...] = ()
    is_other: bool = False
    is_secret: bool = False


@dataclass(frozen=True, slots=True)
class HumanRequest:
    """A live question or approval, identified by a connection-unique local token.

    Attributes:
        token: Opaque local identity passed to answer; never reuse it after resolution.
        rpc_id: Original RPC identity, preserving integer versus string.
        thread_id: Runtime conversation containing the request.
        turn_id: Runtime turn containing the request, including nonblocking questions.
        kind: Question, command approval or file-change approval.
        questions: Structured questions; empty for approvals.
        details: Runtime-provided command, paths, reason or proposed diff for display.
        decisions: Supported one-time decision names; no automatic default approval.
        is_blocking: Whether the runtime is waiting for this request to finish.
    """

    token: str
    rpc_id: "RpcId"
    thread_id: str
    turn_id: str
    kind: Literal["question", "command", "file"]
    questions: tuple[Question, ...] = ()
    details: str = ""
    decisions: tuple[str, ...] = ()
    is_blocking: bool = True


@dataclass(frozen=True, slots=True)
class RequestResolved:
    """A local request token that no longer accepts answers.

    Resolution includes reply submission, runtime dismissal, stop or disconnection;
    it does not assert approval or successful execution of the requested operation.
    """

    token: str


type ConnectionEvent = SessionStatus | TextEvent | LoginEvent | HumanRequest | RequestResolved
