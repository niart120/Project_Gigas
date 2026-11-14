"""Public delivery values used by the Codex Qt service."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from gigas.game_io.input.config import SerialConfig
    from gigas.runtime.codex.connection import CodexConnection, RuntimeEvent
    from gigas.runtime.codex.management import ProjectInfo
    from gigas.runtime.codex.models import ConnectionEvent, ConnectionOptions

type ServiceOperation = Literal[
    "connect",
    "login",
    "setup_windows_sandbox",
    "send",
    "answer",
    "stop",
    "disconnect",
    "select_model",
    "goal_set",
    "goal_resume",
    "goal_clear",
]
type AnswerOutcome = Literal["sent", "not_sent", "unknown"]


type ConnectionFactory = Callable[
    [ConnectionOptions, Callable[[RuntimeEvent], None]], CodexConnection
]


@dataclass(frozen=True, slots=True)
class InputConnectionResult:
    """Applied serial configuration and connection outcome for the GUI."""

    config: "SerialConfig | None"
    connected: bool
    error: str = ""


@dataclass(frozen=True, slots=True)
class ProjectResult:
    """A Project selection attempt; an empty error confirms the selected server root.

    Attributes:
        generation: Connection generation captured when selection was requested.
        path: Requested directory on failure; server-reported root on success.
        project: Confirmed public Project metadata on success; None on failure.
        error: Display-safe failure details; failure preserves the previous selection.
    """

    generation: int
    path: "Path"
    project: "ProjectInfo | None" = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class ServiceEvent:
    """A connection event with the generation that produced it."""

    generation: int
    event: "ConnectionEvent"


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """Delivery evidence for one answer, independent of request resolution.

    Attributes:
        token: Connection-unique token supplied to answer.
        outcome: sent confirms only transport write completion; not_sent means
            rejection before submission; unknown must never be retried automatically.
            None of these outcomes assert execution of an approved operation.
    """

    token: str
    outcome: AnswerOutcome


@dataclass(frozen=True, slots=True)
class MessageResult:
    """Runtime acceptance evidence for one ordinary message or in-turn instruction.

    Attributes:
        outcome: accepted means a successful public reply; not_sent means no
            submission or an explicit rejection; unknown must not be retried automatically.
        turn_id: Turn confirmed by the successful reply, otherwise None.
            Acceptance does not assert turn completion or compliance with the instruction.
    """

    outcome: Literal["accepted", "not_sent", "unknown"]
    turn_id: str | None = None


@dataclass(frozen=True, slots=True)
class ServiceResult:
    """Operation completion; empty error means the call returned successfully.

    A successful stop confirms server termination and local input recovery.
    A successful goal_clear confirms its RPC result, not execution termination.
    Errors contain no raw exception text.
    Answer operations include token-specific delivery evidence in answer, including
    rejected or cancelled calls. The generation still identifies the connection.
    failure_id correlates this result with an already emitted SessionStatus from
    that generation. None identifies an uncorrelated result, not a successful call.
    Message operations include public acceptance evidence in message; a returned
    call without submission is not acceptance.
    """

    generation: int
    operation: ServiceOperation
    error: str = ""
    answer: AnswerResult | None = None
    failure_id: int | None = None
    message: MessageResult | None = None
