"""Bridge GUI commands to one thread-owned asyncio Codex connection."""

import asyncio
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import uuid4

from PySide6.QtCore import QThread, Signal

from gigas.game_io.input.serial import SerialInput, list_serial_ports
from gigas.mcp.game_io import TOOL_TIMEOUT_SECONDS, GameIoMcpServer
from gigas.runtime.codex.connection import (
    AnswerDeliveryError,
    CodexConnectionError,
    MessageDeliveryError,
)
from gigas.runtime.codex.launch import create_connection
from gigas.runtime.codex.models import (
    HumanRequest,
    RequestResolved,
    SessionStatus,
    ThreadConfiguration,
    TurnStarted,
)
from gigas.services.input import InputService
from gigas.services.observation import ObservationService
from gigas.services.recording import RecordingService
from gigas.storage.project import ProjectError, ProjectWriter

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Mapping
    from pathlib import Path

    from gigas.game_io.capture import CaptureReader
    from gigas.game_io.input.config import SerialConfig
    from gigas.game_io.observation import ObservationResolution
    from gigas.runtime.codex.models import ConnectionEvent, ConnectionOptions
    from gigas.services.input import InputDevice, InputGrant

type ServiceOperation = Literal[
    "connect", "login", "send", "steer", "answer", "stop", "disconnect", "select_model"
]
type AnswerOutcome = Literal["sent", "not_sent", "unknown"]
type _Operation = (
    ServiceOperation
    | Literal["project_create", "project_open", "resolution", "input_apply", "input_ports"]
)


class ServiceConnection(Protocol):
    """Async connection boundary owned exclusively by the service loop."""

    async def open(self) -> None:
        """Open the connection and report authentication state."""
        ...

    async def select_model(self, model: str, effort: str) -> None:
        """Select catalog values for the next ordinary turn."""
        ...

    async def login(self) -> None:
        """Begin explicit login."""
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
        """Request interruption without asserting turn completion."""
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

    A successful stop result is only interrupt acceptance. Turn termination is
    reported separately through SessionStatus. Errors contain no raw exception text.
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
class _Command:
    generation: int
    operation: _Operation
    options: "ConnectionOptions | None" = None
    text: str = ""
    token: str = ""
    decision: str | None = None
    answers: "Mapping[str, str] | None" = None
    path: "Path | None" = None
    resolution: "ObservationResolution | None" = None
    input_config: "SerialConfig | None" = None
    expected_turn_id: str = ""
    model: str = ""
    effort: str = ""


class CodexServiceThread(QThread):
    """Own one asyncio loop and deliver queued results to the GUI.

    Call start once, then use the command methods from the GUI thread. Starting
    this thread does not connect to Codex. shutdown rejects new work and returns
    immediately; finished and wait(0) confirm thread exit. shutdown_error must be
    empty before treating exit as successful cleanup.

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
        self._factory = factory
        self._input_factory = input_factory
        self._input = InputService()
        self._input_config: SerialConfig | None = None
        self._input_target: SerialConfig | None = None
        self._input_pending = False
        self._input_apply_task: asyncio.Task[None] | None = None
        self._data_root = data_root.resolve()
        self._reader = reader
        self._project_selected = False
        self._writer: ProjectWriter | None = None
        self._observations: ObservationService | None = None
        self._recording: RecordingService | None = None
        self._mcp: GameIoMcpServer | None = None
        self._grant: InputGrant | None = None
        self._run_input_allowed = False
        self._run_message = ""
        self._run_end_task: asyncio.Task[None] | None = None
        self._waiting_answers: set[str] = set()
        self._sending_answers: set[str] = set()
        self._turn_running = False
        self._resolution: ObservationResolution = "full_hd"
        self._resources_lock = asyncio.Lock()
        self._lock = Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queued: list[_Command] = []
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
        self._submit(_Command(self._generation, "input_apply", input_config=config))

    def refresh_input_ports(self) -> None:
        """Enumerate serial ports without opening a connection."""
        self._submit(_Command(self._generation, "input_ports"))

    def create_project(self, path: "Path") -> None:
        """Queue exclusive creation of a new project while disconnected.

        Args:
            path: New directory whose parent already exists.
        """
        self._submit(_Command(self._generation, "project_create", path=path.resolve()))

    def open_project(self, path: "Path") -> None:
        """Queue validation and selection of an existing project while disconnected.

        Args:
            path: Existing project directory. Reopening the held path reloads it.
        """
        self._submit(_Command(self._generation, "project_open", path=path.resolve()))

    def set_observation_resolution(self, resolution: "ObservationResolution") -> None:
        """Apply a size to later observations on the owning loop without reconnecting.

        Args:
            resolution: Validated application setting: original, full_hd, or hd.
        """
        self._submit(_Command(self._generation, "resolution", resolution=resolution))

    def connect_runtime(self, options: "ConnectionOptions") -> int:
        """Queue a connection using an immutable settings snapshot.

        Args:
            options: Settings and absolute conversation directory for this connection.

        Returns:
            Generation identifying all events and results for this connection attempt.
        """
        self._generation += 1
        self._submit(_Command(self._generation, "connect", options=options))
        return self._generation

    def select_model(self, model: str, effort: str) -> None:
        """Queue a next-turn selection without writing persistent settings."""
        self._submit(_Command(self._generation, "select_model", model=model, effort=effort))

    def login(self) -> None:
        """Queue an explicit ChatGPT login request."""
        self._submit(_Command(self._generation, "login"))

    def send(self, prompt: str) -> None:
        """Queue a user turn; completion does not mean the turn has finished.

        Args:
            prompt: User text passed unchanged to the connection.
        """
        self._submit(_Command(self._generation, "send", text=prompt))

    def steer(self, prompt: str, expected_turn_id: str) -> None:
        """Queue one instruction for the selected active turn, without retrying.

        Args:
            prompt: User text passed unchanged to the connection.
            expected_turn_id: Turn identity captured when the user submits.

        The completed signal carries MessageResult for acceptance, rejection, or
        uncertainty. It does not assert that the model followed the instruction.
        """
        self._submit(
            _Command(self._generation, "steer", text=prompt, expected_turn_id=expected_turn_id)
        )

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
        self._submit(
            _Command(
                self._generation,
                "answer",
                token=token,
                decision=decision,
                answers=None if answers is None else dict(answers),
            )
        )

    def interrupt(self) -> None:
        """Queue stop independently of a pending connect or send operation."""
        self._submit(_Command(self._generation, "stop"))

    def disconnect_runtime(self) -> None:
        """Close the current connection while keeping the service loop available."""
        self._submit(_Command(self._generation, "disconnect"))

    def shutdown(self) -> None:
        """Reject new commands and asynchronously close all owned resources."""
        with self._lock:
            self._ending = True
            queued, self._queued = self._queued, []
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._signal_shutdown)
        for command in queued:
            self._reject_submission(command)

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

    def _submit(self, command: _Command) -> None:
        with self._lock:
            ending = self._ending
            if not ending:
                if self._loop is None:
                    self._queued.append(command)
                else:
                    self._loop.call_soon_threadsafe(self._dispatch, command)
        if ending:
            self._reject_submission(command)

    async def _serve(self) -> None:
        self._done = asyncio.Event()
        with self._lock:
            self._loop = asyncio.get_running_loop()
            queued, self._queued = self._queued, []
            ending = self._ending
        if ending:
            return
        for command in queued:
            self._dispatch(command)
        await self._done.wait()
        try:
            try:
                await self._input.close()
            finally:
                if self._connection is not None:
                    try:
                        await self._connection.stop()
                    except Exception as error:  # noqa: BLE001 -- still close after interruption failure
                        failure_id = (
                            error.failure_id if isinstance(error, CodexConnectionError) else None
                        )
                        self.completed.emit(
                            ServiceResult(
                                self._active_generation, "stop", "中断に失敗", failure_id=failure_id
                            )
                        )
                    await self._close_connection()
                elif self._closing is not None:
                    await asyncio.shield(self._closing)
        finally:
            pending = list(self._tasks)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            async with self._resources_lock:
                try:
                    await self._close_recording()
                finally:
                    if self._writer is not None:
                        await asyncio.to_thread(self._writer.close)
                        self._writer = None

    def _signal_shutdown(self) -> None:
        self._input_pending = False
        self._stop_input()
        if self._done is not None:
            self._done.set()

    def _stop_input(self) -> None:
        self._run_input_allowed = False
        self._input.stop()

    def _dispatch(self, command: _Command) -> None:
        if self._ending:
            self._reject_submission(command)
            return
        if command.operation in {"stop", "disconnect"}:
            self._stop_input()
        if command.operation == "input_apply":
            self._input_target = command.input_config
            self._input_pending = True
            if self._input_apply_task is not None and not self._input_apply_task.done():
                self._stop_input()
                return
            self._input_apply_task = asyncio.create_task(self._apply_input())
            self._tasks.add(self._input_apply_task)
            self._input_apply_task.add_done_callback(
                lambda task: self._task_finished(task, command)
            )
            return
        if command.operation == "resolution":
            if command.resolution is not None:
                self._resolution = command.resolution
                if self._observations is not None:
                    self._observations.resolution = command.resolution
            return
        task = asyncio.create_task(
            self._execute(command), name=f"gigas-service-{command.operation}"
        )
        self._tasks.add(task)
        task.add_done_callback(lambda finished: self._task_finished(finished, command))

    def _task_finished(self, task: asyncio.Task[None], command: _Command) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            # _execute handles cancellation once entered; this call never started.
            self._reject_submission(command)

    def _reject_submission(self, command: _Command) -> None:
        if command.operation in {"answer", "send", "steer", "select_model"}:
            self._complete(command, "送信されていない", "not_sent")

    def _complete(
        self,
        command: _Command,
        error: str,
        outcome: AnswerOutcome,
        failure_id: int | None = None,
        turn_id: str | None = None,
    ) -> None:
        if command.operation in {
            "project_create",
            "project_open",
            "resolution",
            "input_apply",
            "input_ports",
        }:
            return
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
        self.completed.emit(
            ServiceResult(command.generation, command.operation, error, answer, failure_id, message)
        )

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

    async def _execute(self, command: _Command) -> None:
        if command.operation in {"project_create", "project_open"}:
            await _finish_io(self._change_project(command))
            return
        error = ""
        failure_id: int | None = None
        outcome: AnswerOutcome = "sent"
        turn_id: str | None = None
        try:
            turn_id = await self._perform(command)
        except asyncio.CancelledError:
            if command.operation in {"answer", "send", "steer", "select_model"}:
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
        self._complete(command, error, outcome, failure_id, turn_id)

    async def _perform(self, command: _Command) -> str | None:
        if command.operation == "input_ports":
            try:
                ports = await asyncio.to_thread(list_serial_ports)
            except Exception as error:  # noqa: BLE001 -- discovery failure must reach the GUI.
                self.input_ports.emit((), str(error))
            else:
                self.input_ports.emit(ports, "")
            return None
        if command.operation == "connect":
            async with self._resources_lock:
                if self._connection is not None or command.options is None:
                    message = "接続の終了待ち"
                    raise CodexConnectionError(message)
                command.options.cwd.mkdir(parents=True, exist_ok=True)
                self._active_generation = command.generation
                self._closing = None
                self._connection = self._factory(
                    command.options,
                    lambda event: self._receive(command.generation, event),
                    self._prepare_turn,
                    self._thread_started,
                )
            await self._connection.open()
            return None
        connection = self._connection
        if connection is None or command.generation != self._active_generation:
            if command.operation in {"answer", "send", "steer", "select_model"}:
                message = "この接続では送信を受け付けない"
                raise CodexConnectionError(message)
            return None
        match command.operation:
            case "select_model":
                await connection.select_model(command.model, command.effort)
            case "login":
                await connection.login()
            case "send":
                return await connection.send(command.text)
            case "steer":
                return await connection.steer(command.text, command.expected_turn_id)
            case "answer":
                self._sending_answers.add(command.token)
                try:
                    await connection.answer(
                        command.token, decision=command.decision, answers=command.answers
                    )
                except AnswerDeliveryError:
                    raise
                except CodexConnectionError:
                    self._sending_answers.discard(command.token)
                    self._resume_input()
                    raise
                self._sending_answers.discard(command.token)
                self._waiting_answers.discard(command.token)
                self._resume_input()
            case "stop":
                await connection.stop()
            case "disconnect":
                await self._close_connection()
        return None

    def _receive(self, generation: int, event: "ConnectionEvent") -> None:
        if generation == self._active_generation:
            if isinstance(event, TurnStarted) and self._grant is not None:
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
                    self._end_run("completed" if event.phase == "ready" else event.phase)
        self.received.emit(ServiceEvent(generation, event))
        if (
            isinstance(event, SessionStatus)
            and event.phase == "closed"
            and generation == self._active_generation
            and self._closing is None
        ):
            # Runtime failures use the same cleanup as an explicit disconnect.
            self._dispatch(_Command(generation, "disconnect"))

    def _resume_input(self) -> None:
        if self._turn_running and not self._waiting_answers and not self._sending_answers:
            self._input.resume()

    def _end_run(self, reason: str) -> None:
        if self._grant is None:
            return
        self._input.stop(reason)
        self._grant = None
        self._waiting_answers.clear()
        self._sending_answers.clear()
        self._run_end_task = asyncio.create_task(self._input.finish_run(reason))
        self._tasks.add(self._run_end_task)
        self._run_end_task.add_done_callback(self._run_finished)

    def _run_finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                path = str(self._writer.path) if self._writer is not None else ""
                self.recording_failed.emit("", path, _recording_reason(error))

    async def _change_project(self, command: _Command) -> None:
        path = command.path
        if path is None:
            return
        error = ""
        try:
            await self._select_project(path, create=command.operation == "project_create")
        except Exception as failure:  # noqa: BLE001 -- sanitize unexpected failures at GUI boundary
            error = _recording_reason(failure)
        self.project_completed.emit(ProjectResult(path, error))

    async def _select_project(self, path: "Path", *, create: bool) -> None:
        async with self._resources_lock:
            if self._connection is not None:
                message = "Codexを切断してからプロジェクトを変更してください"
                raise ProjectError(message)
            if self._writer is not None and self._writer.path == path:
                if create:
                    message = f"{path}: 既存のプロジェクトは新規作成できません"
                    raise ProjectError(message)
                await asyncio.to_thread(self._writer.reload)
            else:
                operation = ProjectWriter.create if create else ProjectWriter.open
                candidate = await asyncio.to_thread(operation, path)
                try:
                    if self._writer is not None:
                        await asyncio.to_thread(self._writer.close)
                except BaseException:
                    await asyncio.to_thread(candidate.close)
                    raise
                self._writer = candidate
            self._project_selected = True

    async def _prepare_turn(self, message: str, thread_id: str | None) -> ThreadConfiguration:
        # Cancellation must recover synchronous I/O and its newly acquired writer.
        self._run_input_allowed = True
        return await _finish_io(self._prepare_recording(message, thread_id))

    async def _prepare_recording(self, message: str, thread_id: str | None) -> ThreadConfiguration:
        self._end_run("replaced")
        if self._run_end_task is not None:
            await asyncio.shield(self._run_end_task)
        async with self._resources_lock:
            try:
                if self._mcp is not None:
                    await self._mcp.close()
                    self._mcp = None
                self._run_message = message
                if self._writer is None:
                    self._writer = await asyncio.to_thread(self._create_chat_archive)
                writer = self._writer
                if self._recording is None:
                    self._recording = RecordingService(writer)
                    self._recording.on_failure = lambda error: self.recording_failed.emit(
                        "", str(writer.path), _recording_reason(error)
                    )
                if self._observations is None:
                    self._observations = ObservationService(
                        self._reader,
                        self._recording,
                        resolution=self._resolution,
                    )
                protocol = (
                    self._input_config.protocol
                    if self._input_config is not None and self._input.connected
                    else None
                )
                self._mcp = GameIoMcpServer(self._observations, self._input, protocol)
                try:
                    await self._mcp.start()
                except BaseException:
                    await self._mcp.close()
                    self._mcp = None
                    raise
                if thread_id is not None:
                    await self._begin_run(thread_id)
                prefix = f"mcp_servers.{self._mcp.server_name}."
                endpoint = self._mcp.config
                return ThreadConfiguration(
                    writer.path,
                    {
                        prefix + "url": endpoint["url"],
                        prefix + "http_headers": dict(endpoint["http_headers"]),
                        prefix + "enabled": True,
                        prefix + "enabled_tools": ["observe", "act_and_observe"],
                        prefix + "required": True,
                        prefix + "startup_timeout_sec": 10,
                        prefix + "tool_timeout_sec": TOOL_TIMEOUT_SECONDS,
                        prefix + "tools.act_and_observe.approval_mode": "approve",
                    },
                )
            except Exception as error:
                path = self._writer.path if self._writer is not None else self._data_root / "chats"
                self.recording_failed.emit("", str(path), _recording_reason(error))
                raise

    def _create_chat_archive(self) -> ProjectWriter:
        parent = self._data_root / "chats"
        parent.mkdir(parents=True, exist_ok=True)
        return ProjectWriter.create(parent / str(uuid4()))

    async def _thread_started(self, thread_id: str) -> None:
        await _finish_io(self._record_thread(thread_id))
        await self._begin_run(thread_id)

    async def _begin_run(self, thread_id: str) -> None:
        if self._observations is None or self._recording is None or self._mcp is None:
            message = "入力実行の記録先が準備されていません"
            raise ProjectError(message)
        self._grant = await self._input.begin_run(
            self._run_message, thread_id, self._observations, self._recording
        )
        self._mcp.bind_grant(self._grant)
        if not self._run_input_allowed:
            self._input.stop("実行準備中に停止または入力接続の変更がありました")
        self._waiting_answers.clear()
        self._sending_answers.clear()

    async def _record_thread(self, thread_id: str) -> None:
        async with self._resources_lock:
            writer, observations = self._writer, self._observations
            if writer is None or observations is None or self._recording is None:
                message = "チャットの記録先が準備されていません"
                raise ProjectError(message)
            self._recording.submit_thread(thread_id)
            observations.thread_id = thread_id

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
        closing = [connection.close()]
        if self._mcp is not None:
            closing.append(self._mcp.close())
        outcomes = await asyncio.gather(*closing, return_exceptions=True)
        async with self._resources_lock:
            try:
                await self._close_recording()
            finally:
                if self._writer is not None and not self._project_selected:
                    await asyncio.to_thread(self._writer.close)
                    self._writer = None
                if not isinstance(outcomes[0], BaseException):
                    self._connection = None
        for result in outcomes:
            if isinstance(result, BaseException):
                raise result

    async def _close_recording(self) -> None:
        try:
            if self._mcp is not None:
                try:
                    await self._mcp.close()
                except Exception:
                    # The server task has ended and closed its socket. Preserve
                    # the failure, but do not retry the same failed task forever.
                    self._mcp = None
                    raise
                self._mcp = None
        finally:
            if self._observations is not None:
                try:
                    await self._observations.drain()
                except Exception as error:  # noqa: BLE001 -- report encoding failure, then drain saves
                    path = str(self._writer.path) if self._writer is not None else ""
                    self.recording_failed.emit("", path, _recording_reason(error))
                self._observations = None
            if self._recording is not None:
                await self._recording.drain()
                self._recording = None


async def _finish_io[T](operation: "Coroutine[object, object, T]") -> T:
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


def _recording_reason(error: BaseException) -> str:
    if isinstance(error, ProjectError | OSError):
        return str(error)
    return f"記録処理に失敗しました ({type(error).__name__})"
