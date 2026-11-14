"""Bridge GUI commands to one thread-owned asyncio Codex connection."""

import asyncio
from dataclasses import dataclass
from functools import partial
from threading import Lock
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

from gigas.game_io.input.serial import SerialInput, list_serial_ports
from gigas.runtime.codex.connection import (
    AnswerDeliveryError,
    CodexConnection,
    CodexConnectionError,
    MessageDeliveryError,
)
from gigas.runtime.codex.launch import create_connection
from gigas.runtime.codex.management import CodexManagement
from gigas.runtime.codex.models import DialogueEvent, ExecutionSettings, TextEvent
from gigas.runtime.codex.transport import RpcNotification
from gigas.services._conversation_execution import ConversationExecution
from gigas.services._conversation_resources import ConversationResources, recording_reason
from gigas.services._conversation_selection import ConversationSelection
from gigas.services._workspaces import select_project
from gigas.services.codex_models import (
    AnswerOutcome,
    AnswerResult,
    ConnectionFactory,
    InputConnectionResult,
    MessageResult,
    ProjectResult,
    ServiceEvent,
    ServiceOperation,
    ServiceResult,
)
from gigas.services.conversations import ManagementResult
from gigas.services.input import InputService

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
    from gigas.services.conversations import (
        ConversationTarget,
        ManagementOperation,
        ManagementValue,
    )
    from gigas.services.input import InputDevice

__all__ = [
    "AnswerOutcome",
    "AnswerResult",
    "CodexServiceThread",
    "ConnectionFactory",
    "InputConnectionResult",
    "MessageResult",
    "ProjectResult",
    "ServiceEvent",
    "ServiceOperation",
    "ServiceResult",
]


@dataclass(frozen=True, slots=True)
class _OperationContext:
    generation: int
    operation: ServiceOperation
    token: str = ""


type _ConversationCall = Callable[[ConversationExecution], Awaitable[str | bool | None]]
type _OperationCall = Callable[[], Awaitable[str | bool | None]]


class CodexServiceThread(QThread):
    """Own one asyncio loop and deliver queued results to the GUI.

    Call start once, then use the command methods from the GUI thread. Starting
    this thread does not connect to Codex. shutdown rejects new work and returns
    immediately; finished and wait(0) confirm thread exit. shutdown_error must be
    empty before treating exit as successful cleanup.
    Rejections on the GUI thread emit completion results without recording a body.

    Args:
        data_root: Persistent application data directory; unassigned chats use unassigned/.
        reader: Shared read-only view of the existing capture service.
        input_factory: Creates an input device on the service loop from resolved settings.
        factory: Creates the runtime on the service loop when connecting.
            The conversation directory is prepared on the first send or Goal creation.
            Receives options and the synchronous runtime event callback.
            None creates the bundled runtime using data_root/codex_home, without
            copying credentials or inheriting the developer's runtime home.
    """

    received = Signal(object)
    completed = Signal(object)
    project_completed = Signal(object)
    management_completed = Signal(object)
    browse_goal_changed = Signal(int, object)
    recording_failed = Signal(str, str, str)
    input_changed = Signal(object)
    input_ports = Signal(object, str)

    def __init__(
        self,
        data_root: "Path",
        reader: "CaptureReader",
        factory: ConnectionFactory | None = None,
        *,
        input_factory: "Callable[[SerialConfig], InputDevice]" = SerialInput,
    ) -> None:
        """Prepare command routing without creating a loop or connection."""
        super().__init__()
        self._factory = (
            partial(create_connection, codex_home=data_root / "codex_home")
            if factory is None
            else factory
        )
        self._input_factory = input_factory
        self._input = InputService()
        self._input_config: SerialConfig | None = None
        self._input_target: SerialConfig | None = None
        self._input_pending = False
        self._input_apply_task: asyncio.Task[None] | None = None
        self._resources = ConversationResources(data_root, reader, self.recording_failed.emit)
        self._selection = ConversationSelection()
        self._execution_settings = ExecutionSettings()
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
        self._tasks: dict[asyncio.Task[None], _OperationContext | None] = {}
        self._done: asyncio.Event | None = None
        self.shutdown_error = ""

    @property
    def selected_target(self) -> "ConversationTarget":
        """Return the current immutable selection reference for identity checks.

        Reading does not reserve the selection. The service queue may adopt a
        different target later; the returned object itself is never changed.
        """
        return self._selection.target

    def apply_input(self, config: "SerialConfig | None") -> None:
        """Apply resolved saved input settings; None explicitly disconnects."""
        self._enqueue(lambda: self._accept_input(config))

    def refresh_input_ports(self) -> None:
        """Enumerate serial ports without opening a connection."""
        self._enqueue(lambda: self._track(asyncio.create_task(self._refresh_input_ports())))

    def list_projects(self, *, cursor: str | None = None) -> None:
        """Read one public Project page; management_completed carries its result."""
        self._submit_management(
            "projects", lambda c: CodexManagement(c).list_projects(cursor=cursor)
        )

    def list_threads(self, project_id: str | None, *, cursor: str | None = None) -> None:
        """Read one assignment page; None explicitly requests unassigned threads."""
        self._submit_management(
            "threads",
            lambda c: CodexManagement(c).list_threads(project_id, cursor=cursor),
            project_id=project_id,
        )

    def new_conversation(self, project_id: str | None = None) -> None:
        """Select an unstarted assignment after releasing an idle prior conversation."""
        self._submit_management(
            "new", lambda c: self._new_conversation(c, project_id), project_id=project_id
        )

    def select_conversation(self, thread_id: str) -> None:
        """Read saved history without resuming; a running conversation must stop first."""
        self._submit_management("select", lambda c: self._select_conversation(c, thread_id))

    def load_history(self, *, cursor: str | None = None) -> None:
        """Read one extra page without reserving submission or selection admission.

        Results, including failures, are discarded when their selection instance
        is no longer current. Cursors are passed through without interpretation.
        """
        self._submit_management(
            "history", lambda c: self._selection.history(CodexManagement(c), cursor=cursor)
        )

    def create_project(self, path: "Path") -> None:
        """Queue exclusive directory creation and Project selection before a thread starts.

        Requires an open connection. project_completed reports the server root on
        success; failure preserves the previous selection without deleting files.

        Args:
            path: New directory whose parent already exists.
        """
        generation = self._generation
        self._enqueue(
            lambda: self._track(
                asyncio.create_task(
                    _finish_io(self._change_project(path, generation=generation, create=True))
                )
            )
        )

    def open_project(self, path: "Path") -> None:
        """Queue server Project lookup or registration for an existing directory.

        Requires an open connection and no started conversation. The first matching
        server root is reused; an unregistered directory becomes a new Project.

        Args:
            path: Existing workspace directory, selected before a thread starts.
        """
        generation = self._generation
        self._enqueue(
            lambda: self._track(
                asyncio.create_task(
                    _finish_io(self._change_project(path, generation=generation, create=False))
                )
            )
        )

    def set_observation_resolution(self, resolution: "ObservationResolution") -> None:
        """Apply a size to later observations on the owning loop without reconnecting.

        Args:
            resolution: Validated application setting: original, full_hd, or hd.
        """
        self._enqueue(lambda: self._resources.set_resolution(resolution))

    def set_execution_settings(self, settings: ExecutionSettings) -> None:
        """Queue saved options for future conversation preparation without sending an RPC.

        Args:
            settings: Last successfully loaded or saved application options. An ongoing
                preparation retains its snapshot; an existing runtime thread is unchanged.
        """
        self._enqueue(lambda: self._apply_execution_settings(settings))

    def _apply_execution_settings(self, settings: ExecutionSettings) -> None:
        self._execution_settings = settings
        if self._conversation is not None:
            self._conversation.set_execution_settings(settings)

    def connect_runtime(self, options: "ConnectionOptions") -> int:
        """Queue a connection using an immutable settings snapshot.

        Args:
            options: Saved startup settings and the absolute process working directory.
                Conversation workspaces are prepared separately on explicit execution.

        Returns:
            Generation identifying all events and results for this connection attempt.
        """
        self._generation += 1
        context = _OperationContext(self._generation, "connect")

        def start() -> None:
            self._apply_execution_settings(options.execution_settings)
            self._start_operation(context, lambda: self._connect(options, context.generation))

        self._enqueue(start, context)
        return self._generation

    def set_goal(self, objective: str) -> None:
        """Submit the objective through the conversation's shared send operation."""
        self._submit("goal_set", lambda execution: execution.set_goal(objective))

    def resume_goal(self) -> None:
        """Request active status without replacing the retained objective."""
        self._submit("goal_resume", lambda execution: execution.resume_goal())

    def clear_goal(self) -> None:
        """Withdraw the Goal without interrupting the current turn or input."""
        self._submit("goal_clear", self._clear_goal)

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
            lambda execution: execution.answer(token, decision=decision, answers=copied_answers),
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
                self._reject_submission(context)

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
            self._reject_submission(context)

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
        self._tasks[task] = context
        task.add_done_callback(lambda finished: self._task_finished(finished, context))

    def _task_finished(self, task: asyncio.Task[None], context: _OperationContext | None) -> None:
        self._tasks.pop(task, None)
        if task.cancelled() and context is not None:
            # _execute handles cancellation once entered; this call never started.
            self._reject_submission(context)

    def _reject_submission(self, command: _OperationContext) -> None:
        if command.operation == "setup_windows_sandbox":
            self._complete(
                command,
                "実行環境の準備を開始していない",
                "not_sent",
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
            self._complete(command, "送信されていない", "not_sent")

    def _complete(
        self,
        command: _OperationContext,
        error: str,
        outcome: AnswerOutcome,
        failure_id: int | None = None,
        turn_id: str | None = None,
        goal_accepted: bool = False,
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
                if self._connection is connection and self._conversation is not None:
                    execution = self._conversation
                    if isinstance(event, RpcNotification) and execution.snapshot.thread_id is None:
                        goal = self._selection.receive_goal(event)
                        if goal is not None:
                            self.browse_goal_changed.emit(generation, goal)
                    execution.receive(event)

            connection = self._factory(options, receive)
            self._connection = connection
            self._conversation = self._make_execution(connection)
        await connection.open()

    def _make_execution(self, connection: CodexConnection) -> ConversationExecution:
        def emit(event: "ConnectionEvent") -> None:
            if self._conversation is execution:
                self._receive(self._active_generation, event)

        execution = ConversationExecution(
            connection,
            self._resources,
            self._input,
            emit,
            selection=self._selection,
            settings=self._execution_settings,
            closing=lambda: (
                self._closing is not None
                or self._connection is not connection
                or self._conversation is not execution
            ),
            request_close=lambda: self._begin_close(connection, execution),
        )
        return execution

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
        if command.operation != "stop" and self._selection.busy:
            raise CodexConnectionError("会話の選択・操作が完了していません")
        return await call(execution)

    async def _select_model(
        self, execution: ConversationExecution, model: str, effort: str
    ) -> None:
        if execution.snapshot.thread_id is not None:
            await execution.select_model(model, effort)
        else:
            connection = self._require_browsing(execution)
            await self._selection.select_model(connection, model, effort)

    async def _clear_goal(self, execution: ConversationExecution) -> None:
        if execution.snapshot.thread_id is not None:
            await execution.clear_goal()
        else:
            connection = self._require_browsing(execution)
            if not connection.status.authenticated:
                raise CodexConnectionError("Codexの接続・認証を確認してください")
            await self._selection.clear_goal(
                connection,
                closing=lambda: self._closing is not None or self._connection is not connection,
            )

    def _require_browsing(self, execution: ConversationExecution) -> CodexConnection:
        connection = self._require_management(self._active_generation)
        if execution.snapshot.sending or execution.snapshot.stopping:
            raise CodexConnectionError("会話の送信・停止処理が完了していません")
        return connection

    def _submit_management(
        self,
        operation: "ManagementOperation",
        call: "Callable[[CodexConnection], Awaitable[ManagementValue]]",
        *,
        project_id: str | None = None,
    ) -> None:
        generation = self._generation
        self._enqueue(
            lambda: self._track(
                asyncio.create_task(self._manage(generation, operation, call, project_id))
            )
        )

    async def _manage(
        self,
        generation: int,
        operation: "ManagementOperation",
        call: "Callable[[CodexConnection], Awaitable[ManagementValue]]",
        project_id: str | None,
    ) -> None:
        target = self._selection.target
        value = None
        error = ""
        try:
            value = await call(self._require_management(generation))
        except CodexConnectionError as failure:
            error = str(failure)
        except Exception:  # noqa: BLE001 -- public boundary must not expose upstream text.
            error = "会話管理の操作に失敗しました"
        if operation == "history" and (
            target is not self._selection.target or generation != self._active_generation
        ):
            return
        self.management_completed.emit(
            ManagementResult(generation, operation, value, error, project_id)
        )

    def _require_management(self, generation: int) -> CodexConnection:
        connection = self._connection
        if (
            connection is None
            or generation != self._active_generation
            or self._closing is not None
            or self._ending
        ):
            raise CodexConnectionError("Codexへ接続してから操作してください")
        return connection

    def _require_switch(self, connection: CodexConnection) -> ConversationExecution:
        if (
            self._require_management(self._active_generation) is not connection
            or self._conversation is None
        ):
            raise CodexConnectionError("会話の選択中に接続が変わりました")
        execution = self._conversation
        state = execution.snapshot
        if (
            state.sending
            or state.stopping
            or state.turn_id is not None
            or (state.goal is not None and state.goal.status == "active")
            or self._input.busy
        ):
            raise CodexConnectionError("会話を停止してから切り替えてください")
        return execution

    async def _new_conversation(
        self, connection: CodexConnection, project_id: str | None
    ) -> "ManagementValue":
        self._require_switch(connection)
        return await self._selection.new(
            project_id, release=lambda: self._release_selection(connection)
        )

    async def _select_conversation(
        self, connection: CodexConnection, thread_id: str
    ) -> "ManagementValue":
        self._require_switch(connection)
        return await self._selection.select(
            CodexManagement(connection),
            thread_id,
            release=lambda: self._release_selection(connection),
        )

    async def _release_selection(self, connection: CodexConnection) -> None:
        execution = self._require_switch(connection)
        thread_id = execution.snapshot.thread_id
        if thread_id is not None:
            result = await connection.request("thread/unsubscribe", {"threadId": thread_id})
            if not isinstance(result, dict) or result.get("status") not in {
                "notLoaded",
                "notSubscribed",
                "unsubscribed",
            }:
                raise CodexConnectionError("会話の購読解除結果を確認できません")
        async with self._resources_lock:
            self._require_switch(connection)
            self._selection.retain_goal(execution.snapshot.thread_id, execution.snapshot.goal)
            self._conversation = self._make_execution(connection)
            execution.connection_closing()
            self._receive(self._active_generation, self._conversation.snapshot)
            await self._resources.close_endpoint()
            await execution.drain()
            # Settle old command results before publishing the new selection.
            pending = [
                task
                for task, context in self._tasks.items()
                if context is not None
                and context.operation
                in {"send", "answer", "select_model", "goal_set", "goal_resume", "goal_clear"}
            ]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await self._resources.close()
            self._require_management(self._active_generation)

    def _receive(self, generation: int, event: "ConnectionEvent") -> None:
        if generation == self._active_generation:
            self._record_event(event)
        self.received.emit(ServiceEvent(generation, event))

    def _record_event(self, event: "ConnectionEvent") -> None:
        if isinstance(event, TextEvent) and event.role == "user":
            self._resources.record_dialogue("dialogue.submitted", event.text)
        elif (
            isinstance(event, DialogueEvent)
            and event.kind == "message"
            and event.payload.get("partial") is False
            and isinstance(text := event.payload.get("text"), str)
        ):
            self._resources.record_dialogue("dialogue.message", text)

    async def _change_project(self, path: "Path", *, generation: int, create: bool) -> None:
        error = ""
        try:
            path = await self._select_project(path, generation=generation, create=create)
        except CodexConnectionError as failure:
            error = str(failure)
        except Exception as failure:  # noqa: BLE001 -- sanitize unexpected failures at GUI boundary
            error = recording_reason(failure)
        self.project_completed.emit(ProjectResult(path, error))

    async def _select_project(self, path: "Path", *, generation: int, create: bool) -> "Path":
        if generation != self._active_generation:
            raise CodexConnectionError("この接続ではプロジェクトを選択できません")
        connection = self._connection
        execution = self._conversation
        connection = self._require_project_selection(connection, execution)
        project = await select_project(CodexManagement(connection), path, create=create)
        self._require_project_selection(connection, execution)
        await self._selection.new(project.id, release=lambda: self._release_selection(connection))
        return project.root

    def _require_project_selection(
        self, connection: CodexConnection | None, execution: ConversationExecution | None
    ) -> CodexConnection:
        if (
            connection is None
            or execution is None
            or self._connection is not connection
            or self._conversation is not execution
            or self._closing is not None
            or self._ending
        ):
            raise CodexConnectionError("Codexへ接続してからプロジェクトを選択してください")
        state = execution.snapshot
        if state.thread_id is not None or state.sending or state.stopping:
            raise CodexConnectionError("会話の開始前にプロジェクトを選択してください")
        return connection

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
            execution = self._conversation or execution
            self._closing = asyncio.create_task(
                self._release_connection(connection, execution),
                name="gigas-connection-resources-close",
            )
            execution.connection_closing()
            self._selection.connection_closing()
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
                    self._selection.retain_goal(
                        execution.snapshot.thread_id, execution.snapshot.goal
                    )
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
