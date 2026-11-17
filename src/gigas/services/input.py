"""One input connection and one finite, interruptible operation at a time."""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import uuid4

from gigas.game_io.input.models import InputState, InputStep, InvalidSequenceError, validate_state
from gigas.game_io.input.serial import SerialInput, SerialPort, list_serial_ports

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from gigas.game_io.input.config import InputProtocol, SerialConfig


@dataclass(frozen=True, slots=True)
class InputConnectionResult:
    """Applied serial configuration and connection outcome for the application."""

    config: "SerialConfig | None"
    connected: bool
    error: str = ""


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


@dataclass(slots=True)
class _ManualInput:
    pending: deque[InputState | None] = field(default_factory=deque)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    state: InputState = field(default_factory=InputState)
    releasing: bool = False


class InputService:
    """Own input on one asyncio loop, independently of observation and persistence.

    start() synchronously admits one sequence with no queue. stop() cancels only
    the current operation; drain_input() joins its release and reports its result.
    A completed stop does not disable later input. The conversation owner rejects
    requests while its own stop is pending. This service owns no conversation,
    observation or persistence state.
    """

    def __init__(
        self,
        *,
        factory: "Callable[[SerialConfig], InputDevice] | None" = None,
        on_connection_changed: "Callable[[InputConnectionResult], None] | None" = None,
        on_busy_changed: "Callable[[bool], None] | None" = None,
        on_manual_result: "Callable[[OperationResult], None] | None" = None,
    ) -> None:
        """Prepare the input owner without connecting or creating a chat archive.

        Args:
            factory: Construct an unopened device from settings, or None for SerialInput.
            on_connection_changed: Synchronous callback after each settings application.
            on_manual_result: Synchronous callback when a manual operation finishes.
            on_busy_changed: Optional synchronous callback on the owning loop.
                True follows admitted task ownership; False follows completion
                of the current task, including neutral recovery. Rejections and
                stop requests alone do not notify. The callback must not raise.
        """
        self._on_busy_changed = on_busy_changed
        self._on_manual_result = on_manual_result
        self._factory = SerialInput if factory is None else factory
        self._on_connection_changed = on_connection_changed
        self._config: SerialConfig | None = None
        self._target: SerialConfig | None = None
        self._pending = False
        self._apply_task: asyncio.Task[None] | None = None
        self._closing = False
        self._device: InputDevice | None = None
        self._available = False
        self._operation: asyncio.Task[OperationResult] | None = None
        self._manual: _ManualInput | None = None
        self._closing_operation: asyncio.Task[OperationResult] | None = None
        self._connection_task: asyncio.Task[None] | None = None
        self._connect_cancelled = False

    def request_config(self, config: "SerialConfig | None") -> None:
        """Retain the latest settings for application after caller coordination.

        Application coordination may wait for its execution to stop before calling
        apply_config. Repeated requests replace only the pending target.
        """
        if self._closing:
            return
        self._target = config
        self._pending = True

    @property
    def config_pending(self) -> bool:
        """Whether the latest settings still need an application attempt."""
        return self._pending

    @property
    def connection_change_required(self) -> bool:
        """Whether the latest target differs from a healthy applied connection."""
        return self._target != self._config or (self._target is not None and not self.connected)

    @property
    def changing(self) -> bool:
        """Whether settings or a submitted connection transition still await completion."""
        return (
            self._pending
            or (self._apply_task is not None and not self._apply_task.done())
            or (self._connection_task is not None and not self._connection_task.done())
        )

    def reject_config(self, reason: str) -> None:
        """Report a failed application prerequisite without starting the pending device."""
        self._pending = False
        if self._on_connection_changed is not None:
            self._on_connection_changed(InputConnectionResult(self._target, self.connected, reason))

    async def apply_config(self) -> None:
        """Apply one latest target, retaining ownership through caller cancellation.

        Settings arriving during this attempt remain config_pending. The caller
        coordinates its execution again before requesting the next attempt.
        """
        if self._closing:
            return
        if self._apply_task is None or self._apply_task.done():
            self._apply_task = asyncio.create_task(self._apply_config())
            self._apply_task.add_done_callback(_retrieve_exception)
        await asyncio.shield(self._apply_task)

    async def _apply_config(self) -> None:
        if self._pending and not self._closing:
            config = self._target
            self._pending = False
            error = ""
            try:
                if config is None or config != self._config or not self.connected:
                    device = None if config is None else self._factory(config)
                    await self.connect(device)
                self._config = config
            except Exception as failure:
                logger.exception(
                    "Input operation failed",
                    extra={"event": "input.failed", "operation": "_apply_config"},
                )
                error = str(failure)
            if self._on_connection_changed is not None:
                self._on_connection_changed(InputConnectionResult(config, self.connected, error))

    @staticmethod
    async def list_ports() -> tuple[SerialPort, ...]:
        """Enumerate current serial ports without opening a device."""
        return await asyncio.to_thread(list_serial_ports)

    def begin_close(self) -> None:
        """Withdraw unapplied settings and stop input before asynchronous cleanup."""
        if not self._closing:
            self._closing_operation = self.stop()
        self._closing = True
        self._pending = False
        self._connect_cancelled = True
        self._available = False

    @property
    def protocol(self) -> "InputProtocol | None":
        """Return capabilities of the connected owner, or None when unavailable."""
        return self._device.protocol if self.connected and self._device is not None else None

    @property
    def manual_available(self) -> bool:
        """Whether a manual update can continue or begin on this input owner."""
        return (
            self.connected
            and not self.changing
            and not self._closing
            and (
                not self.busy if self._manual is None else self.busy and not self._manual.releasing
            )
        )

    def update_manual(self, state: InputState) -> OperationResult:
        """Admit a manual state, retaining button transitions and latest unsent axes.

        Neutral is an update during a gesture. release_manual ends the gesture.
        No conversation or record archive is created. Invalid or busy input is
        rejected without sending; an empty idle update is a no-op.
        """
        manual = self._manual
        if manual is None and state == InputState():
            return OperationResult("OK")
        if not self.manual_available:
            code = "BUSY" if self.busy or self.changing or manual is not None else "INPUT_DISABLED"
            return OperationResult(code, message="手動入力を受け付けません")
        device = self._device
        if device is None:
            return OperationResult("INPUT_DISABLED", message="入力機器を使用できません")
        try:
            validate_state(state, device.protocol)
        except InvalidSequenceError as error:
            return OperationResult("INVALID_SEQUENCE", message=str(error))
        if manual is None:
            manual = self._manual = _ManualInput()
            task = asyncio.create_task(self._execute_manual(manual, device))
            self._operation = task
            task.add_done_callback(self._release_operation)
            if self._on_busy_changed is not None:
                self._on_busy_changed(True)
        if state != manual.state:
            if (
                manual.pending
                and (previous := manual.pending[-1]) is not None
                and (previous.buttons == state.buttons)
            ):
                manual.pending[-1] = state
            else:
                manual.pending.append(state)
            manual.state = state
            manual.changed.set()
        return OperationResult("OK")

    def stop_manual(self) -> asyncio.Task[OperationResult] | None:
        """Request manual release without waiting, returning the captured operation."""
        manual = self._manual
        if manual is None:
            return None
        if not manual.releasing:
            manual.releasing = True
            manual.pending.append(None)
            manual.changed.set()
        return self._operation

    async def release_manual(self) -> None:
        """Release only the captured manual gesture and wait through cancellation.

        Raises:
            InputServiceError: Sending or neutral recovery was not confirmed.
        """
        operation = self.stop_manual()
        if operation is None:
            return
        try:
            result = await asyncio.shield(operation)
        except asyncio.CancelledError:
            await asyncio.gather(operation, return_exceptions=True)
            raise
        _require_released(result)

    def stop_finite(self) -> asyncio.Task[OperationResult] | None:
        """Cancel the captured finite operation without touching a manual gesture."""
        return self.stop() if self._manual is None else None

    @property
    def connected(self) -> bool:
        """Return whether opening and initial neutral succeeded and no I/O failed."""
        return self._available

    @property
    def busy(self) -> bool:
        """Whether accepted input still owns execution or neutral-state recovery."""
        return self._operation is not None and not self._operation.done()

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
        operation = self.stop()
        self._available = False
        self._connect_cancelled = False
        task = asyncio.create_task(self._connect(device, operation))
        self._connection_task = task
        task.add_done_callback(_retrieve_exception)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            self._connect_cancelled = True
            self.stop()
            raise

    async def _connect(
        self, device: InputDevice | None, operation: asyncio.Task[OperationResult] | None
    ) -> None:
        try:
            if operation is not None:
                (outcome,) = await asyncio.gather(operation, return_exceptions=True)
                if isinstance(outcome, OperationResult) and outcome.code not in {
                    "OK",
                    "INTERRUPTED",
                }:
                    raise InputServiceError(outcome.code, outcome.message)
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
                logger.info("Input device connected", extra={"event": "input.connected"})
        except OSError as error:
            raise InputServiceError("DEVICE_UNAVAILABLE", str(error)) from error

    def stop(self) -> asyncio.Task[OperationResult] | None:
        """End manual input or cancel a finite operation, returning its captured task.

        Returning the task lets a stop caller collect its result even when the
        normal completion callback clears this service's current-operation slot.
        """
        if self._manual is not None:
            return self.stop_manual()
        task = self._operation
        if task is not None and not task.done() and not task.cancelling():
            task.cancel()
        return task

    async def drain_input(self) -> OperationResult | None:
        """Wait for input and release, returning their outcome when execution began.

        An operation cancelled before its coroutine starts returns None and sends
        no input. Cancelling this wait leaves the operation owned by this service.
        """
        return await self.wait_operation(self._operation)

    @staticmethod
    async def wait_operation(task: asyncio.Task[OperationResult] | None) -> OperationResult | None:
        """Wait for a captured operation without joining a subsequently accepted one.

        Cancellation before the operation starts returns None. Cancelling the
        waiter leaves the task owned by the input service.
        """
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
        self.begin_close()
        outcome: OperationResult | BaseException | None = None
        if self._closing_operation is not None:
            (outcome,) = await asyncio.shield(
                asyncio.gather(self._closing_operation, return_exceptions=True)
            )
        if self._apply_task is not None:
            await asyncio.shield(asyncio.gather(self._apply_task, return_exceptions=True))
        if self._connection_task is not None:
            await asyncio.shield(asyncio.gather(self._connection_task, return_exceptions=True))
        try:
            await self._close_device()
        except OSError as error:
            logger.exception(
                "Input operation failed", extra={"event": "input.failed", "operation": "close"}
            )
            raise InputServiceError("DEVICE_UNAVAILABLE", str(error)) from error
        if isinstance(outcome, OperationResult):
            _require_released(outcome)

    async def _close_device(self) -> None:
        self._available = False
        if self._device is not None:
            await self._device.close()
            self._device = None
            logger.info("Input device closed", extra={"event": "input.disconnected"})

    def start(
        self, sequence: tuple[InputStep, ...]
    ) -> tuple[str, asyncio.Task[OperationResult]] | OperationResult:
        """Admit a finite sequence and register its task without yielding.

        Args:
            sequence: Nonempty tuple of constructed InputStep values. Common
                invariants and nonemptiness are not revalidated here; every
                state is checked against the selected device's capabilities.

        Returns:
            The operation ID and owned task, or an immediate rejection with no
            operation ID and no input. Await the task through shield; on request
            cancellation, cancel that captured task once and join its cleanup.
            A task cancelled before it starts has no input result or side effects.
        """
        device = self._device
        if self.changing:
            return OperationResult("BUSY", message="入力接続の切替中です")
        if not self._available or device is None:
            return OperationResult("INPUT_DISABLED", message="入力機器を使用できません")
        if self._manual is not None or self.busy:
            return OperationResult("BUSY", message="入力操作を実行中です")
        try:
            for step in sequence:
                validate_state(step.state, device.protocol)
        except InvalidSequenceError as error:
            return OperationResult("INVALID_SEQUENCE", message=str(error))
        operation_id = f"operation-{uuid4()}"
        task = asyncio.create_task(self._execute(sequence, device, operation_id))
        self._operation = task
        task.add_done_callback(self._release_operation)
        if self._on_busy_changed is not None:
            self._on_busy_changed(True)
        return operation_id, task

    def _release_operation(self, task: asyncio.Task[OperationResult]) -> None:
        if self._operation is task:
            manual = self._manual
            self._manual = None
            self._operation = None
            if self._on_busy_changed is not None:
                self._on_busy_changed(False)
            if manual is not None and not task.cancelled() and self._on_manual_result is not None:
                self._on_manual_result(task.result())

    async def _execute_manual(self, manual: _ManualInput, device: InputDevice) -> OperationResult:
        result = OperationResult("OK", "input")
        failed = False
        neutral_attempted = False
        try:
            while True:
                while not manual.pending:
                    manual.changed.clear()
                    await manual.changed.wait()
                state = manual.pending.popleft()
                if state is None:
                    break
                neutral_attempted = state == InputState()
                result = replace(
                    result,
                    phase="neutral" if neutral_attempted else "input",
                    input_sent="unknown",
                    neutral="unconfirmed",
                )
                cancelled = await _settle_io(device.send(state))
                result = replace(
                    result,
                    input_sent="sent",
                    neutral="sent" if neutral_attempted else "unconfirmed",
                )
                if cancelled:
                    raise asyncio.CancelledError  # noqa: TRY301 -- preserve completed write.
        except asyncio.CancelledError:
            result = replace(result, code="INTERRUPTED", message="手動入力を停止しました")
        except Exception as error:
            logger.exception(
                "Input operation failed",
                extra={
                    "event": "input.failed",
                    "operation": "_execute_manual",
                    "operation_id": result.operation_id,
                    "phase": result.phase,
                },
            )
            failed = True
            result = replace(result, code="DEVICE_UNAVAILABLE", message=str(error))
        if result.neutral == "unconfirmed" and not neutral_attempted:
            try:
                await _settle_io(device.send(InputState()))
                result = replace(result, neutral="sent")
            except OSError as error:
                logger.exception(
                    "Input operation failed",
                    extra={
                        "event": "input.failed",
                        "operation": "_execute_manual",
                        "operation_id": result.operation_id,
                        "phase": result.phase,
                    },
                )
                result = replace(
                    result, code="DEVICE_UNAVAILABLE", phase="neutral", message=str(error)
                )
                failed = True
        if failed or result.neutral == "unconfirmed":
            try:
                await _settle_io(self._close_device())
            except OSError as error:
                logger.exception(
                    "Input operation failed",
                    extra={
                        "event": "input.failed",
                        "operation": "_execute_manual",
                        "operation_id": result.operation_id,
                        "phase": result.phase,
                    },
                )
                result = replace(result, message=f"{result.message}; 接続終了失敗: {error}")
        return result

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
        except Exception as error:
            logger.exception(
                "Input operation failed",
                extra={
                    "event": "input.failed",
                    "operation": "_execute",
                    "operation_id": result.operation_id,
                    "phase": result.phase,
                },
            )
            transport_failed = True
            result = replace(result, code="DEVICE_UNAVAILABLE", message=str(error))
        if result.neutral == "unconfirmed" and not neutral_attempted:
            try:
                await _settle_io(device.send(InputState()))
                result = replace(result, neutral="sent")
            except OSError as error:
                logger.exception(
                    "Input operation failed",
                    extra={
                        "event": "input.failed",
                        "operation": "_execute",
                        "operation_id": result.operation_id,
                        "phase": result.phase,
                    },
                )
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
                logger.exception(
                    "Input operation failed",
                    extra={
                        "event": "input.failed",
                        "operation": "_execute",
                        "operation_id": result.operation_id,
                        "phase": result.phase,
                    },
                )
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


def _require_released(result: OperationResult) -> None:
    if result.code not in {"OK", "INTERRUPTED"}:
        raise InputServiceError(result.code, result.message or "入力の解除を確認できません")


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
