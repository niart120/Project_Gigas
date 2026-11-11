"""Bridge GUI commands to one thread-owned asyncio Codex connection."""

import asyncio
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, Literal, Protocol

from PySide6.QtCore import QThread, Signal

from gigas.runtime.codex.connection import AnswerDeliveryError, CodexConnectionError
from gigas.runtime.codex.launch import create_connection

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from gigas.runtime.codex.models import ConnectionEvent, ConnectionOptions

type ServiceOperation = Literal["connect", "login", "send", "answer", "stop", "disconnect"]
type AnswerOutcome = Literal["sent", "not_sent", "unknown"]


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
    [ConnectionOptions, Callable[[ConnectionEvent], None]], ServiceConnection
]


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
    """

    generation: int
    operation: ServiceOperation
    error: str = ""
    answer: AnswerResult | None = None


@dataclass(frozen=True, slots=True)
class _Command:
    generation: int
    operation: ServiceOperation
    options: "ConnectionOptions | None" = None
    text: str = ""
    token: str = ""
    decision: str | None = None
    answers: "Mapping[str, str] | None" = None


class CodexServiceThread(QThread):
    """Own one asyncio loop and deliver queued results to the GUI.

    Call start once, then use the command methods from the GUI thread. Starting
    this thread does not connect to Codex. shutdown rejects new work and returns
    immediately; finished and wait(0) confirm thread exit. shutdown_error must be
    empty before treating exit as successful cleanup.

    Args:
        factory: Called on the service loop after creating the conversation directory.
            The default creates the bundled runtime connection.
    """

    received = Signal(object)
    completed = Signal(object)

    def __init__(self, factory: ConnectionFactory = create_connection) -> None:
        """Prepare command routing without creating a loop or connection."""
        super().__init__()
        self._factory = factory
        self._lock = Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queued: list[_Command] = []
        self._ending = False
        self._generation = 0
        self._active_generation = 0
        self._connection: ServiceConnection | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._done: asyncio.Event | None = None
        self.shutdown_error = ""

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
                except Exception:  # noqa: BLE001 -- interruption failure must not skip close
                    self.completed.emit(
                        ServiceResult(self._active_generation, "stop", "中断に失敗")
                    )
                await self._connection.close()
                self._connection = None
        finally:
            pending = list(self._tasks)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    def _signal_shutdown(self) -> None:
        if self._done is not None:
            self._done.set()

    def _dispatch(self, command: _Command) -> None:
        if self._ending:
            self._reject_answer(command)
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

    def _complete(self, command: _Command, error: str, outcome: AnswerOutcome) -> None:
        answer = AnswerResult(command.token, outcome) if command.operation == "answer" else None
        self.completed.emit(ServiceResult(command.generation, command.operation, error, answer))

    async def _execute(self, command: _Command) -> None:
        error = ""
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
        except CodexConnectionError as failure:
            error = str(failure)
            outcome = "not_sent"
        except Exception:  # noqa: BLE001 -- sanitize the service/GUI boundary
            error = "Codex操作に失敗"
            outcome = "unknown"
        self._complete(command, error, outcome)

    async def _perform(self, command: _Command) -> None:
        if command.operation == "connect":
            if self._connection is not None or command.options is None:
                message = "接続の終了待ち"
                raise CodexConnectionError(message)
            command.options.cwd.mkdir(parents=True, exist_ok=True)
            self._active_generation = command.generation
            self._connection = self._factory(
                command.options,
                lambda event: self.received.emit(ServiceEvent(command.generation, event)),
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
                await connection.close()
                self._connection = None
