"""Qt delivery and the lifetime of services sharing one background asyncio loop."""

import asyncio
import logging
from threading import Lock
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

from gigas.runtime.codex.connection import CodexConnectionError
from gigas.runtime.codex.models import ExecutionSettings
from gigas.services.codex import CodexService, _OperationContext, rejected_result
from gigas.services.codex_models import (
    AnswerResult,
    ConnectionFactory,
    MessageResult,
    ProjectResult,
    ServiceEvent,
    ServiceResult,
)
from gigas.services.input import (
    InputConnectionResult,
    InputService,
    InputServiceError,
    OperationResult,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from gigas.game_io.capture import CaptureReader
    from gigas.game_io.input.config import SerialConfig
    from gigas.game_io.input.models import InputState
    from gigas.game_io.observation import ObservationResolution
    from gigas.runtime.codex.management import ProjectInfo
    from gigas.runtime.codex.models import ConnectionOptions
    from gigas.services.conversations import ConversationTarget, HistoryCursor
    from gigas.services.input import InputDevice

__all__ = [
    "AnswerResult",
    "ApplicationServiceThread",
    "ConnectionFactory",
    "InputConnectionResult",
    "MessageResult",
    "ProjectResult",
    "ServiceEvent",
    "ServiceResult",
]


class ApplicationServiceThread(QThread):
    """Deliver GUI commands and own Codex and input on one background loop.

    Starting the thread opens no connections. shutdown rejects new work and
    returns immediately; finished and wait(0) confirm thread exit. A nonempty
    shutdown_error means cleanup was not confirmed.

    Args:
        data_root: Application data directory containing codex_home and unassigned chats.
        reader: Shared read-only capture view for conversation observations.
        factory: Runtime connection factory, or None for the bundled app-server.
        input_factory: Device factory called on the service loop from resolved settings.
    """

    received = Signal(object)
    completed = Signal(object)
    project_completed = Signal(object)
    management_completed = Signal(object)
    browse_goal_changed = Signal(int, object)
    recording_failed = Signal(str, str, str)
    input_changed = Signal(object)
    input_busy_changed = Signal(bool)
    input_ports = Signal(object, str)
    manual_changed = Signal(bool, object)
    manual_completed = Signal(object)

    def __init__(
        self,
        data_root: "Path",
        reader: "CaptureReader",
        factory: ConnectionFactory | None = None,
        *,
        input_factory: "Callable[[SerialConfig], InputDevice] | None" = None,
    ) -> None:
        """Bind service dependencies without starting a thread or connecting devices."""
        super().__init__()
        self.inputs = InputService(
            factory=input_factory,
            on_connection_changed=self._input_changed,
            on_busy_changed=self._input_busy_changed,
            on_manual_result=self.manual_completed.emit,
        )
        self.codex = CodexService(
            data_root,
            reader,
            self.inputs,
            factory=factory,
            received=self._receive,
            completed=self.completed.emit,
            project_completed=self.project_completed.emit,
            management_completed=self.management_completed.emit,
            browse_goal_changed=self.browse_goal_changed.emit,
            recording_failed=self.recording_failed.emit,
        )
        self._input_apply_task: asyncio.Task[None] | None = None
        self._lock = Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queued: list[tuple[Callable[[], None], _OperationContext | None]] = []
        self._ending = False
        self._exit_requested = False
        self._stops_pending = 0
        self._generation = 0
        self._tasks: set[asyncio.Task[None]] = set()
        self._done: asyncio.Event | None = None
        self.shutdown_error = ""

    def _receive(self, event: ServiceEvent) -> None:
        self.received.emit(event)
        self._publish_manual()

    def _input_changed(self, result: InputConnectionResult) -> None:
        self.input_changed.emit(result)
        self._publish_manual()

    def _input_busy_changed(self, busy: bool) -> None:
        self.input_busy_changed.emit(busy)
        self._publish_manual()

    def _manual_allowed(self) -> bool:
        return (
            not self._ending
            and not self._exit_requested
            and not self._stops_pending
            and not self.codex.manual_blocked
            and self.inputs.manual_available
        )

    def _publish_manual(self) -> None:
        self.manual_changed.emit(self._manual_allowed(), self.inputs.protocol)

    def update_manual(self, state: "InputState") -> None:
        """Queue a complete manual state, checking execution admission on this loop."""
        self._enqueue(lambda: self._track(asyncio.create_task(self._update_manual(state))))

    async def _update_manual(self, state: "InputState") -> None:
        # Match Codex command task ordering so an earlier send registers first.
        if self._manual_allowed():
            result = self.inputs.update_manual(state)
            if result.code != "OK":
                self.manual_completed.emit(result)
        self._publish_manual()

    def release_manual(self) -> None:
        """Queue manual-only release; closing a window never stops Codex or finite input."""
        self._enqueue(lambda: self._track(asyncio.create_task(self._release_manual())))

    async def _release_manual(self) -> None:
        # Keep the same task ordering as preceding manual updates.
        self.inputs.stop_manual()
        self._publish_manual()

    def set_exit_requested(self, requested: bool) -> None:
        """Suspend manual input from exit request through confirmation or cancellation."""
        self._enqueue(lambda: self._track(asyncio.create_task(self._set_exit_requested(requested))))

    async def _set_exit_requested(self, requested: bool) -> None:
        # Exit/cancellation must not overtake manual updates already dispatched.
        self._exit_requested = requested
        if requested:
            self.inputs.stop_manual()
        self._publish_manual()

    @property
    def selected_target(self) -> "ConversationTarget":
        """Return the Codex service's current immutable conversation selection."""
        return self.codex.selected_target

    def connect_runtime(self, options: "ConnectionOptions") -> int:
        """Queue a connection and return the request number used by its results."""
        self._generation += 1
        generation = self._generation
        context = _OperationContext(generation, "connect")
        self._enqueue(lambda: self.codex.connect_runtime(options, generation), context)
        return generation

    def apply_input(self, config: "SerialConfig | None") -> None:
        """Apply resolved saved input settings; None explicitly disconnects."""
        self._enqueue(lambda: self._accept_input(config))

    def refresh_input_ports(self) -> None:
        """Enumerate serial ports without opening a connection."""
        self._enqueue(lambda: self._track(asyncio.create_task(self._refresh_input_ports())))

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
        except Exception:
            logger.exception(
                "Application service operation failed",
                extra={"event": "service.failed", "operation": "run"},
            )
            self.shutdown_error = "接続サービスの終了失敗"
        finally:
            with self._lock:
                self._loop = None
                self._ending = True

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

    def _signal_shutdown(self) -> None:
        self.inputs.begin_close()
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

    def _accept_input(self, config: "SerialConfig | None") -> None:
        self.inputs.request_config(config)
        self._publish_manual()
        if self._input_apply_task is not None and not self._input_apply_task.done():
            return
        self._input_apply_task = asyncio.create_task(self._apply_input())
        self._track(self._input_apply_task)

    async def _apply_input(self) -> None:
        try:
            while self.inputs.config_pending and not self._ending:
                if self.inputs.connection_change_required and self.codex.execution_busy:
                    await self.codex.stop_execution()
                if self._ending:
                    return
                await self.inputs.apply_config()
        except CodexConnectionError as error:
            logger.warning(
                "Application service operation failed",
                extra={"event": "service.failed", "operation": "_apply_input", "error": str(error)},
            )
            self.inputs.reject_config(str(error))
        except Exception:
            logger.exception(
                "Application service operation failed",
                extra={"event": "service.failed", "operation": "_apply_input"},
            )
            self.inputs.reject_config("入力接続を変更する前の停止に失敗しました")
        finally:
            self._publish_manual()

    async def _refresh_input_ports(self) -> None:
        try:
            ports = await self.inputs.list_ports()
        except Exception as error:
            logger.exception(
                "Application service operation failed",
                extra={"event": "service.failed", "operation": "_refresh_input_ports"},
            )
            self.input_ports.emit((), str(error))
        else:
            self.input_ports.emit(ports, "")

    def list_projects(self, *, cursor: str | None = None) -> None:
        """Read one public Project page; management_completed carries its result."""
        self._enqueue(lambda: self.codex.list_projects(cursor=cursor))

    def inspect_project(self, project_id: str) -> None:
        """Inspect a project and all members; deliver project_inspect via management_completed."""
        self._enqueue(lambda: self.codex.inspect_project(project_id))

    def probe_project_folder(self, project: "ProjectInfo") -> None:
        """Check a folder asynchronously without loading a project's conversation list."""
        self._enqueue(lambda: self.codex.probe_project_folder(project))

    def update_project(self, expected: "ProjectInfo", *, name: str, root: "Path") -> None:
        """Queue one project edit using the metadata shown when its dialog opened."""
        self._enqueue(lambda: self.codex.update_project(expected, name=name, root=root))

    def delete_project(self, expected: "ProjectInfo") -> None:
        """Queue a confirmed registration deletion; files and conversations remain."""
        self._enqueue(lambda: self.codex.delete_project(expected))

    def list_threads(self, project_id: str | None, *, cursor: str | None = None) -> None:
        """Read one assignment page; None explicitly requests unassigned threads."""
        self._enqueue(lambda: self.codex.list_threads(project_id, cursor=cursor))

    def list_archived_threads(self, *, cursor: str | None = None) -> None:
        """Queue one page of archived threads across project assignments."""
        self._enqueue(lambda: self.codex.list_archived_threads(cursor=cursor))

    def archive_threads(self, project_id: str | None, thread_ids: tuple[str, ...]) -> None:
        """Archive the IDs chosen by the user under their confirmed assignment."""
        self._enqueue(lambda: self.codex.archive_threads(project_id, thread_ids))

    def restore_thread(self, thread_id: str) -> None:
        """Restore one archived thread while preserving the current selection."""
        self._enqueue(lambda: self.codex.restore_thread(thread_id))

    def auto_archive_threads(self, days: int) -> None:
        """Queue the connection-time scan of eligible threads."""
        self._enqueue(lambda: self.codex.auto_archive_threads(days))

    def new_conversation(self, project_id: str | None = None) -> None:
        """Select an unstarted assignment after releasing an idle prior conversation."""
        self._enqueue(lambda: self.codex.new_conversation(project_id))

    def select_conversation(self, thread_id: str) -> None:
        """Read saved history without resuming; a running conversation must stop first."""
        self._enqueue(lambda: self.codex.select_conversation(thread_id))

    def load_history(self, *, cursor: "HistoryCursor | None" = None) -> None:
        """Read one extra page without reserving submission or selection admission.

        Results, including failures, are discarded when their selection instance
        is no longer current. Cursors are passed through without interpretation.
        """
        self._enqueue(lambda: self.codex.load_history(cursor=cursor))

    def create_project(self, path: "Path") -> None:
        """Queue exclusive directory creation and select a new conversation there.

        Requires an open connection and stopped execution, including input recovery.
        project_completed carries the request generation and public Project on
        success; failure preserves the previous selection without deleting files.

        Args:
            path: New directory whose parent already exists.
        """
        self._enqueue(lambda: self.codex.create_project(path))

    def open_project(self, path: "Path") -> None:
        """Queue server Project lookup or registration for an existing directory.

        Requires an open connection and stopped execution, including input recovery.
        The first matching server root is reused; an unregistered directory becomes
        a new Project. A delayed lookup cannot replace an intervening selection.

        Args:
            path: Existing workspace directory for a new conversation.
        """
        self._enqueue(lambda: self.codex.open_project(path))

    def set_observation_resolution(self, resolution: "ObservationResolution") -> None:
        """Apply a size to later observations on the owning loop without reconnecting.

        Args:
            resolution: Validated observation image size from application settings.
        """
        self._enqueue(lambda: self.codex.set_observation_resolution(resolution))

    def set_execution_settings(self, settings: ExecutionSettings) -> None:
        """Queue saved options for future conversation preparation without sending an RPC.

        Args:
            settings: Last successfully loaded or saved application options. An ongoing
                preparation retains its snapshot; a loaded runtime thread is unchanged.
        """
        self._enqueue(lambda: self.codex.set_execution_settings(settings))

    def send(self, prompt: str, service_tier: str | None = None) -> None:
        """Submit ordinary or additional text through the same turn/start path.

        Args:
            prompt: User text passed unchanged to conversation execution.
            service_tier: Speed captured when the user submitted the message.
        """
        context = _OperationContext(self._generation, "send")
        self._enqueue(lambda: self.codex.send(prompt, service_tier), context)

    def set_goal(self, objective: str, service_tier: str | None = None) -> None:
        """Submit the objective through the conversation's shared send operation.

        Args:
            objective: User's Goal text.
            service_tier: Speed for a newly activated Goal.
        """
        context = _OperationContext(self._generation, "goal_set")
        self._enqueue(lambda: self.codex.set_goal(objective, service_tier), context)

    def resume_goal(self, service_tier: str | None = None) -> None:
        """Request active status without replacing the retained objective.

        Args:
            service_tier: Speed to apply before resuming the Goal.
        """
        context = _OperationContext(self._generation, "goal_resume")
        self._enqueue(lambda: self.codex.resume_goal(service_tier), context)

    def clear_goal(self) -> None:
        """Withdraw the Goal without interrupting the current turn or input."""
        context = _OperationContext(self._generation, "goal_clear")
        self._enqueue(self.codex.clear_goal, context)

    def select_model(self, model: str, effort: str) -> None:
        """Submit next-turn settings through the shared conversation send operation."""
        context = _OperationContext(self._generation, "select_model")
        self._enqueue(lambda: self.codex.select_model(model, effort), context)

    def login(self) -> None:
        """Queue explicit ChatGPT login on the connection owner."""
        context = _OperationContext(self._generation, "login")
        self._enqueue(self.codex.login, context)

    def setup_windows_sandbox(self) -> None:
        """Queue explicit Windows preparation without blocking disconnect."""
        context = _OperationContext(self._generation, "setup_windows_sandbox")
        self._enqueue(self.codex.setup_windows_sandbox, context)

    def interrupt(self) -> None:
        """Request common stop; the result reports actual stop completion."""
        context = _OperationContext(self._generation, "stop")
        self._enqueue(lambda: self._track(asyncio.create_task(self._stop(context))), context)

    async def _stop(self, context: _OperationContext) -> None:
        logger.info(
            "Application stop requested",
            extra={"event": "service.stop_requested", "connection_id": context.generation},
        )
        self._stops_pending += 1
        operation = self.inputs.stop()
        self._publish_manual()
        error = ""
        try:
            try:
                await self.codex.stop_execution()
            finally:
                if operation is not None:
                    (outcome,) = await asyncio.gather(operation, return_exceptions=True)
                    if isinstance(outcome, OperationResult) and outcome.code not in {
                        "OK",
                        "INTERRUPTED",
                    }:
                        raise InputServiceError(outcome.code, outcome.message)
        except (CodexConnectionError, InputServiceError) as failure:
            error = str(failure)
        except Exception:
            logger.exception(
                "Application service operation failed",
                extra={"event": "service.failed", "operation": "_stop"},
            )
            error = "停止の完了を確認できません"
        finally:
            self._stops_pending -= 1
            self._publish_manual()
        logger.log(
            logging.ERROR if error else logging.INFO,
            "Application stop completed",
            extra={
                "event": "service.stop_completed",
                "connection_id": context.generation,
                "error": error,
            },
        )
        self.completed.emit(ServiceResult(context.generation, "stop", error))

    def disconnect_runtime(self) -> None:
        """Close the current connection while keeping the service loop available."""
        context = _OperationContext(self._generation, "disconnect")
        self._enqueue(self.codex.disconnect_runtime, context)

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
        answers = None if answers is None else dict(answers)
        context = _OperationContext(self._generation, "answer", token)
        self._enqueue(lambda: self.codex.answer(token, decision=decision, answers=answers), context)

    async def _serve(self) -> None:
        asyncio.get_running_loop().set_exception_handler(self._loop_exception)
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
            await self.codex.close()
            logger.info("Codex resources closed", extra={"event": "service.codex_closed"})
        finally:
            try:
                await self.inputs.close()
                logger.info("Input resources closed", extra={"event": "service.input_closed"})
            finally:
                pending = list(self._tasks)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

    def _track(self, task: asyncio.Task[None]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._task_finished)

    def _task_finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error(
                "Application service task failed",
                exc_info=error,
                extra={"event": "service.task_failed", "task_name": task.get_name()},
            )

    @staticmethod
    def _loop_exception(_loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        error = context.get("exception")
        logger.error(
            "Uncaught service-loop exception",
            exc_info=error if isinstance(error, BaseException) else None,
            extra={"event": "service.uncaught_exception", "details": context.get("message")},
        )

    def _reject_submission(self, command: _OperationContext) -> None:
        result = rejected_result(command)
        if result is not None:
            self.completed.emit(result)
