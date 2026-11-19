"""Compose one admitted input with its conversation's observation and records."""

import asyncio
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Protocol

from gigas.game_io.observation import ObservationError
from gigas.services.input import OperationResult
from gigas.storage.input_events import OperationFinished, OperationStarted

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.game_io.input.models import InputStep
    from gigas.game_io.observation import Observation
    from gigas.services.observation import ObservationService
    from gigas.services.recording import RecordingService


class TrackedInputStart(Protocol):
    def __call__(
        self, sequence: "tuple[InputStep, ...]", progress: "Callable[[int], None] | None" = None
    ) -> tuple[str, asyncio.Task[OperationResult]] | OperationResult: ...


type InputStart = Callable[
    [tuple[InputStep, ...]], tuple[str, asyncio.Task[OperationResult]] | OperationResult
]


async def act_and_observe(
    sequence: "tuple[InputStep, ...]",
    *,
    start_input: InputStart,
    observations: "ObservationService",
    recording: "RecordingService",
) -> "tuple[OperationResult, Observation | ObservationError | None]":
    """Return input facts and a separate observation outcome without awaiting saves.

    Args:
        sequence: Validated finite sequence.
        start_input: Synchronous admission supplied by the conversation owner.
        observations: Observation service fixed for this conversation.
        recording: Persistence destination fixed before admission.

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
    return await _finish(Run(sequence, *admitted), observations, recording)


@dataclass(slots=True)
class Sample:
    offset_ms: int
    step: int | None = None
    image: "Observation | None" = None

    def metadata(self) -> dict[str, object]:
        return {
            "offset_ms": self.offset_ms,
            "step": self.step,
            "observation_id": self.image.observation_id if self.image else None,
            "captured_at": self.image.captured_at.isoformat() if self.image else None,
        }


@dataclass(slots=True)
class Run:
    sequence: "tuple[InputStep, ...]"
    identifier: str
    input_task: asyncio.Task[OperationResult]
    completed: int = 0
    samples: list[Sample] = field(default_factory=list)

    @property
    def step(self) -> int | None:
        return self.completed + 1 if self.completed < len(self.sequence) else None


async def _try_observe(observations: "ObservationService") -> "Observation | ObservationError":
    try:
        return await observations.observe()
    except ObservationError as error:
        return error
    except Exception as error:  # noqa: BLE001 -- keep input facts on encoder failure.
        return ObservationError("OBSERVATION_ENCODING_ERROR", str(error))


async def _sample(
    run: Run, interval: int, observations: "ObservationService", started: float
) -> None:
    loop = asyncio.get_running_loop()
    duration = sum(step.duration_ms for step in run.sequence)
    pending: asyncio.Task[None] | None = None

    async def capture(slot: Sample) -> None:
        if run.input_task.done():
            return
        slot.step = run.step
        observation = await _try_observe(observations)
        if not isinstance(observation, ObservationError):
            slot.image = observation

    try:
        for offset in range(interval, duration, interval):
            remaining = offset - int((loop.time() - started) * 1000)
            while remaining > 0 and not run.input_task.done():
                await asyncio.wait({run.input_task}, timeout=min(remaining, 3_600_000) / 1000)
                remaining = offset - int((loop.time() - started) * 1000)
            if run.input_task.done():
                if remaining <= 0:
                    run.samples.append(Sample(offset))
                    continue
                break
            slot = Sample(offset)
            run.samples.append(slot)
            if remaining > -interval and (pending is None or pending.done()):
                if pending is not None:
                    await pending
                pending = asyncio.create_task(capture(slot))
    finally:
        if pending is not None:
            await asyncio.shield(pending)


async def _finish(
    run: Run,
    observations: "ObservationService",
    recording: "RecordingService",
    *,
    interval: int | None = None,
    dispatched: bool = False,
) -> "tuple[OperationResult, Observation | ObservationError | None]":
    task = run.input_task
    operation_id = run.identifier
    recording.submit_input(OperationStarted(operation_id, run.sequence))
    sampler = (
        asyncio.create_task(_sample(run, interval, observations, asyncio.get_running_loop().time()))
        if interval
        else None
    )
    result = OperationResult("INTERRUPTED", "input", operation_id, interrupted=True)
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
        if sampler:
            await asyncio.shield(sampler)
        if result.code == "OK" or (dispatched and result.code == "INTERRUPTED"):
            try:
                captured = await _try_observe(observations)
                if isinstance(captured, ObservationError):
                    observation_error = captured
                else:
                    observation = captured
            except asyncio.CancelledError:
                cancelled_observation = True
                raise

        return result, observation_error or observation
    finally:
        if sampler and not sampler.done():
            await asyncio.shield(sampler)
        recording.submit_input(
            OperationFinished(
                operation_id,
                result.status,
                result.completed_steps,
                observation_id=observation.observation_id if observation is not None else None,
                phase="observe"
                if result.code == "OK" and (observation_error or cancelled_observation)
                else result.phase,
                reason=str(observation_error)
                if observation_error and result.code == "OK"
                else "観測応答が取り消されました"
                if cancelled_observation
                else result.message,
                input_sent=result.input_sent,
                neutral=result.neutral,
                samples=tuple(slot.metadata() for slot in run.samples),
            )
        )


@dataclass(slots=True)
class ToolOutcome:
    metadata: dict[str, object]
    image: "Observation | None" = None
    samples: tuple[Sample, ...] = ()
    error: bool = False


class GameOperations:
    """Own dispatched input and its last completed result for one connection."""

    def __init__(
        self,
        start: TrackedInputStart,
        observations: "ObservationService",
        recording: "RecordingService",
        *,
        dispatch_allowed: "Callable[[], bool] | None" = None,
    ) -> None:
        self.dispatch_allowed = dispatch_allowed
        self.start = start
        self.observations = observations
        self.recording = recording
        self.active: Run | None = None
        self.finishing: asyncio.Task[ToolOutcome] | None = None
        self.last: ToolOutcome | None = None

    def _admit(self, sequence: "tuple[InputStep, ...]") -> Run | ToolOutcome:
        def progress(completed: int) -> None:
            run.completed = completed

        if self.active is not None:
            return ToolOutcome({"code": "BUSY", "message": "操作列を実行中です"}, error=True)
        accepted = self.start(sequence, progress)
        if isinstance(accepted, OperationResult):
            return ToolOutcome(asdict(accepted), error=True)
        run = Run(sequence, *accepted)
        return run

    async def execute(self, sequence: "tuple[InputStep, ...]", interval: int) -> ToolOutcome:
        run = self._admit(sequence)
        if isinstance(run, ToolOutcome):
            return run
        return await self._result(run, interval=interval)

    def dispatch(self, sequence: "tuple[InputStep, ...]") -> ToolOutcome:
        if self.dispatch_allowed is not None and not self.dispatch_allowed():
            return ToolOutcome(
                {"code": "INPUT_DISABLED", "message": "実行中のターンがありません"}, error=True
            )
        run = self._admit(sequence)
        if isinstance(run, ToolOutcome):
            return run
        self.active = run
        self.finishing = asyncio.create_task(self._dispatched(run))
        return ToolOutcome({"dispatch_id": run.identifier})

    async def _result(
        self, run: Run, *, interval: int | None = None, dispatched: bool = False
    ) -> ToolOutcome:
        result, image = await _finish(
            run, self.observations, self.recording, interval=interval, dispatched=dispatched
        )
        metadata = asdict(result)
        if dispatched:
            metadata["dispatch_id"] = metadata.pop("operation_id")
        metadata["total_steps"] = len(run.sequence)
        metadata["step"] = run.step
        metadata["status"] = result.status
        failed = result.code not in {"OK", "INTERRUPTED"}
        if isinstance(image, ObservationError):
            metadata["observation_error"] = {"code": image.code, "message": str(image)}
            return ToolOutcome(metadata, samples=tuple(run.samples), error=failed or not dispatched)
        return ToolOutcome(
            metadata,
            image,
            tuple(run.samples),
            error=failed or (not dispatched and result.code != "OK"),
        )

    async def _dispatched(self, run: Run) -> ToolOutcome:
        try:
            self.last = await self._result(run, dispatched=True)
            return self.last
        finally:
            self.active = None

    def stop_dispatch(self) -> None:
        if self.active:
            task = self.active.input_task
            if not task.done() and not task.cancelling():
                task.cancel()

    async def query(self, identifier: str, *, cancel: bool) -> ToolOutcome:
        run = self.active
        if run is None or run.identifier != identifier:
            if self.last and self.last.metadata["dispatch_id"] == identifier:
                return self.last
            return ToolOutcome({"code": "UNKNOWN_DISPATCH", "dispatch_id": identifier}, error=True)
        if cancel:
            self.stop_dispatch()
        if cancel or run.input_task.done():
            finishing = self.finishing
            if finishing is None:
                message = "非同期操作の終了処理が見つかりません"
                raise RuntimeError(message)
            return await asyncio.shield(finishing)
        metadata: dict[str, object] = {
            "dispatch_id": identifier,
            "status": "running",
            "completed_steps": run.completed,
            "total_steps": len(run.sequence),
            "step": run.step,
        }
        image = await _try_observe(self.observations)
        if isinstance(image, ObservationError):
            metadata["observation_error"] = {"code": image.code, "message": str(image)}
            return ToolOutcome(metadata)
        return ToolOutcome(metadata, image)
