"""Typed operation records without run ownership or input permissions."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from gigas.game_io.input.models import InputStep


@dataclass(frozen=True, slots=True)
class OperationStarted:
    """The accepted complete sequence without project or conversation metadata.

    Attributes:
        operation_id: Identity shared with the final operation outcome.
        sequence: Accepted input steps, submitted for recording before execution.
    """

    operation_id: str
    sequence: "tuple[InputStep, ...]"


@dataclass(frozen=True, slots=True)
class OperationFinished:
    """Execution outcome without connection details or a physical-delivery guarantee.

    Attributes:
        operation_id: Corresponding operation start.
        result: Input execution result: completed, interrupted, or failed.
        completed_steps: Steps whose specified hold or wait duration finished.
        observation_id: Finalized observation ID, even when its PNG save later fails.
        phase: Failed or cancelled stage, including observe after completed input.
        reason: Cause of interruption or failure; empty for ordinary completion.
        input_sent: none, sent, or unknown for attempted state delivery.
        neutral: not_needed, sent, or unconfirmed; sent is not physical verification.
        samples: Ordered scheduled captures. Missing image IDs mark missed captures.
    """

    operation_id: str
    result: Literal["completed", "interrupted", "failed"]
    completed_steps: int
    observation_id: str | None = None
    phase: str = ""
    reason: str = ""
    input_sent: Literal["none", "sent", "unknown"] = "none"
    neutral: Literal["not_needed", "sent", "unconfirmed"] = "not_needed"
    samples: tuple[dict[str, object], ...] = ()


type InputEvent = OperationStarted | OperationFinished


def input_record(event: InputEvent) -> tuple[str, dict[str, object]]:
    """Return detached JSON data for an accepted operation or its result."""
    payload: dict[str, object] = {"operation_id": event.operation_id}
    if isinstance(event, OperationStarted):
        kind = "input.operation_started"
        payload["sequence"] = [step.to_json() for step in event.sequence]
    else:
        kind = "input.operation_finished"
        payload.update(
            result=event.result,
            completed_steps=event.completed_steps,
            input_sent=event.input_sent,
            neutral=event.neutral,
        )
        if event.samples:
            payload["samples"] = list(event.samples)
        if event.observation_id is not None:
            payload["observation_id"] = event.observation_id
        if event.phase:
            payload["phase"] = event.phase
        if event.reason:
            payload["reason"] = event.reason
    return kind, payload
