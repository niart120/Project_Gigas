"""One input connection, run permission and interruptible recorded operation."""

import asyncio
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import uuid4

from gigas.game_io.input.models import InputState, InputStep, InvalidSequenceError, validate_state
from gigas.game_io.observation import ObservationError
from gigas.services.input_observation import observe_after
from gigas.services.observation import ObservationInterrupted
from gigas.storage.input_events import OperationFinished, OperationStarted, RunFinished, RunStarted

if TYPE_CHECKING:
    from gigas.game_io.capture import CaptureReader
    from gigas.game_io.input.config import InputProtocol
    from gigas.game_io.observation import Observation
    from gigas.services.observation import ObservationService


class InputDevice(Protocol):
    """State-level input transport; opening establishes neutral before returning."""

    @property
    def protocol(self) -> "InputProtocol":
        """Return this connection's fixed input capabilities."""
        ...

    async def open(self) -> None:
        """Open and establish neutral; retain submitted I/O after cancellation."""
        ...

    async def send(self, state: InputState) -> None:
        """Send a complete state, raising OSError for uncertain delivery."""
        ...

    async def close(self) -> None:
        """Close after submitted I/O settles, retaining ownership on failure."""
        ...


class InputServiceError(RuntimeError):
    """Run or connection setup failed, with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        """Keep the setup failure's category and explanation."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True, eq=False)
class InputGrant:
    """Identity-bound run context issued by begin_run, never accepted by value.

    A returned grant can remain disabled when the run began without a connection,
    or when stop occurred during its start record. Stop never revives old grants.
    """

    request_id: str
    run_id: str
    thread_id: str
    turn_id: str | None = None


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Input outcome; code OK denotes completion, not physical device acceptance.

    observation can also accompany interruption during encoding. Its ID belongs
    to the result record, but does not convert interrupted execution into success.
    input_sent=unknown and neutral=unconfirmed never mean verified delivery.
    """

    code: str
    phase: str = "accept"
    operation_id: str | None = None
    completed_steps: int = 0
    input_sent: Literal["none", "sent", "unknown"] = "none"
    neutral: Literal["not_needed", "sent", "unconfirmed"] = "not_needed"
    observation: "Observation | None" = None
    message: str = ""


class InputService:
    """Own input on one asyncio loop, borrowing capture and observation services.

    Args:
        reader: Capture state used for admission and post-action frame selection.
        observations: Existing encoder and shared recording owner. Its recording
            failure callback is assigned to this service's common stop path.

    Start and finish runs explicitly. One operation is admitted at a time, with
    no queue. stop() revokes permission immediately; await drain_input() to join
    input cleanup. close() closes the device but does not drain image/record saves.
    The application must drain observations before closing its writer or loop.
    """

    def __init__(self, reader: "CaptureReader", observations: "ObservationService") -> None:
        """Borrow existing capture and persistence without connecting anything."""
        self._reader = reader
        self._observations = observations
        self._recording = observations.recording
        self._recording.on_failure = self._recording_failed
        self._device: InputDevice | None = None
        self._available = False
        self._run: InputGrant | None = None
        self._grant: InputGrant | None = None
        self._revoked = True
        self._suspended = False
        self._operation: asyncio.Task[OperationResult] | None = None
        self._finishing = False
        self._connecting = False
        self._connection_task: asyncio.Task[None] | None = None
        self._connect_cancelled = False
        self._stop_code = "INTERRUPTED"
        self._stop_reason = "操作が停止されました"

    @property
    def connected(self) -> bool:
        """Return whether opening and initial neutral succeeded and no I/O failed."""
        return self._available

    async def connect(self, device: InputDevice | None) -> None:
        """Stop, close the old owner, then open the new device, or disconnect.

        Connection changes never issue a new grant. A stop during this transition
        suppresses a not-yet-started open, or closes the candidate after open settles.

        Raises:
            InputServiceError: Switching is busy, or open/close fails. A failed
                close retains its owner and prevents opening the next device.
        """
        if self._connecting:
            message = "入力接続の切替中です"
            raise InputServiceError("BUSY", message)
        self.stop("入力接続を変更しました")
        self._connecting = True
        self._connect_cancelled = False
        task = asyncio.create_task(self._connect(device))
        self._connection_task = task
        task.add_done_callback(_retrieve_exception)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            self.stop()
            raise

    async def _connect(self, device: InputDevice | None) -> None:
        try:
            await self.drain_input()
            await self._close_device()
            if device is None or self._connect_cancelled:
                return
            self._device = device
            try:
                await device.open()
            except BaseException:
                await self._close_device()
                raise
            if self._connect_cancelled:
                await self._close_device()
            else:
                self._available = True
        except OSError as error:
            raise InputServiceError("DEVICE_UNAVAILABLE", str(error)) from error
        finally:
            self._connecting = False

    async def begin_run(
        self, message: str, thread_id: str, *, turn_id: str | None = None
    ) -> InputGrant:
        """Persist an explicit request, then issue its identity-bound run context.

        A run beginning without input remains disabled. Stop during the save also
        leaves it disabled. Call finish_run() before beginning another run.

        Raises:
            InputServiceError: A run/transition is active or its start cannot be saved.
        """
        if self._run is not None or self._connecting:
            message_text = "前の実行または入力接続の切替が終了していません"
            raise InputServiceError("BUSY", message_text)
        grant = InputGrant(f"request-{uuid4()}", f"run-{uuid4()}", thread_id, turn_id)
        self._run = grant
        self._revoked = False
        self._suspended = False
        self._stop_code = "INTERRUPTED"
        self._stop_reason = "操作が停止されました"
        enabled = self._available
        try:
            await self._recording.record_input(
                RunStarted(grant.request_id, grant.run_id, thread_id, message)
            )
        except asyncio.CancelledError:
            self.stop()
            raise
        except Exception as error:
            self.stop(str(error), code="STORAGE_ERROR")
            raise InputServiceError("STORAGE_ERROR", str(error)) from error
        if not self._revoked and self._run is grant and enabled:
            self._grant = grant
        if self._run is grant:
            self._observations.thread_id = thread_id
        return grant

    def stop(self, reason: str = "操作が停止されました", *, code: str = "INTERRUPTED") -> None:
        """Revoke permission and cancel the current operation once, without awaiting I/O."""
        self._grant = None
        self._revoked = True
        self._connect_cancelled = True
        if self._stop_code != "STORAGE_ERROR" or code == "STORAGE_ERROR":
            self._stop_code = code
            self._stop_reason = reason
        self._cancel_operation()

    def suspend(self) -> None:
        """Temporarily reject input and cancel this sequence while preserving the run."""
        self._suspended = True
        self._cancel_operation()

    def resume(self) -> None:
        """Lift temporary suspension without restoring a revoked grant or old sequence."""
        self._suspended = False

    def _cancel_operation(self) -> None:
        task = self._operation
        if task is not None and not task.done() and not self._finishing and not task.cancelling():
            task.cancel()

    def _recording_failed(self, error: Exception) -> None:
        self.stop(str(error), code="STORAGE_ERROR")

    async def drain_input(self) -> None:
        """Wait for the owned operation's input and encoding cleanup, not saves.

        Cancelling this wait does not abandon the operation; call again to join it.
        """
        if self._operation is not None:
            await asyncio.shield(asyncio.gather(self._operation, return_exceptions=True))

    async def finish_run(self, reason: str) -> None:
        """Revoke, join input cleanup, then queue the run's terminal record."""
        self.stop(reason)
        await self.drain_input()
        grant = self._run
        if grant is not None:
            self._recording.submit_input(
                RunFinished(grant.request_id, grant.run_id, reason, grant.turn_id)
            )
            self._run = None

    async def close(self) -> None:
        """Stop and close input; observation/record persistence is drained separately.

        Raises:
            InputServiceError: Closing fails; the connection owner is retained.
        """
        await self.finish_run("closed")
        if self._connection_task is not None:
            await asyncio.shield(asyncio.gather(self._connection_task, return_exceptions=True))
        try:
            await self._close_device()
        except OSError as error:
            raise InputServiceError("DEVICE_UNAVAILABLE", str(error)) from error

    async def _close_device(self) -> None:
        self._available = False
        if self._device is not None:
            await self._device.close()
            self._device = None

    async def act(self, grant: InputGrant, sequence: tuple[InputStep, ...]) -> OperationResult:
        """Admit, record and execute a finite sequence through post-action encoding.

        Returns:
            An explicit success or failure outcome. Admission rejection sends no
            input and has no operation_id. Input errors are never retried.

        External cancellation calls the common stop path, raises CancelledError,
        and leaves cleanup owned for drain_input().
        """
        device = self._device
        if (
            self._grant is None
            or grant is not self._grant
            or self._suspended
            or not self._available
            or device is None
        ):
            return OperationResult("INPUT_DISABLED", message="この実行では入力できません")
        if self._operation is not None and not self._operation.done():
            return OperationResult("BUSY", message="入力操作を実行中です")
        snapshot = self._reader.snapshot()
        if snapshot.phase != "streaming" or snapshot.frame is None:
            return OperationResult("DEVICE_UNAVAILABLE", message="観測できる映像がありません")
        if not isinstance(sequence, tuple) or not sequence:
            return OperationResult("INVALID_SEQUENCE", message="1要素以上の入力タプルが必要です")
        if any(not isinstance(step, InputStep) for step in sequence):
            return OperationResult("INVALID_SEQUENCE", message="検証済みの入力要素が必要です")
        try:
            for step in sequence:
                validate_state(step.state, device.protocol)
        except InvalidSequenceError as error:
            return OperationResult("INVALID_SEQUENCE", message=str(error))
        operation_id = f"operation-{uuid4()}"
        self._finishing = False
        task = asyncio.create_task(
            self._execute(grant, sequence, device, snapshot.generation, operation_id)
        )
        self._operation = task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if task.cancelled() and current is not None and not current.cancelling():
                return OperationResult(
                    self._stop_code, operation_id=operation_id, message=self._stop_reason
                )
            self.stop()
            raise

    async def _execute(
        self,
        grant: InputGrant,
        sequence: tuple[InputStep, ...],
        device: InputDevice,
        generation: int,
        operation_id: str,
    ) -> OperationResult:
        result = OperationResult("OK", "record", operation_id)
        neutral_attempted = False
        transport_failed = False
        try:
            await self._recording.record_input(
                OperationStarted(
                    grant.request_id, grant.run_id, operation_id, sequence, grant.turn_id
                )
            )
            for step in sequence:
                result = replace(result, phase="input")
                if step.kind == "press":
                    neutral_attempted = False
                    result = replace(result, input_sent="unknown", neutral="unconfirmed")
                    await device.send(step.state)
                    result = replace(result, input_sent="sent")
                    await _wait(step.duration_ms)
                    neutral_attempted = True
                    neutral_task = asyncio.create_task(device.send(InputState()))
                    try:
                        await asyncio.shield(neutral_task)
                    except asyncio.CancelledError:
                        # The release is already submitted. Keep its delivery result
                        # even though the remainder of the sequence is cancelled.
                        await asyncio.shield(neutral_task)
                        result = replace(
                            result, neutral="sent", completed_steps=result.completed_steps + 1
                        )
                        raise
                    result = replace(result, neutral="sent")
                else:
                    await _wait(step.duration_ms)
                result = replace(result, completed_steps=result.completed_steps + 1)
            boundary_ns = time.monotonic_ns()
            result = replace(result, phase="observe")
            observation = await observe_after(
                self._reader, self._observations, generation, boundary_ns
            )
            result = replace(result, phase="", observation=observation)
        except asyncio.CancelledError as error:
            result = replace(
                result,
                code=self._stop_code,
                message=self._stop_reason,
                observation=error.observation
                if isinstance(error, ObservationInterrupted)
                else None,
            )
        except ObservationError as error:
            result = replace(result, code=error.code, message=str(error))
        except Exception as error:  # noqa: BLE001 - record the terminal outcome before cleanup.
            transport_failed = result.phase == "input"
            code = {
                "input": "DEVICE_UNAVAILABLE",
                "record": "STORAGE_ERROR",
                "observe": "OBSERVATION_ENCODING_ERROR",
            }[result.phase]
            result = replace(result, code=code, message=str(error))
        self._finishing = True
        if result.code != "OK" and result.phase == "observe":
            self.stop(result.message, code=result.code)
        if result.neutral == "unconfirmed" and not neutral_attempted:
            try:
                await device.send(InputState())
                result = replace(result, neutral="sent")
            except OSError as error:
                result = replace(
                    result, code="DEVICE_UNAVAILABLE", phase="neutral", message=str(error)
                )
                transport_failed = True
        if result.neutral == "unconfirmed":
            transport_failed = True
        if transport_failed:
            self.stop(result.message, code="DEVICE_UNAVAILABLE")
            try:
                await self._close_device()
            except OSError as error:
                result = replace(result, message=f"{result.message}; 接続終了失敗: {error}")
        self._recording.submit_input(
            OperationFinished(
                grant.run_id,
                operation_id,
                "completed"
                if result.code == "OK"
                else "interrupted"
                if result.code == "INTERRUPTED"
                else "failed",
                result.completed_steps,
                observation_id=result.observation.observation_id
                if result.observation is not None
                else None,
                phase=result.phase,
                reason=result.message,
                input_sent=result.input_sent,
                neutral=result.neutral,
            )
        )
        return result


async def _wait(duration_ms: int) -> None:
    # Bounded conversions keep arbitrary finite integer durations representable;
    # each await remains interruptible. This is not an operation-time limit.
    remaining = duration_ms
    while remaining:
        chunk = min(remaining, 3_600_000)
        await asyncio.sleep(chunk / 1000)
        remaining -= chunk


def _retrieve_exception(task: asyncio.Task[None]) -> None:
    # A cancelled connect requester leaves the transition owned until close joins it.
    if not task.cancelled():
        task.exception()
