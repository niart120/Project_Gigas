"""Conversation admission and orchestration over a connection and owned resources."""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

from gigas.runtime.codex import _conversation_protocol as protocol
from gigas.runtime.codex._human_requests import HumanRequests
from gigas.runtime.codex._notifications import TurnContent
from gigas.runtime.codex.connection import (
    AnswerDeliveryError,
    CodexConnectionError,
    MessageDeliveryError,
)
from gigas.runtime.codex.models import (
    ConversationStatus,
    DialogueEvent,
    GoalEvent,
    RequestResolved,
    SessionStatus,
    TextEvent,
    TurnStarted,
    can_submit,
)
from gigas.runtime.codex.transport import RpcError, RpcNotification, ServerRequest, TransportError
from gigas.services.input import OperationResult

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Mapping

    from gigas.game_io.input.models import InputStep
    from gigas.runtime.codex.connection import CodexConnection, RuntimeEvent
    from gigas.runtime.codex.models import ConnectionEvent, GoalState
    from gigas.runtime.codex.transport import JsonObject, JsonValue
    from gigas.services._conversation_resources import ConversationResources
    from gigas.services.input import InputService


@dataclass(slots=True)
class _Turn:
    id: str
    content: TurnContent
    terminal: asyncio.Future[None]


@dataclass(slots=True)
class _Submission:
    task: asyncio.Task[object] | None = None
    submitted: bool = False


@dataclass(slots=True)
class _Stop:
    task: asyncio.Task[None] | None = None
    interrupts: dict[str, tuple[_Turn, asyncio.Task[None]]] = field(default_factory=dict)

    @property
    def pending(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(task for _, task in self.interrupts.values())


@dataclass(slots=True)
class _GoalReply:
    """Notifications observed only while one Goal RPC is being settled."""

    seen: list["GoalState | None"] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(slots=True)
class _TurnReply:
    """Turn identities observed only while one turn/start is being settled."""

    seen: set[str] = field(default_factory=set)
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class ConversationExecution:
    """Own one thread's facts and its send/stop tasks on the service loop.

    Resources and the input device retain their own lifetimes. Connection-wide
    shutdown is supplied by the service and must never wait for this stop task.
    All callbacks are synchronous; notification processing never waits for RPCs.
    """

    def __init__(
        self,
        connection: "CodexConnection",
        resources: "ConversationResources",
        inputs: "InputService",
        emit: "Callable[[ConnectionEvent], None]",
        *,
        closing: "Callable[[], bool]",
        request_close: "Callable[[], asyncio.Task[None]]",
    ) -> None:
        """Bind a new conversation before opening the connection."""
        self._connection = connection
        self._resources = resources
        self._inputs = inputs
        self._emit = emit
        self._closing = closing
        self._request_close = request_close
        self._thread_id: str | None = None
        self._goal: GoalState | None = None
        self._turn: _Turn | None = None
        self._requests = HumanRequests()
        self._sending: _Submission | None = None
        self._stopping: _Stop | None = None
        self._goal_reply: _GoalReply | None = None
        self._turn_reply: _TurnReply | None = None

    @property
    def snapshot(self) -> ConversationStatus:
        """Project server facts and task ownership without introducing phases."""
        return ConversationStatus(
            self._thread_id,
            self._turn.id if self._turn is not None else None,
            self._goal,
            self._sending is not None,
            self._stopping is not None,
            self._requests.has_blocking,
        )

    def _publish(self) -> None:
        self._emit(self.snapshot)

    def _require_admission(self) -> None:
        if self._closing() or self._stopping is not None:
            raise CodexConnectionError("停止・切断処理中は送信できません")
        if self._connection.status.phase != "ready" or not self._connection.status.authenticated:
            raise CodexConnectionError("Codexの接続・認証を確認してください")

    async def _submit[T](
        self, operation: "Callable[[_Submission], Coroutine[object, object, T]]"
    ) -> T:
        self._require_admission()
        if not can_submit(self._connection.status, self.snapshot):
            raise CodexConnectionError("別の送信処理が完了していません")
        submission = self._sending = _Submission()
        task = asyncio.create_task(operation(submission), name="gigas-conversation-send")
        submission.task = task
        self._publish()
        try:
            return await asyncio.shield(task)
        except RpcError as error:
            reason = f"要求が拒否されました (RPC {error.code})"
            raise CodexConnectionError(reason) from None
        except TransportError:
            if submission.submitted:
                raise MessageDeliveryError("要求の受付結果を確認できません") from None
            raise CodexConnectionError("会話の準備中に接続が失われました") from None
        finally:
            # A canceled caller does not discard ownership of an in-flight write.
            if task.done():
                self._finish_submission(submission)
            else:
                task.add_done_callback(lambda _task: self._finish_submission(submission))

    def _finish_submission(self, submission: _Submission) -> None:
        if submission.task is not None and not submission.task.cancelled():
            submission.task.exception()
        if self._sending is submission:
            self._sending = None
            self._publish()

    async def _prepare(self) -> str:
        await self._connection.require_ready()
        if self._thread_id is not None:
            return self._thread_id
        configuration = await self._resources.prepare(self.start_input)
        selection = self._connection.selection
        config = await self._connection.request(
            "config/read", {"includeLayers": False, "cwd": str(configuration.cwd)}
        )
        params = protocol.thread_start_params(configuration, selection, config)
        result = await self._connection.request("thread/start", params)
        thread_id = protocol.validate_thread(result, configuration, selection)
        self._resources.record_thread(thread_id)
        self._thread_id = thread_id
        self._publish()
        return thread_id

    async def send(self, prompt: str) -> str | None:
        """Submit ordinary or additional text once through turn/start.

        Returns:
            Accepted turn ID, or None when stop canceled preparation before submission.

        Raises:
            CodexConnectionError: Admission, preparation or explicit server rejection failed.
            MessageDeliveryError: A submitted request has unknown acceptance.
        """
        if not prompt.strip():
            raise CodexConnectionError("指示が空です")

        async def submit(submission: _Submission) -> str | None:
            try:
                thread_id = await self._prepare()
            except asyncio.CancelledError:
                if self._stopping is not None or self._closing():
                    return None
                raise
            if self._stopping is not None or self._closing():
                return None
            submission.submitted = True
            self._emit(TextEvent(uuid4().hex, prompt, "user", True))
            return await self._send_turn(thread_id, prompt)

        return await self._submit(submit)

    async def _send_turn(self, thread_id: str, prompt: str) -> str:
        reply = self._turn_reply = _TurnReply()
        if self._turn is not None:
            reply.seen.add(self._turn.id)
        try:
            result = await self._connection.request(
                "turn/start",
                protocol.turn_start_params(thread_id, prompt, self._connection.selection),
            )
            try:
                accepted = protocol.turn_id(result)
            except CodexConnectionError as error:
                raise MessageDeliveryError("指示の受付結果を確認できません") from error
            while accepted not in reply.seen:
                if self._closing():
                    raise MessageDeliveryError("指示の実行通知を確認する前に切断しました")
                reply.changed.clear()
                await reply.changed.wait()
            return accepted
        finally:
            if self._turn_reply is reply:
                self._turn_reply = None

    async def set_goal(self, objective: str) -> bool:
        """Create a Goal or edit its objective without resetting an existing state or budget."""
        if not objective.strip():
            raise CodexConnectionError("目標が空です")

        async def submit(submission: _Submission) -> bool:
            try:
                thread_id = await self._prepare()
            except asyncio.CancelledError:
                if self._stopping is not None or self._closing():
                    return False
                raise
            if self._stopping is not None or self._closing():
                return False
            if self._goal is None:
                selection = self._connection.selection
                await self._connection.request(
                    "thread/settings/update",
                    {
                        "threadId": thread_id,
                        "model": selection.model,
                        "effort": selection.effort,
                    },
                )
            if self._stopping is not None or self._closing():
                return False
            submission.submitted = True
            self._emit(TextEvent(uuid4().hex, objective, "user", True))
            await self._change_goal(
                "thread/goal/set",
                protocol.goal_params(
                    thread_id,
                    objective=objective,
                    status="active" if self._goal is None else None,
                ),
            )
            return True

        return await self._submit(submit)

    async def resume_goal(self) -> bool:
        """Request active for the retained objective without rewriting objective or budget."""
        if self._goal is None or self._goal.status in {"active", "complete"}:
            raise CodexConnectionError("再開できる目標がありません")

        async def submit(submission: _Submission) -> bool:
            await self._connection.require_ready()
            if self._stopping is not None or self._closing():
                return False
            submission.submitted = True
            await self._change_goal(
                "thread/goal/set",
                protocol.goal_params(
                    self._require_thread(),
                    status="active",
                ),
            )
            return True

        return await self._submit(submit)

    async def clear_goal(self) -> None:
        """Withdraw the objective without interrupting its turn or device input."""
        if self._goal is None:
            raise CodexConnectionError("削除する目標がありません")

        async def submit(submission: _Submission) -> None:
            await self._connection.require_ready()
            if self._stopping is not None or self._closing():
                raise CodexConnectionError("停止により目標の削除を送信しませんでした")
            submission.submitted = True
            await self._change_goal(
                "thread/goal/clear", protocol.goal_params(self._require_thread())
            )

        await self._submit(submit)

    def _require_thread(self) -> str:
        if self._thread_id is None:
            raise CodexConnectionError("会話が準備されていません")
        return self._thread_id

    async def _change_goal(self, method: str, params: "JsonObject") -> None:
        reply = self._goal_reply = _GoalReply()
        try:
            result = await self._connection.request(method, params)
            try:
                if method == "thread/goal/clear":
                    if not protocol.goal_clear_result(result):
                        return  # No deletion occurred; the server emits no notification.
                    expected = None
                else:
                    expected = protocol.goal_set_result(result)
            except CodexConnectionError as error:
                raise MessageDeliveryError("目標の変更結果を確認できません") from error
            # Match the RPC result to an observed fact, retaining later notifications.
            # A no-change response (for example budgetLimited) needs no extra event.
            while self._goal != expected and expected not in reply.seen:
                if self._closing():
                    raise MessageDeliveryError("目標の変更通知を確認する前に切断しました")
                reply.changed.clear()
                await reply.changed.wait()
        finally:
            if self._goal_reply is reply:
                self._goal_reply = None

    async def select_model(self, model: str, effort: str) -> None:
        """Commit catalog choices only after required thread settings succeed."""

        async def submit(submission: _Submission) -> None:
            selection = self._connection.propose_model(model, effort)
            if self._thread_id is not None:
                submission.submitted = True
                await self._connection.request(
                    "thread/settings/update",
                    {
                        "threadId": self._thread_id,
                        "model": model,
                        "effort": effort,
                    },
                )
            self._connection.commit_model(selection)

        await self._submit(submit)

    async def stop(self) -> None:
        """Interrupt current work and return after server termination and input release.

        Concurrent callers share one task. A server-side stop failure invokes the
        service's close task; a device release failure alone does not close chat.
        """
        stop = self._stopping
        if stop is None:
            if self._closing():
                raise CodexConnectionError("接続を終了しています")
            stop = self._stopping = _Stop()
            operation = self._inputs.stop()
            submission = self._sending
            self._cancel_preparation()
            self._requests.cancel_unsubmitted()
            answers = self._requests.pending_tasks
            for request in self._requests.expire():
                self._emit(RequestResolved(request.token))
            self._interrupt_current(stop)
            stop.task = asyncio.create_task(
                self._stop(stop, submission, answers, operation),
                name="gigas-conversation-stop",
            )
            stop.task.add_done_callback(_retrieve_exception)
            self._publish()
        if stop.task is not None:
            await asyncio.shield(stop.task)

    def _interrupt_current(self, stop: _Stop) -> None:
        turn = self._turn
        if turn is not None and turn.id not in stop.interrupts:
            task = asyncio.create_task(self._interrupt(turn), name="gigas-turn-interrupt")
            stop.interrupts[turn.id] = turn, task
            task.add_done_callback(self._interrupt_finished)

    def _interrupt_finished(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None and not self._closing():
            # Stop cannot wait for a pending Goal RPC once interruption has failed.
            self._request_close()

    async def _interrupt(self, turn: _Turn) -> None:
        await self._connection.request(
            "turn/interrupt",
            {
                "threadId": self._require_thread(),
                "turnId": turn.id,
            },
        )
        try:
            await asyncio.shield(turn.terminal)
        except asyncio.CancelledError as error:
            if self._closing():
                raise CodexConnectionError("turnの終端を確認する前に切断しました") from error
            raise

    async def _stop(
        self,
        stop: _Stop,
        submission: _Submission | None,
        answers: tuple[asyncio.Task[None], ...],
        operation: asyncio.Task[OperationResult] | None,
    ) -> None:
        input_outcome: OperationResult | BaseException | None = None
        try:
            try:
                if submission is not None and submission.task is not None:
                    (outcome,) = await asyncio.gather(submission.task, return_exceptions=True)
                    self._check_submission_at_stop(outcome, submission.submitted)
                if answers:
                    outcomes = await asyncio.gather(*answers, return_exceptions=True)
                    for outcome in outcomes:
                        self._check_answer_at_stop(outcome)
                self._require_stopping_connection()
                if self._goal is not None and self._goal.status == "active":
                    await self._change_goal(
                        "thread/goal/set",
                        protocol.goal_params(
                            self._require_thread(),
                            status="paused",
                        ),
                    )
                if operation is not None:
                    (input_outcome,) = await asyncio.gather(operation, return_exceptions=True)
                # Notification callbacks may append autonomous turns while pausing.
                while any(not task.done() for task in stop.pending):
                    await asyncio.gather(*stop.pending)
                for task in stop.pending:
                    task.result()
                self._require_stopping_connection()
            except (TransportError, CodexConnectionError) as error:
                await asyncio.shield(self._request_close())
                raise CodexConnectionError(
                    "サーバー側の停止を確認できず、接続を終了しました"
                ) from error
            if isinstance(input_outcome, OperationResult) and input_outcome.code not in {
                "OK",
                "INTERRUPTED",
            }:
                raise CodexConnectionError(input_outcome.message or "入力の解除を確認できません")
        finally:
            # Even a server error must retain the captured input through its release.
            if operation is not None and not operation.done():
                await asyncio.gather(operation, return_exceptions=True)
            pending = tuple(task for task in stop.pending if not task.done())
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if self._stopping is stop:
                self._stopping = None
                self._publish()

    def _require_stopping_connection(self) -> None:
        if self._closing():
            raise CodexConnectionError("停止の完了前に接続が終了しました")

    @staticmethod
    def _check_submission_at_stop(outcome: object, submitted: bool) -> None:
        if isinstance(outcome, RpcError):
            return  # An explicit rejection did not start or change server work.
        if isinstance(outcome, BaseException) and submitted:
            raise CodexConnectionError("送信済み要求の結果が不明です") from outcome

    @staticmethod
    def _check_answer_at_stop(outcome: object) -> None:
        # Only deliveries that had not started are canceled by stop.
        if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
            raise CodexConnectionError("回答の配送結果が不明です") from outcome

    def start_input(
        self, sequence: "tuple[InputStep, ...]"
    ) -> "tuple[str, asyncio.Task[OperationResult]] | OperationResult":
        """Synchronously reject closing/stopping requests or register input with its owner."""
        if self._thread_id is None or self._closing() or self._stopping is not None:
            return OperationResult("INPUT_DISABLED", message="会話を停止・切断しています")
        return self._inputs.start(sequence)

    def receive(self, event: "RuntimeEvent") -> None:
        """Apply a notification or request promptly without waiting for sends or stop."""
        if isinstance(event, ServerRequest):
            self._receive_request(event)
        elif isinstance(event, RpcNotification):
            self._receive_notification(event)
        else:
            self._emit(event)
            if isinstance(event, SessionStatus) and event.phase in {"closed", "error"}:
                self._request_close()

    def _receive_request(self, event: ServerRequest) -> None:
        params = event.params or {}
        if (
            params.get("threadId") != self._thread_id
            or self._turn is None
            or params.get("turnId") != self._turn.id
            or self._stopping is not None
            or self._closing()
            or event.method
            not in {
                "item/tool/requestUserInput",
                "item/commandExecution/requestApproval",
                "item/fileChange/requestApproval",
            }
        ):
            self._requests.reject(event.id, self._connection.reject, -32600, "要求を受け付けません")
            return
        try:
            request = self._requests.accept(event, params)
        except ValueError:
            self._requests.reject(
                event.id, self._connection.reject, -32602, "要求形式に対応していません"
            )
            return
        if request is not None:
            self._emit(request)
            self._publish()

    def _receive_notification(self, event: RpcNotification) -> None:
        params = event.params or {}
        if self._thread_id is None or params.get("threadId") != self._thread_id:
            return
        if event.method == "serverRequest/resolved":
            request_id = params.get("requestId")
            if isinstance(request_id, str | int) and not isinstance(request_id, bool):
                request = self._requests.resolve_rpc_id(request_id)
                if request is not None:
                    self._emit(RequestResolved(request.token))
            self._publish()
        elif event.method in {"turn/started", "turn/completed"}:
            turn_id, status = protocol.turn_notification(params)
            if self._turn_reply is not None:
                self._turn_reply.seen.add(turn_id)
                self._turn_reply.changed.set()
            if event.method == "turn/started":
                if self._turn is None or self._turn.id != turn_id:
                    self._turn = _Turn(
                        turn_id, TurnContent(turn_id), asyncio.get_running_loop().create_future()
                    )
                    self._publish()
                    self._emit(TurnStarted(turn_id))
                    if self._stopping is not None:
                        self._interrupt_current(self._stopping)
            else:
                turn = self._turn if self._turn is not None and self._turn.id == turn_id else None
                if turn is None and self._stopping is not None:
                    interrupted = self._stopping.interrupts.get(turn_id)
                    if interrupted is not None:
                        turn = interrupted[0]
                if turn is None:
                    return
                for item in turn.content.finish():
                    self._emit(item)
                if not turn.terminal.done():
                    turn.terminal.set_result(None)
                if self._turn is turn:
                    self._turn = None
                for request in self._requests.finish(turn_id):
                    self._emit(RequestResolved(request.token))
                self._emit(DialogueEvent("turn_completed", {"status": status}, turn_id))
            self._publish()
        elif event.method in {"thread/goal/updated", "thread/goal/cleared"}:
            self._goal = protocol.goal_state(
                params.get("goal") if event.method.endswith("updated") else None
            )
            if self._goal_reply is not None:
                self._goal_reply.seen.append(self._goal)
                self._goal_reply.changed.set()
            self._emit(GoalEvent(self._goal))
            self._publish()
        elif self._turn is not None and params.get("turnId") == self._turn.id:
            for item in self._turn.content.accept(event.method, params):
                self._emit(item)
            if event.method == "item/fileChange/patchUpdated":
                item_id = params.get("itemId")
                if isinstance(item_id, str):
                    self._file_changes(item_id, params.get("changes"))
            elif event.method in {"item/started", "item/completed"}:
                item = params.get("item")
                if (
                    isinstance(item, dict)
                    and item.get("type") == "fileChange"
                    and isinstance(item_id := item.get("id"), str)
                ):
                    self._file_changes(item_id, item.get("changes"))

    def _file_changes(self, item_id: str, changes: "JsonValue") -> None:
        if self._turn is not None:
            requests = self._requests.update_file_changes(self._turn.id, item_id, changes)
            if requests:
                for request in requests:
                    self._requests.resolve(request.token)
                    self._emit(RequestResolved(request.token))
                    self._requests.reject(
                        request.rpc_id, self._connection.reject, -32600, "確認対象が変更されました"
                    )
                self._publish()

    async def answer(
        self, token: str, *, decision: str | None = None, answers: "Mapping[str, str] | None" = None
    ) -> None:
        """Deliver a live answer to its original RPC ID, independently of message sending."""
        self._require_admission()
        try:
            task = self._requests.answer(token, decision, answers, respond=self._connection.respond)
        except ValueError as error:
            raise CodexConnectionError(str(error)) from error
        self._emit(RequestResolved(token))
        self._publish()
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise CodexConnectionError("停止・切断により回答を送信しませんでした") from None
            raise
        except TransportError as error:
            raise AnswerDeliveryError("回答の配送結果を確認できません") from error

    def connection_closing(self) -> None:
        """Wake notification waiters as failures before the service closes resources."""
        self._cancel_preparation()
        self._requests.cancel_unsubmitted()
        if self._goal_reply is not None:
            self._goal_reply.changed.set()
        if self._turn_reply is not None:
            self._turn_reply.changed.set()
        if self._stopping is not None:
            for turn, _task in self._stopping.interrupts.values():
                if not turn.terminal.done():
                    turn.terminal.cancel()
        for request in self._requests.expire():
            self._emit(RequestResolved(request.token))
        if self._turn is not None:
            for item in self._turn.content.finish():
                self._emit(item)
            if not self._turn.terminal.done():
                self._turn.terminal.cancel()
            self._turn = None
        self._publish()

    def _cancel_preparation(self) -> None:
        submission = self._sending
        if submission is not None and not submission.submitted:
            task = submission.task
            if task is not None and not task.cancelling():
                task.cancel()

    async def drain(self) -> None:
        """Join sending and answer delivery during service-owned close, never the stop task."""
        pending: list[asyncio.Task[object]] = list(self._requests.pending_tasks)
        if self._sending is not None and self._sending.task is not None:
            pending.append(self._sending.task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def _retrieve_exception(task: asyncio.Task[None]) -> None:
    if not task.cancelled():
        task.exception()
