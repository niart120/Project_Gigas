"""Typed input records and their persisted payload validation."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from gigas.game_io.input.config import PROTOCOLS
from gigas.game_io.input.models import InvalidSequenceError, parse_sequence

if TYPE_CHECKING:
    from gigas.game_io.input.models import InputStep


@dataclass(frozen=True, slots=True)
class RunStarted:
    """One explicit user request, recorded before granting input permission."""

    request_id: str
    run_id: str
    thread_id: str
    message: str


@dataclass(frozen=True, slots=True)
class OperationStarted:
    """The accepted complete sequence, recorded before any physical input.

    The optional turn_id is an observed runtime ID, never a placeholder.
    """

    request_id: str
    run_id: str
    operation_id: str
    sequence: "tuple[InputStep, ...]"
    turn_id: str | None = None


@dataclass(frozen=True, slots=True)
class OperationFinished:
    """Execution outcome without connection details or a physical-delivery guarantee.

    Attributes:
        run_id: Run that owned the operation.
        operation_id: Corresponding operation start.
        result: completed, interrupted, or failed.
        completed_steps: Steps finished through neutral delivery or wait completion.
        observation_id: Finalized observation ID, even when its PNG save later fails.
        phase: Stage of interruption or failure; empty for ordinary completion.
        reason: Cause of interruption or failure; empty for ordinary completion.
        input_sent: none, sent, or unknown for attempted state delivery.
        neutral: not_needed, sent, or unconfirmed; sent is not physical verification.
    """

    run_id: str
    operation_id: str
    result: Literal["completed", "interrupted", "failed"]
    completed_steps: int
    observation_id: str | None = None
    phase: str = ""
    reason: str = ""
    input_sent: Literal["none", "sent", "unknown"] = "none"
    neutral: Literal["not_needed", "sent", "unconfirmed"] = "not_needed"


@dataclass(frozen=True, slots=True)
class RunFinished:
    """Run termination after permission revocation and submitted operation results.

    reason distinguishes ordinary completion, stop, and start failure.
    turn_id is omitted until the actual runtime value is known.
    """

    request_id: str
    run_id: str
    reason: str
    turn_id: str | None = None


type InputEvent = RunStarted | OperationStarted | OperationFinished | RunFinished


def input_record(event: InputEvent) -> tuple[str, dict[str, object]]:
    """Return detached JSON data for one typed event.

    Raises:
        ValueError: The event or its payload is invalid.
    """
    payload: dict[str, object] = {"run_id": event.run_id}
    if isinstance(event, RunStarted):
        kind = "input.run_started"
        payload.update(
            request_id=event.request_id, thread_id=event.thread_id, message=event.message
        )
    elif isinstance(event, OperationStarted):
        kind = "input.operation_started"
        payload.update(
            request_id=event.request_id,
            operation_id=event.operation_id,
            sequence=[step.to_json() for step in event.sequence],
        )
        if event.turn_id is not None:
            payload["turn_id"] = event.turn_id
    elif isinstance(event, OperationFinished):
        kind = "input.operation_finished"
        payload.update(
            operation_id=event.operation_id,
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
    else:
        kind = "input.run_finished"
        payload.update(request_id=event.request_id, reason=event.reason)
        if event.turn_id is not None:
            payload["turn_id"] = event.turn_id
    validate_input_payload(kind, payload)
    return kind, payload


def validate_input_payload(kind: str, value: object) -> None:
    """Validate a known input record without requiring hardware or prior log rows.

    Raises:
        TypeError: The payload is not an object.
        ValueError: Required identifiers, sequence, or execution results are invalid.
    """
    if not isinstance(value, dict):
        message = "入力記録にはpayload objectが必要です"
        raise TypeError(message)
    _text(value.get("run_id"))
    if "turn_id" in value:
        _text(value["turn_id"])
    if kind != "input.operation_finished":
        _text(value.get("request_id"))
    if kind == "input.run_started":
        _text(value.get("thread_id"))
        _text(value.get("message"))
    elif kind == "input.operation_started":
        _text(value.get("operation_id"))
        # The record omits connection details. Reuse the existing parser to verify
        # that one supported device could accept the complete recorded sequence.
        for protocol in PROTOCOLS:
            try:
                parse_sequence(value.get("sequence"), protocol)
            except InvalidSequenceError:
                continue
            break
        else:
            message = "記録された入力列が不正です"
            raise ValueError(message)
    elif kind == "input.operation_finished":
        _text(value.get("operation_id"))
        completed = value.get("completed_steps")
        if (
            type(completed) is not int
            or completed < 0
            or value.get("result") not in ("completed", "interrupted", "failed")
            or value.get("input_sent") not in ("none", "sent", "unknown")
            or value.get("neutral") not in ("not_needed", "sent", "unconfirmed")
        ):
            message = "入力の実行結果が不正です"
            raise ValueError(message)
        if value["result"] != "completed":
            _text(value.get("phase"))
            _text(value.get("reason"))
        if "observation_id" in value:
            observation_id = _text(value["observation_id"])
            identifier = UUID(observation_id.removeprefix("obs-"))
            if identifier.version != 4 or observation_id != f"obs-{identifier}":
                message = "入力結果の観測IDが不正です"
                raise ValueError(message)
    elif kind == "input.run_finished":
        _text(value.get("reason"))
    else:
        message = "未対応の入力イベントです"
        raise ValueError(message)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        message = "入力記録には空でない文字列が必要です"
        raise ValueError(message)
    return value
