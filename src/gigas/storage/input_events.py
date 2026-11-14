"""Typed operation records without run ownership or input permissions."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from gigas.game_io.input.models import InputStep


@dataclass(frozen=True, slots=True)
class OperationStarted:
    """The accepted complete sequence, recorded before any physical input.

    The optional turn_id is an observed runtime ID, never a placeholder.
    """

    operation_id: str
    sequence: "tuple[InputStep, ...]"
    thread_id: str
    turn_id: str | None = None


@dataclass(frozen=True, slots=True)
class OperationFinished:
    """Execution outcome without connection details or a physical-delivery guarantee.

    Attributes:
        operation_id: Corresponding operation start.
        result: Input execution result: completed, interrupted, or failed.
        completed_steps: Steps finished through neutral delivery or wait completion.
        observation_id: Finalized observation ID, even when its PNG save later fails.
        phase: Failed or cancelled stage, including observe after completed input.
        reason: Cause of interruption or failure; empty for ordinary completion.
        input_sent: none, sent, or unknown for attempted state delivery.
        neutral: not_needed, sent, or unconfirmed; sent is not physical verification.
    """

    operation_id: str
    result: Literal["completed", "interrupted", "failed"]
    completed_steps: int
    observation_id: str | None = None
    phase: str = ""
    reason: str = ""
    input_sent: Literal["none", "sent", "unknown"] = "none"
    neutral: Literal["not_needed", "sent", "unconfirmed"] = "not_needed"


type InputEvent = OperationStarted | OperationFinished


def input_record(event: InputEvent) -> tuple[str, dict[str, object]]:
    """Return detached JSON data for an accepted operation or its result."""
    payload: dict[str, object] = {"operation_id": event.operation_id}
    if isinstance(event, OperationStarted):
        kind = "input.operation_started"
        payload.update(
            thread_id=event.thread_id,
            sequence=[step.to_json() for step in event.sequence],
        )
        if event.turn_id is not None:
            payload["turn_id"] = event.turn_id
    else:
        kind = "input.operation_finished"
        payload.update(
            result=event.result,
            completed_steps=event.completed_steps,
            input_sent=event.input_sent,
            neutral=event.neutral,
        )
        if event.observation_id is not None:
            payload["observation_id"] = event.observation_id
        if event.phase:
            payload["phase"] = event.phase
        if event.reason:
            payload["reason"] = event.reason
    return kind, payload
