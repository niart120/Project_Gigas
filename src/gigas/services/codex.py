"""Codex connection, conversation and result ownership on the caller's asyncio loop."""

import asyncio
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

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
from gigas.services._project_management import (
    ProjectManagement,
    ProjectUpdateError,
    directory_status,
)
from gigas.services._workspaces import select_project
from gigas.services.codex_models import (
    AnswerOutcome,
    AnswerResult,
    ConnectionFactory,
    MessageResult,
    ProjectResult,
    ServiceEvent,
    ServiceOperation,
    ServiceResult,
)
from gigas.services.conversations import ManagementResult, StoredConversationTarget
from gigas.services.input import InputService, OperationResult
from gigas.services.projects import ProjectFolderStatus

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Mapping
    from pathlib import Path

    from gigas.game_io.capture import CaptureReader
    from gigas.game_io.observation import ObservationResolution
    from gigas.runtime.codex.connection import RuntimeEvent
    from gigas.runtime.codex.management import ProjectInfo
    from gigas.runtime.codex.models import (
        ConnectionEvent,
        ConnectionOptions,
    )
    from gigas.services.conversations import (
        BrowseGoalEvent,
        ConversationTarget,
        HistoryCursor,
        ManagementOperation,
        ManagementValue,
    )

__all__ = [
    "AnswerOutcome",
    "AnswerResult",
    "CodexService",
    "ConnectionFactory",
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


class CodexService:
    """Own Codex work on one loop, notifying the application without Qt objects.

    Input is borrowed: close recovers Codex work but never closes the device.
    Command methods schedule work on the calling loop and report typed results.
    All notification callbacks run synchronously on that same loop and must not raise.

    Args:
        data_root: Application data directory containing codex_home and unassigned chats.
        reader: Shared read-only capture view for conversation observations.
        inputs: Input service borrowed on the same loop; its owner closes the device.
        received: Connection and conversation event callback.
        completed: Codex command completion callback.
        project_completed: Project selection result callback.
        management_completed: Conversation management result callback.
        browse_goal_changed: Request number and target-specific browsing Goal callback.
        recording_failed: Operation identifier, record path and display-safe reason callback.
        factory: Runtime factory, or None to use the bundled app-server and app codex_home.
    """

    def __init__(
        self,
        data_root: "Path",
        reader: "CaptureReader",
        inputs: InputService,
        *,
        received: "Callable[[ServiceEvent], None]",
        completed: "Callable[[ServiceResult], None]",
        project_completed: "Callable[[ProjectResult], None]",
        management_completed: "Callable[[ManagementResult], None]",
        browse_goal_changed: "Callable[[int, BrowseGoalEvent], None]",
        recording_failed: "Callable[[str, str, str], None]",
        factory: ConnectionFactory | None = None,
    ) -> None:
        """Bind borrowed input and synchronous result callbacks without connecting."""
        self.received = received
        self.completed = completed
        self.project_completed = project_completed
        self.management_completed = management_completed
        self.browse_goal_changed = browse_goal_changed
        self._factory = (
            partial(create_connection, codex_home=data_root / "codex_home")
            if factory is None
            else factory
        )
        self._input = inputs
        self._resources = ConversationResources(data_root, reader, recording_failed)
        self._selection = ConversationSelection()
        self._execution_settings = ExecutionSettings()
        self._conversation: ConversationExecution | None = None
        self._resources_lock = asyncio.Lock()
        self._generation = 0
        self._active_generation = 0
        self._ending = False
        self._connection: CodexConnection | None = None
        self._closing: asyncio.Task[None] | None = None
        self._tasks: dict[asyncio.Task[None], _OperationContext | None] = {}
        self._project_task: asyncio.Task[object] | None = None

    def connect_runtime(self, options: "ConnectionOptions", generation: int) -> None:
        """Start the connection attempt identified by the application's request number."""
        self._generation = generation
        self._apply_execution_settings(options.execution_settings)
        context = _OperationContext(generation, "connect")
        self._start_operation(context, lambda: self._connect(options, generation))

    async def close(self) -> None:
        """Close Codex and recover its tasks and records, leaving input connected."""
        self._ending = True
        try:
            await self._close_connection()
        finally:
            pending = list(self._tasks)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            async with self._resources_lock:
                await self._resources.close()

    @property
    def selected_target(self) -> "ConversationTarget":
        """Return the current immutable selection reference for identity checks.

        Reading does not reserve the selection. The service queue may adopt a
        different target later; the returned object itself is never changed.
        """
        return self._selection.target

    def list_projects(self, *, cursor: str | None = None) -> None:
        """Read one public Project page; management_completed carries its result."""
        self._submit_management(
            "projects", lambda c: CodexManagement(c).list_projects(cursor=cursor)
        )

    def inspect_project(self, project_id: str) -> None:
        """Read all project members and directory availability without execution."""
        self._submit_management(
            "project_inspect",
            lambda c: self._project_manager(c).inspect(project_id),
            project_id=project_id,
        )

    def probe_project_folder(self, project: "ProjectInfo") -> None:
        """Check only one listed folder, retaining lazy conversation-list fetching."""
        self._submit_management(
            "project_folder", lambda _c: self._probe_folder(project), project_id=project.id
        )

    async def _probe_folder(self, project: "ProjectInfo") -> ProjectFolderStatus:
        status = await asyncio.to_thread(directory_status, project.root)
        return ProjectFolderStatus(project, status)

    def update_project(self, expected: "ProjectInfo", *, name: str, root: "Path") -> None:
        """Save an edit and reconcile member workspaces through public metadata."""
        self._submit_management(
            "project_update",
            lambda c: self._update_project(c, expected, name=name, root=root),
            project_id=expected.id,
        )

    def delete_project(self, expected: "ProjectInfo") -> None:
        """Delete a registration while retaining the selected conversation and draft."""
        self._submit_management(
            "project_delete", lambda c: self._delete_project(c, expected), project_id=expected.id
        )

    def _project_manager(self, connection: CodexConnection) -> ProjectManagement:
        generation = self._active_generation

        def check() -> None:
            if self._require_management(generation) is not connection:
                raise CodexConnectionError("プロジェクト操作中に接続が変わりました")

        return ProjectManagement(
            connection,
            check=check,
            release=lambda: self._release_selection(connection),
            settings=self._execution_settings,
        )

    async def _update_project(
        self, connection: CodexConnection, expected: "ProjectInfo", *, name: str, root: "Path"
    ) -> "ManagementValue":
        result = await self._project_manager(connection).update(expected, name=name, root=root)
        if not result.pending:
            self._selection.refresh_project(expected.id, result.project.root)
        return result

    async def _delete_project(
        self, connection: CodexConnection, expected: "ProjectInfo"
    ) -> "ManagementValue":
        generation = self._active_generation
        result = await self._project_manager(connection).delete(expected)
        target = self._selection.target
        if isinstance(target, StoredConversationTarget) and target.project_id == expected.id:
            metadata = await CodexManagement(connection).read_thread(target.thread_id)
            if self._require_management(generation) is not connection:
                raise CodexConnectionError("プロジェクト操作中に接続が変わりました")
            self._selection.refresh_thread(metadata)
        else:
            self._selection.refresh_project(expected.id, None)
        return result

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

    def load_history(self, *, cursor: "HistoryCursor | None" = None) -> None:
        """Read one extra page without reserving submission or selection admission.

        Results, including failures, are discarded when their selection instance
        is no longer current. Cursors are passed through without interpretation.
        """
        self._submit_management(
            "history", lambda c: self._selection.history(CodexManagement(c), cursor=cursor)
        )

    def create_project(self, path: "Path") -> None:
        """Queue exclusive directory creation and select a new conversation there.

        Requires an open connection and stopped execution, including input recovery.
        project_completed carries the request generation and public Project on
        success; failure preserves the previous selection without deleting files.

        Args:
            path: New directory whose parent already exists.
        """
        generation = self._generation
        self._track(
            asyncio.create_task(
                _finish_io(self._change_project(path, generation=generation, create=True))
            )
        )

    def open_project(self, path: "Path") -> None:
        """Queue server Project lookup or registration for an existing directory.

        Requires an open connection and stopped execution, including input recovery.
        The first matching server root is reused; an unregistered directory becomes
        a new Project. A delayed lookup cannot replace an intervening selection.

        Args:
            path: Existing workspace directory for a new conversation.
        """
        generation = self._generation
        self._track(
            asyncio.create_task(
                _finish_io(self._change_project(path, generation=generation, create=False))
            )
        )

    def set_observation_resolution(self, resolution: "ObservationResolution") -> None:
        """Apply a size to later observations on the owning loop without reconnecting.

        Args:
            resolution: Validated application setting: original, full_hd, hd, or sd.
        """
        self._resources.set_resolution(resolution)

    def set_execution_settings(self, settings: ExecutionSettings) -> None:
        """Queue saved options for future conversation preparation without sending an RPC.

        Args:
            settings: Last successfully loaded or saved application options. An ongoing
                preparation retains its snapshot; a loaded runtime thread is unchanged.
        """
        self._apply_execution_settings(settings)

    def _apply_execution_settings(self, settings: ExecutionSettings) -> None:
        self._execution_settings = settings
        if self._conversation is not None:
            self._conversation.set_execution_settings(settings)

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
        """Schedule one answer, retaining a copy of mutable answers on this loop.

        Args:
            token: Live connection-unique request token.
            decision: One of the request's advertised approval decisions.
            answers: Text indexed by all question identifiers, or None for approval.

        The completed callback reports this token's AnswerResult. Calls rejected
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
        """Request Codex execution and finite-input stop, reporting its completion."""
        self._submit("stop", lambda execution: execution.stop())

    @property
    def manual_blocked(self) -> bool:
        """Whether current Codex execution blocks manual input on the shared loop."""
        return (
            self._closing is None
            and self._conversation is not None
            and self._conversation.manual_blocked
        )

    @property
    def execution_busy(self) -> bool:
        """Whether current Codex work must stop before a device connection changes."""
        if self._conversation is None:
            return False
        snapshot = self._conversation.snapshot
        return (
            snapshot.sending
            or snapshot.stopping
            or snapshot.turn_id is not None
            or (snapshot.goal is not None and snapshot.goal.status == "active")
        )

    async def stop_execution(self) -> None:
        """Wait for current Codex execution to stop, raising if stop is unconfirmed.

        This is an application coordination boundary; it emits no command result
        and does not release unrelated input when there is no conversation.
        """
        task = self._project_task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._conversation is not None:
            await self._conversation.stop()

    def disconnect_runtime(self) -> None:
        """Close the current connection while keeping the service loop available."""
        self._submit_connection("disconnect", lambda _connection: self._close_connection())

    def _submit(
        self, operation: ServiceOperation, call: _ConversationCall, *, token: str = ""
    ) -> None:
        context = _OperationContext(self._generation, operation, token)
        self._start_operation(context, lambda: self._invoke(context, call))

    def _submit_connection(
        self, operation: ServiceOperation, call: "Callable[[CodexConnection], Awaitable[None]]"
    ) -> None:
        context = _OperationContext(self._generation, operation)
        self._start_operation(context, lambda: self._invoke_connection(context, call))

    def _start_operation(self, context: _OperationContext, call: _OperationCall) -> None:
        if self._ending:
            self._reject_submission(context)
            return
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
                            self.browse_goal_changed(generation, goal)
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
                return None
            raise CodexConnectionError("この接続では送信を受け付けない")
        if self._project_task is not None:
            if command.operation == "stop":
                await self.stop_execution()
                return None
            raise CodexConnectionError("プロジェクトの更新が完了していません")
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
        self._track(asyncio.create_task(self._manage(generation, operation, call, project_id)))

    def _admit_management(
        self, connection: CodexConnection, operation: "ManagementOperation"
    ) -> None:
        if self._project_task is not None and operation in {
            "new",
            "select",
            "project_update",
            "project_delete",
        }:
            raise CodexConnectionError("プロジェクトの更新が完了していません")
        if operation in {"project_update", "project_delete"}:
            self._require_switch(connection)
            if self._selection.busy:
                raise CodexConnectionError("会話の選択が完了していません")

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
        mutation = operation in {"project_update", "project_delete"}
        owner = asyncio.current_task()
        connection = None
        try:
            connection = self._require_management(generation)
            self._admit_management(connection, operation)
            if mutation:
                self._project_task = owner
            value = await call(connection)
        except ProjectUpdateError as failure:
            error = str(failure)
            if connection is self._connection and generation == self._active_generation:
                await self._close_connection()
        except asyncio.CancelledError:
            error = "プロジェクト操作を中断しました。接続し直し、保存状態を確認してください"
            if (
                mutation
                and connection is self._connection
                and generation == self._active_generation
            ):
                await self._close_connection()
        except CodexConnectionError as failure:
            error = str(failure)
        except Exception:  # noqa: BLE001 -- public boundary must not expose upstream text.
            error = "会話管理の操作に失敗しました"
        finally:
            if self._project_task is owner:
                self._project_task = None
        if operation == "history" and (
            target is not self._selection.target or generation != self._active_generation
        ):
            return
        self.management_completed(ManagementResult(generation, operation, value, error, project_id))

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
        self.received(ServiceEvent(generation, event))

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
        project = None
        try:
            project = await self._select_project(path, generation=generation, create=create)
            path = project.root
        except CodexConnectionError as failure:
            error = str(failure)
        except Exception as failure:  # noqa: BLE001 -- sanitize unexpected failures at GUI boundary
            error = recording_reason(failure)
        self.project_completed(ProjectResult(generation, path, project, error))

    async def _select_project(
        self, path: "Path", *, generation: int, create: bool
    ) -> "ProjectInfo":
        if self._project_task is not None:
            raise CodexConnectionError("プロジェクトの更新が完了していません")
        connection = self._require_management(generation)
        execution = self._require_switch(connection)
        target = self._selection.target
        project = await select_project(CodexManagement(connection), path, create=create)
        if (
            self._require_management(generation) is not connection
            or self._require_switch(connection) is not execution
            or self._selection.target is not target
        ):
            raise CodexConnectionError("プロジェクトの取得中に会話の選択が変わりました")
        await self._selection.new(project.id, release=lambda: self._release_selection(connection))
        return project

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
            operation = self._input.stop_finite()
            self._closing = asyncio.create_task(
                self._release_connection(connection, execution, operation),
                name="gigas-connection-resources-close",
            )
            execution.connection_closing()
            self._selection.connection_closing()
        return self._closing

    async def _release_connection(
        self,
        connection: CodexConnection,
        execution: ConversationExecution,
        operation: "asyncio.Task[OperationResult] | None",
    ) -> None:
        # Never join the stop task or Qt command that requested this shutdown.
        outcomes = await asyncio.gather(
            connection.close(), self._resources.close_endpoint(), return_exceptions=True
        )
        drained = await asyncio.gather(
            execution.drain(), self._input.wait_operation(operation), return_exceptions=True
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
            if isinstance(outcome, OperationResult) and outcome.code not in {"OK", "INTERRUPTED"}:
                message = f"Codex切断時の入力解除に失敗しました: {outcome.message}"
                raise CodexConnectionError(message)

    def _reject_submission(self, command: _OperationContext) -> None:
        result = rejected_result(command)
        if result is not None:
            self.completed(result)

    def _complete(
        self,
        command: _OperationContext,
        error: str,
        outcome: AnswerOutcome,
        failure_id: int | None = None,
        turn_id: str | None = None,
        goal_accepted: bool = False,
    ) -> None:
        self.completed(
            operation_result(command, error, outcome, failure_id, turn_id, goal_accepted)
        )


async def _finish_io[T](operation: "Coroutine[object, object, T]") -> T:
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


def operation_result(
    command: _OperationContext,
    error: str,
    outcome: AnswerOutcome,
    failure_id: int | None = None,
    turn_id: str | None = None,
    goal_accepted: bool = False,
) -> ServiceResult:
    """Build the existing delivery result without depending on a live service loop."""
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
    return ServiceResult(command.generation, command.operation, error, answer, failure_id, message)


def rejected_result(command: _OperationContext) -> ServiceResult | None:
    """Describe a request rejected before delivery, including after thread exit."""
    if command.operation == "setup_windows_sandbox":
        return operation_result(command, "実行環境の準備を開始していない", "not_sent")
    if command.operation in {
        "answer",
        "send",
        "select_model",
        "goal_set",
        "goal_resume",
        "goal_clear",
    }:
        return operation_result(command, "送信されていない", "not_sent")
    return None
