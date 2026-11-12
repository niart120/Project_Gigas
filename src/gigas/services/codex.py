"""Bridge GUI commands to one thread-owned asyncio Codex connection."""

import asyncio
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import uuid4

from PySide6.QtCore import QThread, Signal

from gigas.mcp.observation import ObservationMcpServer
from gigas.runtime.codex.connection import AnswerDeliveryError, CodexConnectionError
from gigas.runtime.codex.launch import create_connection
from gigas.runtime.codex.models import SessionStatus, ThreadConfiguration
from gigas.services.observation import ObservationService
from gigas.storage.project import ProjectError, ProjectWriter

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Mapping
    from pathlib import Path

    from gigas.game_io.capture import CaptureReader
    from gigas.game_io.observation import ObservationResolution
    from gigas.runtime.codex.models import ConnectionEvent, ConnectionOptions

type ServiceOperation = Literal["connect", "login", "send", "answer", "stop", "disconnect"]
type AnswerOutcome = Literal["sent", "not_sent", "unknown"]
type _Operation = ServiceOperation | Literal["project_create", "project_open", "resolution"]


class ServiceConnection(Protocol):
    """Async connection boundary owned exclusively by the service loop."""

    async def open(self) -> None:
        """Open the connection and report authentication state."""
        ...

    async def login(self) -> None:
        """Begin explicit login."""
        ...

    async def send(self, prompt: str) -> None:
        """Submit one turn without retrying unknown acceptance."""
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
        Callable[[], Awaitable[ThreadConfiguration]],
        Callable[[str], Awaitable[None]],
    ],
    ServiceConnection,
]


def _create_connection(
    options: "ConnectionOptions",
    emit: "Callable[[ConnectionEvent], None]",
    prepare: "Callable[[], Awaitable[ThreadConfiguration]]",
    started: "Callable[[str], Awaitable[None]]",
) -> ServiceConnection:
    return create_connection(options, emit, prepare_thread=prepare, on_thread_started=started)


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
class ServiceResult:
    """Operation completion; empty error means the call returned successfully.

    A successful stop result is only interrupt acceptance. Turn termination is
    reported separately through SessionStatus. Errors contain no raw exception text.
    Answer operations include token-specific delivery evidence in answer, including
    rejected or cancelled calls. The generation still identifies the connection.
    failure_id correlates this result with an already emitted SessionStatus from
    that generation. None identifies an uncorrelated result, not a successful call.
    """

    generation: int
    operation: ServiceOperation
    error: str = ""
    answer: AnswerResult | None = None
    failure_id: int | None = None


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


class CodexServiceThread(QThread):
    """Own one asyncio loop and deliver queued results to the GUI.

    Call start once, then use the command methods from the GUI thread. Starting
    this thread does not connect to Codex. shutdown rejects new work and returns
    immediately; finished and wait(0) confirm thread exit. shutdown_error must be
    empty before treating exit as successful cleanup.

    Args:
        data_root: Persistent application data directory; unselected chats use chats/.
        reader: Shared read-only view of the existing capture service.
        factory: Called on the service loop after creating the conversation directory.
            Receives options, event callback, chat preparation, and thread binding.
            The default creates the bundled runtime connection.
    """

    received = Signal(object)
    completed = Signal(object)
    project_completed = Signal(object)
    recording_failed = Signal(str, str, str)

    def __init__(
        self,
        data_root: "Path",
        reader: "CaptureReader",
        factory: ConnectionFactory = _create_connection,
    ) -> None:
        """Prepare command routing without creating a loop or connection."""
        super().__init__()
        self._factory = factory
        self._data_root = data_root.resolve()
        self._reader = reader
        self._project_selected = False
        self._writer: ProjectWriter | None = None
        self._observations: ObservationService | None = None
        self._mcp: ObservationMcpServer | None = None
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

    def login(self) -> None:
        """Queue an explicit ChatGPT login request."""
        self._submit(_Command(self._generation, "login"))

    def send(self, prompt: str) -> None:
        """Queue a user turn; completion does not mean the turn has finished.

        Args:
            prompt: User text passed unchanged to the connection.
        """
        self._submit(_Command(self._generation, "send", text=prompt))

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
            self._reject_answer(command)

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
            self._reject_answer(command)

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
        if self._done is not None:
            self._done.set()

    def _dispatch(self, command: _Command) -> None:
        if self._ending:
            self._reject_answer(command)
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
            self._reject_answer(command)

    def _reject_answer(self, command: _Command) -> None:
        if command.operation == "answer":
            self._complete(command, "回答は送信されていない", "not_sent")

    def _complete(
        self,
        command: _Command,
        error: str,
        outcome: AnswerOutcome,
        failure_id: int | None = None,
    ) -> None:
        if command.operation in {"project_create", "project_open", "resolution"}:
            return
        answer = AnswerResult(command.token, outcome) if command.operation == "answer" else None
        self.completed.emit(
            ServiceResult(command.generation, command.operation, error, answer, failure_id)
        )

    async def _execute(self, command: _Command) -> None:
        if command.operation in {"project_create", "project_open"}:
            await _finish_io(self._change_project(command))
            return
        error = ""
        failure_id: int | None = None
        outcome: AnswerOutcome = "sent"
        try:
            await self._perform(command)
        except asyncio.CancelledError:
            if command.operation == "answer":
                self._complete(command, "回答の送信結果を確認できない", "unknown")
            return
        except AnswerDeliveryError as failure:
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
        self._complete(command, error, outcome, failure_id)

    async def _perform(self, command: _Command) -> None:
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
                    self._prepare_thread,
                    self._thread_started,
                )
            await self._connection.open()
            return
        connection = self._connection
        if connection is None or command.generation != self._active_generation:
            if command.operation == "answer":
                message = "この接続では回答を受け付けない"
                raise CodexConnectionError(message)
            return
        match command.operation:
            case "login":
                await connection.login()
            case "send":
                await connection.send(command.text)
            case "answer":
                await connection.answer(
                    command.token, decision=command.decision, answers=command.answers
                )
            case "stop":
                await connection.stop()
            case "disconnect":
                await self._close_connection()

    def _receive(self, generation: int, event: "ConnectionEvent") -> None:
        self.received.emit(ServiceEvent(generation, event))
        if (
            isinstance(event, SessionStatus)
            and event.phase == "closed"
            and generation == self._active_generation
            and self._closing is None
        ):
            # Runtime failures use the same cleanup as an explicit disconnect.
            self._dispatch(_Command(generation, "disconnect"))

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

    async def _prepare_thread(self) -> ThreadConfiguration:
        # Cancellation must recover synchronous I/O and its newly acquired writer.
        return await _finish_io(self._prepare_recording())

    async def _prepare_recording(self) -> ThreadConfiguration:
        async with self._resources_lock:
            try:
                if self._writer is None:
                    self._writer = await asyncio.to_thread(self._create_chat_archive)
                writer = self._writer
                if self._observations is None:
                    self._observations = ObservationService(
                        self._reader,
                        writer,
                        resolution=self._resolution,
                        on_save_error=lambda observation, error: self.recording_failed.emit(
                            observation.observation_id, str(writer.path), _recording_reason(error)
                        ),
                    )
                if self._mcp is None:
                    self._mcp = ObservationMcpServer(self._observations)
                    try:
                        await self._mcp.start()
                    except BaseException:
                        await self._mcp.close()
                        self._mcp = None
                        raise
                prefix = f"mcp_servers.{self._mcp.server_name}."
                endpoint = self._mcp.config
                return ThreadConfiguration(
                    writer.path,
                    {
                        prefix + "url": endpoint["url"],
                        prefix + "http_headers": dict(endpoint["http_headers"]),
                        prefix + "enabled": True,
                        prefix + "enabled_tools": ["observe"],
                        prefix + "required": True,
                        prefix + "startup_timeout_sec": 10,
                        prefix + "tool_timeout_sec": 30,
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

    async def _record_thread(self, thread_id: str) -> None:
        async with self._resources_lock:
            writer, observations = self._writer, self._observations
            if writer is None or observations is None:
                message = "チャットの記録先が準備されていません"
                raise ProjectError(message)
            try:
                await observations.recording.record_thread(thread_id)
            except Exception as error:
                self.recording_failed.emit(thread_id, str(writer.path), _recording_reason(error))
                raise
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
                except Exception as error:  # noqa: BLE001 -- encoding ended; saving was drained
                    path = str(self._writer.path) if self._writer is not None else ""
                    self.recording_failed.emit("", path, _recording_reason(error))
                self._observations = None


async def _finish_io[T](operation: "Coroutine[object, object, T]") -> T:
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


def _recording_reason(error: Exception) -> str:
    if isinstance(error, ProjectError | OSError):
        return str(error)
    return f"記録処理に失敗しました ({type(error).__name__})"
