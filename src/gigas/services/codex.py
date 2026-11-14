"""Bridge GUI commands to one thread-owned asyncio Codex connection."""

import asyncio
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from PySide6.QtCore import QThread, Signal

from gigas.game_io.input.serial import SerialInput, list_serial_ports
from gigas.runtime.codex.connection import (
    AnswerDeliveryError,
    CodexConnection,
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
from gigas.services._conversation_execution import ConversationExecution
from gigas.services._conversation_resources import ConversationResources, recording_reason
from gigas.services.input import InputService
from gigas.storage.project import ProjectError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Mapping
    from pathlib import Path

    from gigas.game_io.capture import CaptureReader
    from gigas.game_io.input.config import SerialConfig
    from gigas.game_io.observation import ObservationResolution
    from gigas.runtime.codex.connection import RuntimeEvent
    from gigas.runtime.codex.models import (
        ConnectionEvent,
        ConnectionOptions,
    )
    from gigas.runtime.codex.transport import JsonObject
    from gigas.services.input import InputDevice

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


@dataclass(frozen=True, slots=True)
class _OperationContext:
    generation: int
    operation: ServiceOperation
    token: str = ""
    request_id: str = field(default_factory=lambda: f"message-{uuid4()}")


type _ConversationCall = Callable[[ConversationExecution], Awaitable[str | bool | None]]
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
            Receives options and the synchronous runtime event callback.
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
        factory: ConnectionFactory = create_connection,
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
        self._conversation: ConversationExecution | None = None
        self._resources_lock = asyncio.Lock()
        self._lock = Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queued: list[tuple[Callable[[], None], _OperationContext | None]] = []
        self._ending = False
        self._generation = 0
        self._active_generation = 0
        self._connection: CodexConnection | None = None
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
        """Submit the objective through the conversation's shared send operation."""
        self._submit("goal_set", lambda execution: execution.set_goal(objective))

    def resume_goal(self) -> None:
        """Request active status without replacing the retained objective."""
        self._submit("goal_resume", lambda execution: execution.resume_goal())

    def clear_goal(self) -> None:
        """Withdraw the Goal without interrupting the current turn or input."""
        self._submit("goal_clear", lambda execution: execution.clear_goal())

    def select_model(self, model: str, effort: str) -> None:
        """Submit next-turn settings through the shared conversation send operation."""
        self._submit("select_model", lambda execution: self._select_model(execution, model, effort))

    def login(self) -> None:
        """Queue explicit ChatGPT login on the connection owner."""
        self._submit_connection("login", lambda connection: connection.login())

    def setup_windows_sandbox(self) -> None:
        """Queue explicit Windows preparation without blocking disconnect."""
        self._submit_connection(
            "setup_windows_sandbox", lambda connection: connection.setup_windows_sandbox()
        )

    def send(self, prompt: str) -> None:
        """Submit ordinary or additional text through the same turn/start path.

        Args:
            prompt: User text passed unchanged to conversation execution.
        """
        self._submit("send", lambda execution: execution.send(prompt))

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
        """Request common stop; the result reports actual stop completion."""
        self._submit("stop", lambda execution: execution.stop())

    def disconnect_runtime(self) -> None:
        """Close the current connection while keeping the service loop available."""
        self._submit_connection("disconnect", lambda _connection: self._close_connection())

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
        self, operation: ServiceOperation, call: _ConversationCall, *, token: str = ""
    ) -> None:
        context = _OperationContext(self._generation, operation, token)
        self._enqueue(
            lambda: self._start_operation(context, lambda: self._invoke(context, call)), context
        )

    def _submit_connection(
        self,
        operation: ServiceOperation,
        call: "Callable[[CodexConnection], Awaitable[None]]",
    ) -> None:
        context = _OperationContext(self._generation, operation)
        self._enqueue(
            lambda: self._start_operation(context, lambda: self._invoke_connection(context, call)),
            context,
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
            await self._close_connection()
        finally:
            try:
                await self._input.close()
            finally:
                pending = list(self._tasks)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                async with self._resources_lock:
                    await self._resources.close()

    def _signal_shutdown(self) -> None:
        self._input_pending = False
        self._input.stop()
        if self._done is not None:
            self._done.set()

    def _dispatch(
        self, start: "Callable[[], None]", context: _OperationContext | None = None
    ) -> None:
        if self._ending:
            if context is not None:
                self._reject_submission(context)
            return
        start()

    def _start_operation(self, context: _OperationContext, call: _OperationCall) -> None:
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
        if command.operation == "send":
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
        execution = self._conversation
        if changing and execution is not None:
            snapshot = execution.snapshot
            if (
                snapshot.sending
                or snapshot.turn_id is not None
                or (snapshot.goal is not None and snapshot.goal.status == "active")
            ):
                context = _OperationContext(self._active_generation, "stop")
                self._start_operation(context, execution.stop)
        self._input_target = config
        self._input_pending = True
        if self._input_apply_task is not None and not self._input_apply_task.done():
            self._input.stop()
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
                    self._input.stop()
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
            if command.operation in {"stop", "disconnect"}:
                self._complete(command, "停止・切断の完了を確認できない", "unknown")
                return
            if command.operation == "setup_windows_sandbox":
                self._complete(command, "実行環境の準備完了を確認できない", "unknown")
                return
            if command.operation in {
                "answer",
                "send",
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
            self._active_generation = generation
            self._closing = None

            def receive(event: "RuntimeEvent") -> None:
                execution.receive(event)

            connection = self._factory(options, receive)
            execution = ConversationExecution(
                connection,
                self._resources,
                self._input,
                lambda event: self._receive(generation, event),
                closing=lambda: self._closing is not None or self._connection is not connection,
                request_close=lambda: self._begin_close(connection, execution),
            )
            self._connection = connection
            self._conversation = execution
        await connection.open()

    async def _invoke_connection(
        self,
        command: _OperationContext,
        call: "Callable[[CodexConnection], Awaitable[None]]",
    ) -> None:
        connection = self._connection
        if connection is None or command.generation != self._active_generation:
            if command.operation == "disconnect":
                return
            raise CodexConnectionError("この接続では操作を受け付けません")
        if self._closing is not None and command.operation != "disconnect":
            raise CodexConnectionError("接続の終了待ち")
        await call(connection)

    async def _invoke(
        self, command: _OperationContext, call: _ConversationCall
    ) -> str | bool | None:
        execution = self._conversation
        if execution is None or command.generation != self._active_generation:
            if command.operation == "stop":
                self._input.stop()
                await self._input.drain_input()
                return None
            raise CodexConnectionError("この接続では送信を受け付けない")
        if command.operation in {"goal_resume", "goal_clear", "stop"}:
            self._record_submission({})
        return await call(execution)

    def _record_submission(self, payload: "JsonObject") -> None:
        context = self._recording_operation.get()
        if context is not None:
            payload.update({"request_id": context.request_id, "operation": context.operation})
        self._record_dialogue("submitted", payload)

    async def _select_model(
        self, execution: ConversationExecution, model: str, effort: str
    ) -> None:
        self._record_submission({"model": model, "effort": effort})
        await execution.select_model(model, effort)

    async def _answer(
        self,
        execution: ConversationExecution,
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
        await execution.answer(token, decision=decision, answers=answers)

    def _receive(self, generation: int, event: "ConnectionEvent") -> None:
        if generation == self._active_generation:
            self._record_event(event)
        self.received.emit(ServiceEvent(generation, event))

    def _record_dialogue(
        self, kind: str, payload: "JsonObject", *, turn_id: str | None = None
    ) -> None:
        self._resources.record_dialogue(kind, payload, turn_id=turn_id)

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

    async def _close_connection(self) -> None:
        connection, execution = self._connection, self._conversation
        if connection is not None and execution is not None:
            await asyncio.shield(self._begin_close(connection, execution))
        elif self._closing is not None:
            await asyncio.shield(self._closing)

    def _begin_close(
        self, connection: CodexConnection, execution: ConversationExecution
    ) -> asyncio.Task[None]:
        if self._connection is not connection:
            return asyncio.create_task(connection.close())
        if self._closing is None:
            self._closing = asyncio.create_task(
                self._release_connection(connection, execution),
                name="gigas-connection-resources-close",
            )
            execution.connection_closing()
            self._input.stop()
        return self._closing

    async def _release_connection(
        self, connection: CodexConnection, execution: ConversationExecution
    ) -> None:
        # Never join the stop task or Qt command that requested this shutdown.
        outcomes = await asyncio.gather(
            connection.close(), self._resources.close_endpoint(), return_exceptions=True
        )
        drained = await asyncio.gather(
            execution.drain(), self._input.drain_input(), return_exceptions=True
        )
        async with self._resources_lock:
            try:
                await self._resources.disconnect()
            finally:
                if self._connection is connection:
                    self._conversation = None
                    self._connection = None
        for outcome in (*outcomes, *drained):
            if isinstance(outcome, BaseException):
                raise outcome


async def _finish_io[T](operation: "Coroutine[object, object, T]") -> T:
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise
