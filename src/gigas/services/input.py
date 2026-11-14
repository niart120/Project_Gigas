"""One input connection and one finite, interruptible operation at a time."""

import asyncio
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import uuid4

from gigas.game_io.input.models import InputState, InputStep, InvalidSequenceError, validate_state

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from gigas.game_io.input.config import InputProtocol


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
    """Connection setup failed, with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        """Keep the setup failure's category and explanation."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Input outcome; code OK denotes completion, not physical device acceptance.

    Observation and persistence are composed by the caller after input finishes.
    input_sent=unknown and neutral=unconfirmed never mean verified delivery.
    """

    code: str
    phase: str = "accept"
    operation_id: str | None = None
    completed_steps: int = 0
    input_sent: Literal["none", "sent", "unknown"] = "none"
    neutral: Literal["not_needed", "sent", "unconfirmed"] = "not_needed"
    message: str = ""


class InputService:
    """Own input on one asyncio loop, independently of observation and persistence.

    start() synchronously admits one sequence with no queue. stop() cancels only
    the current operation; drain_input() joins its release and reports its result.
    A completed stop does not disable later input. The conversation owner rejects
    requests while its own stop is pending. This service owns no conversation,
    observation or persistence state.
    """

    def __init__(self) -> None:
        """Prepare the input owner without connecting or creating a chat archive."""
        self._device: InputDevice | None = None
        self._available = False
        self._operation: asyncio.Task[OperationResult] | None = None
        self._connection_task: asyncio.Task[None] | None = None
        self._connect_cancelled = False

    @property
    def connected(self) -> bool:
        """Return whether opening and initial neutral succeeded and no I/O failed."""
        return self._available

    async def connect(self, device: InputDevice | None) -> None:
        """Stop, close the old owner, then open the new device, or disconnect.

        Execution stop leaves this transition running; close suppresses or closes
        the candidate. Requests are rejected until the transition finishes.

        Raises:
            InputServiceError: Switching is busy, or open/close fails. A failed
                close retains its owner and prevents opening the next device.
        """
        if self._connection_task is not None and not self._connection_task.done():
            message = "入力接続の切替中です"
            raise InputServiceError("BUSY", message)
        self.stop()
        self._available = False
        self._connect_cancelled = False
        task = asyncio.create_task(self._connect(device))
        self._connection_task = task
        task.add_done_callback(_retrieve_exception)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            self._connect_cancelled = True
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

    def stop(self) -> asyncio.Task[OperationResult] | None:
        """Cancel once and return the captured operation for release-result recovery.

        Returning the task lets a stop caller collect its result even when the
        normal completion callback clears this service's current-operation slot.
        """
        task = self._operation
        if task is not None and not task.done() and not task.cancelling():
            task.cancel()
        return task

    async def drain_input(self) -> OperationResult | None:
        """Wait for input and release, returning their outcome when execution began.

        An operation cancelled before its coroutine starts returns None and sends
        no input. Cancelling this wait leaves the operation owned by this service.
        """
        task = self._operation
        if task is None:
            return None
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if task.cancelled() and current is not None and not current.cancelling():
                return None
            raise

    async def close(self) -> None:
        """Stop and close input; observation/record persistence is drained separately.

        Raises:
            InputServiceError: Closing fails; the connection owner is retained.
        """
        self._connect_cancelled = True
        self._available = False
        self.stop()
        await self.drain_input()
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

    def start(
        self, sequence: tuple[InputStep, ...]
    ) -> tuple[str, asyncio.Task[OperationResult]] | OperationResult:
        """Admit a finite sequence and register its task without yielding.

        Returns:
            The operation ID and owned task, or an immediate rejection with no
            operation ID and no input. Await the task through shield; on request
            cancellation, cancel that captured task once and join its cleanup.
            A task cancelled before it starts has no input result or side effects.
        """
        device = self._device
        if not self._available or device is None:
            return OperationResult("INPUT_DISABLED", message="入力機器を使用できません")
        if self._operation is not None and not self._operation.done():
            return OperationResult("BUSY", message="入力操作を実行中です")
        if not sequence:
            return OperationResult("INVALID_SEQUENCE", message="1要素以上の入力が必要です")
        try:
            for step in sequence:
                validate_state(step.state, device.protocol)
        except InvalidSequenceError as error:
            return OperationResult("INVALID_SEQUENCE", message=str(error))
        operation_id = f"operation-{uuid4()}"
        task = asyncio.create_task(self._execute(sequence, device, operation_id))
        self._operation = task
        task.add_done_callback(self._release_operation)
        return operation_id, task

    def _release_operation(self, task: asyncio.Task[OperationResult]) -> None:
        if self._operation is task:
            self._operation = None

    async def _execute(
        self,
        sequence: tuple[InputStep, ...],
        device: InputDevice,
        operation_id: str,
    ) -> OperationResult:
        result = OperationResult("OK", "input", operation_id)
        neutral_attempted = False
        transport_failed = False
        try:
            for step in sequence:
                result = replace(result, phase="input")
                if step.kind == "press":
                    neutral_attempted = False
                    result = replace(result, input_sent="unknown", neutral="unconfirmed")
                    cancelled = await _settle_io(device.send(step.state))
                    result = replace(result, input_sent="sent")
                    if cancelled:
                        raise asyncio.CancelledError  # noqa: TRY301 - retain delivery before interruption.
                    await _wait(step.duration_ms)
                    neutral_attempted = True
                    cancelled = await _settle_io(device.send(InputState()))
                    result = replace(result, neutral="sent")
                    if cancelled:
                        result = replace(result, completed_steps=result.completed_steps + 1)
                        raise asyncio.CancelledError  # noqa: TRY301 - retain delivery before interruption.
                else:
                    await _wait(step.duration_ms)
                result = replace(result, completed_steps=result.completed_steps + 1)
            result = replace(result, phase="")
        except asyncio.CancelledError:
            result = replace(result, code="INTERRUPTED", message="操作が停止されました")
        except Exception as error:  # noqa: BLE001 - release input before reporting failure.
            transport_failed = True
            result = replace(result, code="DEVICE_UNAVAILABLE", message=str(error))
        if result.neutral == "unconfirmed" and not neutral_attempted:
            try:
                await _settle_io(device.send(InputState()))
                result = replace(result, neutral="sent")
            except OSError as error:
                result = replace(
                    result, code="DEVICE_UNAVAILABLE", phase="neutral", message=str(error)
                )
                transport_failed = True
        if result.neutral == "unconfirmed":
            transport_failed = True
        if transport_failed:
            try:
                await _settle_io(self._close_device())
            except OSError as error:
                result = replace(result, message=f"{result.message}; 接続終了失敗: {error}")
        return result


async def _settle_io(operation: "Coroutine[object, object, None]") -> bool:
    # Keep the transport result separate from cancellation of the operation.
    task = asyncio.create_task(operation)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.shield(task)
        return True
    return False


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
