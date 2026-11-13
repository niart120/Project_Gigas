"""Bridge GUI commands to one thread-owned asyncio Codex connection."""

import asyncio
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import uuid4

from PySide6.QtCore import QThread, Signal

from gigas.game_io.input.serial import SerialInput, list_serial_ports
from gigas.runtime.codex.connection import (
    AnswerDeliveryError,
    CodexConnectionError,
    MessageDeliveryError,
)
from gigas.runtime.codex.launch import create_connection
from gigas.runtime.codex.models import (
    DialogueEvent,
    GoalEvent,
    HumanRequest,
    ModelSelection,
    PlanEvent,
    RequestResolved,
    SessionStatus,
    TextEvent,
    TurnStarted,
)
from gigas.services._conversation_resources import ConversationResources, recording_reason
from gigas.services.input import InputService
from gigas.storage.project import ProjectError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Mapping
    from pathlib import Path

    from gigas.game_io.capture import CaptureReader
    from gigas.game_io.input.config import SerialConfig
    from gigas.game_io.observation import ObservationResolution
    from gigas.runtime.codex.models import (
        ConnectionEvent,
        ConnectionOptions,
        GoalState,
        ThreadConfiguration,
    )
    from gigas.runtime.codex.transport import JsonObject
    from gigas.services.input import InputDevice, InputGrant
    from gigas.services.observation import ObservationService
    from gigas.services.recording import RecordingService

type ServiceOperation = Literal[
    "connect",
    "login",
    "setup_windows_sandbox",
    "send",
    "steer",
    "answer",
    "stop",
    "disconnect",
    "select_model",
    "goal_set",
    "goal_resume",
    "goal_clear",
]
type AnswerOutcome = Literal["sent", "not_sent", "unknown"]


class ServiceConnection(Protocol):
    """Async connection boundary owned exclusively by the service loop."""

    async def open(self) -> None:
        """Open the connection and report authentication state."""
        ...

    async def set_goal(self, objective: str) -> bool:
        """Create or update the public Goal; return confirmed acceptance."""
        ...

    async def resume_goal(self) -> bool:
        """Explicitly resume the retained Goal after preparing the same thread."""
        ...

    async def clear_goal(self) -> None:
        """Locally accept Goal removal; SessionStatus reports completion or failure."""
        ...

    async def select_model(self, model: str, effort: str) -> None:
        """Select catalog values for the next ordinary turn."""
        ...

    async def login(self) -> None:
        """Begin explicit login."""
        ...

    async def setup_windows_sandbox(self) -> None:
        """Await explicit environment setup and its readiness confirmation."""
        ...

    async def send(self, prompt: str) -> str | None:
        """Return the acknowledged turn ID, or None if stopped before submission."""
        ...

    async def steer(self, prompt: str, expected_turn_id: str) -> str:
        """Return the acknowledged target turn without resolving pending questions."""
        ...

    async def answer(
        self, token: str, *, decision: str | None = None, answers: "Mapping[str, str] | None" = None
    ) -> None:
        """Answer a live local request token once.

        Returning confirms transport write completion only. Raise
        CodexConnectionError for rejection before transmission, and
        AnswerDeliveryError when delivery is unknown. Cancellation and unexpected
        exceptions also leave delivery unknown; callers must not retry them.
        """
        ...

    async def stop(self) -> None:
        """Locally accept interruption; SessionStatus reports completion or failure."""
        ...

    async def close(self) -> None:
        """Wait for all owned connection resources to close."""
        ...


type ConnectionFactory = Callable[
    [
        ConnectionOptions,
        Callable[[ConnectionEvent], None],
        Callable[[str, str | None], Awaitable[ThreadConfiguration]],
        Callable[[str], Awaitable[None]],
    ],
    ServiceConnection,
]


def _create_connection(
    options: "ConnectionOptions",
    emit: "Callable[[ConnectionEvent], None]",
    prepare: "Callable[[str, str | None], Awaitable[ThreadConfiguration]]",
    started: "Callable[[str], Awaitable[None]]",
) -> ServiceConnection:
    return create_connection(options, emit, prepare_turn=prepare, on_thread_started=started)


@dataclass(frozen=True, slots=True)
class InputConnectionResult:
    """Applied serial configuration and connection outcome for the GUI."""

    config: "SerialConfig | None"
    connected: bool
    error: str = ""


@dataclass(frozen=True, slots=True)
class ProjectResult:
    """A project selection attempt; an empty error confirms ownership of path.

    Attributes:
        path: Requested absolute directory, also the selected path on success.
        error: Display-safe failure details; failure preserves the previous selection.
    """

    path: "Path"
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

    Successful stop and goal_clear results confirm local request acceptance only,
    not RPC acceptance, Goal mutation, or turn termination. SessionStatus reports
    actual completion or failure. Errors contain no raw exception text.
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


@dataclass(frozen=True, slots=True)
class _OperationContext:
    generation: int
    operation: ServiceOperation
    token: str = ""
    request_id: str = field(default_factory=lambda: f"message-{uuid4()}")


type _ConnectionCall = Callable[[ServiceConnection], Awaitable[str | bool | None]]
type _OperationCall = Callable[[], Awaitable[str | bool | None]]


class CodexServiceThread(QThread):
    """Own one asyncio loop and deliver queued results to the GUI.

    Call start once, then use the command methods from the GUI thread. Starting
    this thread does not connect to Codex. shutdown rejects new work and returns
    immediately; finished and wait(0) confirm thread exit. shutdown_error must be
    empty before treating exit as successful cleanup.
    Rejections reported on the GUI thread do not write to the archive.

    Args:
        data_root: Persistent application data directory; unselected chats use chats/.
        reader: Shared read-only view of the existing capture service.
        input_factory: Creates an input device on the service loop from resolved settings.
        factory: Creates the runtime on the service loop when connecting.
            The conversation directory is prepared on the first send.
            Receives options, event callback, chat preparation, and thread binding.
            The default creates the bundled runtime connection.
    """

    received = Signal(object)
    completed = Signal(object)
    project_completed = Signal(object)
    recording_failed = Signal(str, str, str)
    input_changed = Signal(object)
    input_ports = Signal(object, str)

    def __init__(
        self,
        data_root: "Path",
        reader: "CaptureReader",
        factory: ConnectionFactory = _create_connection,
        *,
        input_factory: "Callable[[SerialConfig], InputDevice]" = SerialInput,
    ) -> None:
        """Prepare command routing without creating a loop or connection."""
        super().__init__()
        self._recording_operation: ContextVar[_OperationContext | None] = ContextVar(
            "gigas_operation", default=None
        )
        self._factory = factory
        self._input_factory = input_factory
        self._input = InputService()
        self._input_config: SerialConfig | None = None
        self._input_target: SerialConfig | None = None
        self._input_pending = False
        self._input_apply_task: asyncio.Task[None] | None = None
        self._resources = ConversationResources(data_root, reader, self.recording_failed.emit)
        self._grant: InputGrant | None = None
        self._run_input_allowed = False
        self._run_message = ""
        self._run_end_task: asyncio.Task[None] | None = None
        self._waiting_answers: set[str] = set()
        self._sending_answers: set[str] = set()
        self._goal: GoalState | None = None
        self._goal_preparing = False
        self._turn_id: str | None = None
        self._turn_running = False
        self._resources_lock = asyncio.Lock()
        self._lock = Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queued: list[tuple[Callable[[], None], _OperationContext | None]] = []
        self._ending = False
        self._generation = 0
        self._active_generation = 0
        self._connection: ServiceConnection | None = None
        self._closing: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._done: asyncio.Event | None = None
        self.shutdown_error = ""

    def apply_input(self, config: "SerialConfig | None") -> None:
        """Apply resolved saved input settings; None explicitly disconnects."""
        self._enqueue(lambda: self._accept_input(config))

    def refresh_input_ports(self) -> None:
        """Enumerate serial ports without opening a connection."""
        self._enqueue(lambda: self._track(asyncio.create_task(self._refresh_input_ports())))

    def create_project(self, path: "Path") -> None:
        """Queue exclusive creation of a new project while disconnected.

        Args:
            path: New directory whose parent already exists.
        """
        path = path.resolve()
        self._enqueue(
            lambda: self._track(
                asyncio.create_task(_finish_io(self._change_project(path, create=True)))
            )
        )

    def open_project(self, path: "Path") -> None:
        """Queue validation and selection of an existing project while disconnected.

        Args:
            path: Existing project directory. Reopening the held path reloads it.
        """
        path = path.resolve()
        self._enqueue(
            lambda: self._track(
                asyncio.create_task(_finish_io(self._change_project(path, create=False)))
            )
        )

    def set_observation_resolution(self, resolution: "ObservationResolution") -> None:
        """Apply a size to later observations on the owning loop without reconnecting.

        Args:
            resolution: Validated application setting: original, full_hd, or hd.
        """
        self._enqueue(lambda: self._resources.set_resolution(resolution))

    def connect_runtime(self, options: "ConnectionOptions") -> int:
        """Queue a connection using an immutable settings snapshot.

        Args:
            options: Settings and absolute conversation directory for this connection.

        Returns:
            Generation identifying all events and results for this connection attempt.
        """
        self._generation += 1
        context = _OperationContext(self._generation, "connect")
        self._enqueue(
            lambda: self._start_operation(
                context, lambda: self._connect(options, context.generation)
            ),
            context,
        )
        return self._generation

    def set_goal(self, objective: str) -> None:
        """Submit an explicit objective without queuing an ordinary turn."""
        self._submit(
            "goal_set",
            lambda connection: self._prepare_goal(lambda: connection.set_goal(objective)),
        )

    def resume_goal(self) -> None:
        """Resume a retained Goal with new MCP capabilities after cleanup."""
        self._submit("goal_resume", self._resume_goal)

    def clear_goal(self) -> None:
        """Revoke input immediately, then discard the Goal and interrupt its turn."""
        self._submit("goal_clear", lambda connection: connection.clear_goal())

    def select_model(self, model: str, effort: str) -> None:
        """Queue a next-turn selection without writing persistent settings."""
        self._submit(
            "select_model", lambda connection: self._select_model(connection, model, effort)
        )

    def login(self) -> None:
        """Queue an explicit ChatGPT login request."""
        self._submit("login", lambda connection: connection.login())

    def setup_windows_sandbox(self) -> None:
        """Queue explicit Windows environment setup without blocking disconnect.

        Completion reports setup completion, not just acceptance of its start.
        Disconnecting does not assert cancellation of Windows setup itself.
        """
        self._submit("setup_windows_sandbox", lambda connection: connection.setup_windows_sandbox())

    def send(self, prompt: str) -> None:
        """Queue a user turn; completion does not mean the turn has finished.

        Args:
            prompt: User text passed unchanged to the connection.
        """
        self._submit("send", lambda connection: connection.send(prompt))

    def steer(self, prompt: str, expected_turn_id: str) -> None:
        """Queue one instruction for the selected active turn, without retrying.

        Args:
            prompt: User text passed unchanged to the connection.
            expected_turn_id: Turn identity captured when the user submits.

        The completed signal carries MessageResult for acceptance, rejection, or
        uncertainty. It does not assert that the model followed the instruction.
        """
        self._submit("steer", lambda connection: connection.steer(prompt, expected_turn_id))

    def answer(
        self, token: str, *, decision: str | None = None, answers: "Mapping[str, str] | None" = None
    ) -> None:
        """Queue one answer, copying mutable answer mappings at the GUI boundary.

        Args:
            token: Live connection-unique request token.
            decision: One of the request's advertised approval decisions.
            answers: Text indexed by all question identifiers, or None for approval.

        The completed signal reports this token's AnswerResult. Calls rejected
        before dispatch report not_sent; interrupted writes report unknown.
        Results never assert that an approved operation executed successfully.
        """
        copied_answers = None if answers is None else dict(answers)
        self._submit(
            "answer",
            lambda connection: self._answer(connection, token, decision, copied_answers),
            token=token,
        )

    def interrupt(self) -> None:
        """Queue stop independently of a pending connect or send operation."""
        self._submit("stop", lambda connection: connection.stop())

    def disconnect_runtime(self) -> None:
        """Close the current connection while keeping the service loop available."""
        self._submit("disconnect", lambda _connection: self._close_connection())

    def shutdown(self) -> None:
        """Reject new commands and asynchronously close all owned resources."""
        with self._lock:
            self._ending = True
            queued, self._queued = self._queued, []
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._signal_shutdown)
        for _start, context in queued:
            if context is not None:
                self._reject_submission(context, record_delivery=False)

    def run(self) -> None:
        """Run and finally close the private asyncio loop on this QThread."""
        try:
            with asyncio.Runner() as runner:
                runner.run(self._serve())
        except Exception:  # noqa: BLE001 -- thread boundary must not expose exception secrets
            self.shutdown_error = "接続サービスの終了失敗"
        finally:
            with self._lock:
                self._loop = None
                self._ending = True

    def _submit(
        self, operation: ServiceOperation, call: _ConnectionCall, *, token: str = ""
    ) -> None:
        context = _OperationContext(self._generation, operation, token)
        self._enqueue(
            lambda: self._start_operation(context, lambda: self._invoke(context, call)), context
        )

    def _enqueue(
        self, start: "Callable[[], None]", context: _OperationContext | None = None
    ) -> None:
        with self._lock:
            ending = self._ending
            if not ending:
                if self._loop is None:
                    self._queued.append((start, context))
                else:
                    self._loop.call_soon_threadsafe(self._dispatch, start, context)
        if ending and context is not None:
            self._reject_submission(context, record_delivery=False)

    async def _serve(self) -> None:
        self._done = asyncio.Event()
        with self._lock:
            self._loop = asyncio.get_running_loop()
            queued, self._queued = self._queued, []
            ending = self._ending
        if ending:
            return
        for start, context in queued:
            self._dispatch(start, context)
        await self._done.wait()
        try:
            try:
                await self._input.close()
            finally:
                if self._connection is not None:
                    await self._close_connection()
                elif self._closing is not None:
                    await asyncio.shield(self._closing)
        finally:
            pending = list(self._tasks)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            async with self._resources_lock:
                await self._resources.close()

    def _signal_shutdown(self) -> None:
        self._input_pending = False
        self._stop_input()
        if self._done is not None:
            self._done.set()

    def _stop_input(self) -> None:
        self._run_input_allowed = False
        self._input.stop()

    def _dispatch(
        self, start: "Callable[[], None]", context: _OperationContext | None = None
    ) -> None:
        if self._ending:
            if context is not None:
                self._reject_submission(context)
            return
        start()

    def _start_operation(self, context: _OperationContext, call: _OperationCall) -> None:
        if context.operation in {"stop", "disconnect", "goal_clear"}:
            self._stop_input()
        task = asyncio.create_task(
            self._execute(context, call), name=f"gigas-service-{context.operation}"
        )
        self._track(task, context)

    def _track(self, task: asyncio.Task[None], context: _OperationContext | None = None) -> None:
        self._tasks.add(task)
        task.add_done_callback(lambda finished: self._task_finished(finished, context))

    def _task_finished(self, task: asyncio.Task[None], context: _OperationContext | None) -> None:
        self._tasks.discard(task)
        if task.cancelled() and context is not None:
            # _execute handles cancellation once entered; this call never started.
            self._reject_submission(context)

    def _reject_submission(
        self, command: _OperationContext, *, record_delivery: bool = True
    ) -> None:
        if command.operation == "setup_windows_sandbox":
            self._complete(
                command,
                "実行環境の準備を開始していない",
                "not_sent",
                record_delivery=record_delivery,
            )
            return
        if command.operation in {
            "answer",
            "send",
            "steer",
            "select_model",
            "goal_set",
            "goal_resume",
            "goal_clear",
        }:
            self._complete(command, "送信されていない", "not_sent", record_delivery=record_delivery)

    def _complete(
        self,
        command: _OperationContext,
        error: str,
        outcome: AnswerOutcome,
        failure_id: int | None = None,
        turn_id: str | None = None,
        goal_accepted: bool = False,
        *,
        record_delivery: bool = True,
    ) -> None:
        answer = AnswerResult(command.token, outcome) if command.operation == "answer" else None
        message = None
        if command.operation in {"send", "steer"}:
            if outcome == "sent":
                message = (
                    MessageResult("accepted", turn_id)
                    if turn_id is not None
                    else MessageResult("not_sent")
                )
            else:
                message = MessageResult(outcome)
        if command.operation == "goal_set":
            message = (
                MessageResult("accepted" if goal_accepted else "not_sent")
                if outcome == "sent"
                else MessageResult(outcome)
            )
        if record_delivery and command.generation == self._active_generation:
            self._record_dialogue(
                "delivery",
                {
                    "request_id": command.request_id,
                    "operation": command.operation,
                    "outcome": "accepted"
                    if command.operation in {"stop", "goal_clear"} and not error
                    else message.outcome
                    if message
                    else outcome,
                    "error": error,
                    "token": command.token if command.operation == "answer" else "",
                },
                turn_id=turn_id,
            )
        self.completed.emit(
            ServiceResult(command.generation, command.operation, error, answer, failure_id, message)
        )

    def _accept_input(self, config: "SerialConfig | None") -> None:
        changing = config != self._input_config or (
            config is not None and not self._input.connected
        )
        if changing and (
            self._goal_preparing or (self._goal is not None and self._goal.status == "active")
        ):
            context = _OperationContext(self._active_generation, "stop")
            self._start_operation(
                context, lambda: self._invoke(context, lambda connection: connection.stop())
            )
        self._input_target = config
        self._input_pending = True
        if self._input_apply_task is not None and not self._input_apply_task.done():
            self._stop_input()
            return
        self._input_apply_task = asyncio.create_task(self._apply_input())
        self._track(self._input_apply_task)

    async def _refresh_input_ports(self) -> None:
        try:
            ports = await asyncio.to_thread(list_serial_ports)
        except Exception as error:  # noqa: BLE001 -- discovery failure must reach the GUI.
            self.input_ports.emit((), str(error))
        else:
            self.input_ports.emit(ports, "")

    async def _apply_input(self) -> None:
        while self._input_pending:
            config = self._input_target
            self._input_pending = False
            error = ""
            try:
                if config is None or config != self._input_config or not self._input.connected:
                    device = None if config is None else self._input_factory(config)
                    self._run_input_allowed = False
                    await self._input.connect(device)
                self._input_config = config
            except Exception as failure:  # noqa: BLE001 -- preserve a displayable input diagnostic.
                error = str(failure)
            self.input_changed.emit(InputConnectionResult(config, self._input.connected, error))

    async def _execute(self, command: _OperationContext, call: _OperationCall) -> None:
        token = self._recording_operation.set(command)
        try:
            await self._execute_operation(command, call)
        finally:
            self._recording_operation.reset(token)

    async def _execute_operation(self, command: _OperationContext, call: _OperationCall) -> None:
        error = ""
        failure_id: int | None = None
        outcome: AnswerOutcome = "sent"
        turn_id: str | None = None
        goal_accepted = False
        try:
            result = await call()
            goal_accepted = result is True
            turn_id = result if isinstance(result, str) else None
        except asyncio.CancelledError:
            if command.operation == "setup_windows_sandbox":
                self._complete(command, "実行環境の準備完了を確認できない", "unknown")
                return
            if command.operation in {
                "answer",
                "send",
                "steer",
                "select_model",
                "goal_set",
                "goal_resume",
                "goal_clear",
            }:
                self._complete(command, "送信結果を確認できない", "unknown")
            return
        except (AnswerDeliveryError, MessageDeliveryError) as failure:
            error = str(failure)
            outcome = "unknown"
            failure_id = failure.failure_id
        except CodexConnectionError as failure:
            error = str(failure)
            outcome = "not_sent"
            failure_id = failure.failure_id
        except Exception:  # noqa: BLE001 -- sanitize the service/GUI boundary
            error = "Codex操作に失敗"
            outcome = "unknown"
        self._complete(command, error, outcome, failure_id, turn_id, goal_accepted)

    async def _connect(self, options: "ConnectionOptions", generation: int) -> None:
        async with self._resources_lock:
            if self._connection is not None:
                raise CodexConnectionError("接続の終了待ち")
            options.cwd.mkdir(parents=True, exist_ok=True)
            self._goal = None
            self._turn_id = None
            self._active_generation = generation
            self._closing = None
            self._connection = self._factory(
                options,
                lambda event: self._receive(generation, event),
                self._prepare_turn,
                self._thread_started,
            )
        await self._connection.open()

    async def _invoke(self, command: _OperationContext, call: _ConnectionCall) -> str | bool | None:
        connection = self._connection
        if connection is None or command.generation != self._active_generation:
            if command.operation == "setup_windows_sandbox":
                raise CodexConnectionError("この接続では実行環境の準備を受け付けない")
            if command.operation in {
                "answer",
                "send",
                "steer",
                "select_model",
                "goal_set",
                "goal_resume",
                "goal_clear",
            }:
                message = "この接続では送信を受け付けない"
                raise CodexConnectionError(message)
            return None
        if command.operation in {"goal_resume", "goal_clear", "stop"}:
            self._record_submission({})
        return await call(connection)

    def _record_submission(self, payload: "JsonObject") -> None:
        context = self._recording_operation.get()
        if context is not None:
            payload.update({"request_id": context.request_id, "operation": context.operation})
        self._record_dialogue("submitted", payload)

    async def _prepare_goal(self, call: "Callable[[], Awaitable[bool]]") -> bool:
        previous_grant = self._grant
        self._goal_preparing = True
        try:
            return await call()
        finally:
            self._goal_preparing = False
            if self._grant is not previous_grant and (
                self._goal is None or self._goal.status != "active"
            ):
                self._stop_input()
                if self._turn_id is None:
                    self._end_run("goal_inactive")

    async def _resume_goal(self, connection: ServiceConnection) -> bool:
        if not self._resources.has_live_frame:
            raise CodexConnectionError("再開には取得中の映像が必要です")
        await self._input.drain_input()
        return await self._prepare_goal(connection.resume_goal)

    async def _select_model(self, connection: ServiceConnection, model: str, effort: str) -> None:
        self._record_submission({"model": model, "effort": effort})
        await connection.select_model(model, effort)

    async def _answer(
        self,
        connection: ServiceConnection,
        token: str,
        decision: str | None,
        answers: "Mapping[str, str] | None",
    ) -> None:
        self._record_submission(
            {
                "token": token,
                "decision": decision,
                "answers": dict(answers) if answers is not None else None,
            }
        )
        self._sending_answers.add(token)
        try:
            await connection.answer(token, decision=decision, answers=answers)
        except AnswerDeliveryError:
            raise
        except CodexConnectionError:
            self._sending_answers.discard(token)
            self._resume_input()
            raise
        self._sending_answers.discard(token)
        self._waiting_answers.discard(token)
        self._resume_input()

    def _receive(self, generation: int, event: "ConnectionEvent") -> None:
        if generation == self._active_generation:
            self._record_event(event)
            if isinstance(event, GoalEvent):
                self._goal = event.goal
                if (self._goal is None or self._goal.status != "active") and not (
                    self._goal_preparing and self._turn_id is None
                ):
                    self._stop_input()
                    if self._turn_id is None:
                        self._end_run("goal_inactive")
            elif isinstance(event, TurnStarted):
                self._turn_id = event.turn_id
                if self._grant is not None:
                    self._input.bind_turn(self._grant, event.turn_id)
            elif isinstance(event, HumanRequest):
                self._waiting_answers.add(event.token)
                self._input.suspend()
            elif isinstance(event, RequestResolved):
                self._waiting_answers.discard(event.token)
                self._resume_input()
            elif isinstance(event, SessionStatus):
                self._turn_running = event.phase == "running"
                if event.phase == "waiting":
                    self._input.suspend()
                elif event.phase == "stopping":
                    self._stop_input()
                elif event.phase == "running":
                    self._resume_input()
                elif event.phase in {"ready", "stopped", "error", "closed"}:
                    self._turn_id = None
                    if event.phase == "ready" and (
                        self._goal_preparing
                        or (self._goal is not None and self._goal.status == "active")
                    ):
                        self._input.suspend()
                    else:
                        self._end_run("completed" if event.phase == "ready" else event.phase)
        self.received.emit(ServiceEvent(generation, event))
        if (
            isinstance(event, SessionStatus)
            and event.phase == "closed"
            and generation == self._active_generation
            and self._closing is None
        ):
            # Runtime failures use the same cleanup as an explicit disconnect.
            context = _OperationContext(generation, "disconnect")
            self._dispatch(
                lambda: self._start_operation(
                    context,
                    lambda: self._invoke(context, lambda _connection: self._close_connection()),
                ),
                context,
            )

    def _record_dialogue(
        self, kind: str, payload: "JsonObject", *, turn_id: str | None = None
    ) -> None:
        self._resources.record_dialogue(
            kind,
            payload,
            turn_id=turn_id or self._turn_id,
            run_id=self._grant.run_id if self._grant else None,
        )

    def _record_event(self, event: "ConnectionEvent") -> None:
        kinds = {
            GoalEvent: "goal",
            PlanEvent: "plan",
            HumanRequest: "request",
            RequestResolved: "resolved",
            TurnStarted: "turn_started",
            SessionStatus: "status",
        }
        kind = kinds.get(type(event))
        if kind is not None:
            self._record_dialogue(
                kind,
                asdict(event),
                turn_id=event.turn_id
                if isinstance(event, PlanEvent | HumanRequest | TurnStarted)
                else None,
            )
        elif isinstance(event, DialogueEvent):
            self._record_dialogue(event.kind, event.payload, turn_id=event.turn_id)
        elif isinstance(event, ModelSelection):
            self._record_dialogue("selection", {"model": event.model, "effort": event.effort})
        elif isinstance(event, TextEvent) and event.role == "user":
            self._record_submission(asdict(event))

    def _resume_input(self) -> None:
        if (
            self._run_input_allowed
            and self._turn_running
            and not self._waiting_answers
            and not self._sending_answers
        ):
            self._input.resume()

    def _end_run(self, reason: str) -> None:
        if self._grant is None:
            return
        self._input.stop(reason)
        self._grant = None
        self._waiting_answers.clear()
        self._sending_answers.clear()
        self._run_end_task = asyncio.create_task(self._input.finish_run(reason))
        self._track(self._run_end_task)
        self._run_end_task.add_done_callback(self._run_finished)

    def _run_finished(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                self._resources.report_failure(error)

    async def _change_project(self, path: "Path", *, create: bool) -> None:
        error = ""
        try:
            await self._select_project(path, create=create)
        except Exception as failure:  # noqa: BLE001 -- sanitize unexpected failures at GUI boundary
            error = recording_reason(failure)
        self.project_completed.emit(ProjectResult(path, error))

    async def _select_project(self, path: "Path", *, create: bool) -> None:
        async with self._resources_lock:
            if self._connection is not None:
                raise ProjectError("Codexを切断してからプロジェクトを変更してください")
            await self._resources.select_project(path, create=create)

    async def _prepare_turn(self, message: str, thread_id: str | None) -> "ThreadConfiguration":
        # Cancellation must recover synchronous I/O and its newly acquired writer.
        self._run_input_allowed = (
            self._goal_preparing or self._goal is None or self._goal.status == "complete"
        )
        return await _finish_io(self._prepare_recording(message, thread_id))

    async def _prepare_recording(
        self, message: str, thread_id: str | None
    ) -> "ThreadConfiguration":
        self._end_run("replaced")
        if self._run_end_task is not None:
            await asyncio.shield(self._run_end_task)
        async with self._resources_lock:
            self._run_message = message
            protocol = (
                self._input_config.protocol
                if self._input_config is not None and self._input.connected
                else None
            )
            configuration, observations, recording = await self._resources.prepare(
                self._input, protocol
            )
            if thread_id is not None:
                try:
                    await self._begin_run(thread_id, observations, recording)
                except Exception as error:
                    self._resources.report_failure(error)
                    raise
            return configuration

    async def _thread_started(self, thread_id: str) -> None:
        await _finish_io(self._record_thread(thread_id))

    async def _record_thread(self, thread_id: str) -> None:
        async with self._resources_lock:
            observations, recording = self._resources.record_thread(thread_id)
            await self._begin_run(thread_id, observations, recording)

    async def _begin_run(
        self, thread_id: str, observations: "ObservationService", recording: "RecordingService"
    ) -> None:
        self._grant = await self._input.begin_run(
            self._run_message, thread_id, observations, recording
        )
        self._resources.bind(self._grant)
        self._input.suspend()
        if not self._run_input_allowed:
            self._input.stop("実行準備中に停止または入力接続の変更がありました")
        self._waiting_answers.clear()
        self._sending_answers.clear()

    async def _close_connection(self) -> None:
        connection = self._connection
        if connection is None:
            return
        if self._closing is None:
            self._closing = asyncio.create_task(
                self._release_connection(connection), name="gigas-connection-resources-close"
            )
        await asyncio.shield(self._closing)

    async def _release_connection(self, connection: ServiceConnection) -> None:
        # Revoke HTTP intake while joining the runtime, and still drain saving
        # after either cleanup reports a failure.
        self._end_run("disconnected")
        if self._run_end_task is not None:
            await asyncio.shield(self._run_end_task)
        outcomes = await asyncio.gather(
            connection.close(), self._resources.close_endpoint(), return_exceptions=True
        )
        async with self._resources_lock:
            try:
                await self._resources.disconnect()
            finally:
                if not isinstance(outcomes[0], BaseException):
                    self._connection = None
        for result in outcomes:
            if isinstance(result, BaseException):
                raise result


async def _finish_io[T](operation: "Coroutine[object, object, T]") -> T:
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise
