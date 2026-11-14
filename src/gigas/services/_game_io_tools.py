"""Compose one admitted input with its conversation's observation and records."""

import asyncio
from typing import TYPE_CHECKING

from gigas.game_io.observation import ObservationError
from gigas.services.input import OperationResult
from gigas.storage.input_events import OperationFinished, OperationStarted

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.game_io.input.models import InputStep
    from gigas.game_io.observation import Observation
    from gigas.services.observation import ObservationService
    from gigas.services.recording import RecordingService

type InputStart = Callable[
    [tuple[InputStep, ...]], tuple[str, asyncio.Task[OperationResult]] | OperationResult
]


async def act_and_observe(
    sequence: "tuple[InputStep, ...]",
    *,
    start_input: InputStart,
    observations: "ObservationService",
    recording: "RecordingService",
    thread_id: str,
) -> "tuple[OperationResult, Observation | ObservationError | None]":
    """Return input facts and a separate observation outcome without awaiting saves.

    Args:
        sequence: Validated finite sequence.
        start_input: Synchronous admission supplied by the conversation owner.
        observations: Observation service fixed for this conversation.
        recording: Persistence destination fixed before admission.
        thread_id: Actual conversation ID, not an inferred user-request association.

    Returns:
        Input result and, on successful input, its latest image or observation error.
        Admission rejection performs no observation and creates no operation record.

    Raises:
        asyncio.CancelledError: The requester cancelled. Its input is released and
            recorded first; already submitted encoding and saves remain owned.
    """
    admitted = start_input(sequence)
    if isinstance(admitted, OperationResult):
        return admitted, None
    operation_id, task = admitted
    recording.submit_input(OperationStarted(operation_id, sequence, thread_id))
    result = OperationResult("INTERRUPTED", "input", operation_id)
    observation = None
    observation_error = None
    cancelled_observation = False
    try:
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller is not None and caller.cancelling():
                if not task.done() and not task.cancelling():
                    task.cancel()
                recovered = await asyncio.shield(asyncio.gather(task, return_exceptions=True))
                if isinstance(recovered[0], OperationResult):
                    result = recovered[0]
                raise
            # Explicit input stop before the operation coroutine first ran.
            if not task.cancelled():
                raise
        if result.code == "OK":
            try:
                observation = await observations.observe(thread_id=thread_id)
            except asyncio.CancelledError:
                cancelled_observation = True
                raise
            except ObservationError as error:
                observation_error = error
            except Exception as error:  # noqa: BLE001 -- preserve completed physical input.
                observation_error = ObservationError("OBSERVATION_ENCODING_ERROR", str(error))
        return result, observation_error or observation
    finally:
        recording.submit_input(
            OperationFinished(
                operation_id,
                "completed"
                if result.code == "OK"
                else "interrupted"
                if result.code == "INTERRUPTED"
                else "failed",
                result.completed_steps,
                observation_id=observation.observation_id if observation is not None else None,
                phase="observe" if observation_error or cancelled_observation else result.phase,
                reason=str(observation_error)
                if observation_error
                else "観測応答が取り消されました"
                if cancelled_observation
                else result.message,
                input_sent=result.input_sent,
                neutral=result.neutral,
            )
        )
