"""Translate conversation facts into input permission and own run termination."""

import asyncio
from typing import TYPE_CHECKING

from gigas.runtime.codex.connection import AnswerDeliveryError, CodexConnectionError
from gigas.runtime.codex.models import (
    GoalEvent,
    HumanRequest,
    RequestResolved,
    SessionStatus,
    TurnStarted,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from gigas.runtime.codex.models import ConnectionEvent, GoalStatus
    from gigas.services.input import InputGrant, InputService
    from gigas.services.observation import ObservationService
    from gigas.services.recording import RecordingService


class ConversationRun:
    """Own permission policy and its finish task, borrowing the input service."""

    def __init__(self, inputs: "InputService") -> None:
        """Prepare a run policy without issuing input permission."""
        self._input = inputs
        self._grant: InputGrant | None = None
        self._input_allowed = False
        self._message = ""
        self._finishing: asyncio.Task[None] | None = None
        self._waiting_answers: set[str] = set()
        self._sending_answers: set[str] = set()
        self._goal_status: GoalStatus | None = None
        self._goal_preparing = False
        self._turn_id: str | None = None
        self._turn_running = False

    @property
    def goal_active_or_preparing(self) -> bool:
        """Return whether changing input settings must request a Goal stop."""
        return self._goal_preparing or self._goal_status == "active"

    @property
    def turn_id(self) -> str | None:
        """Return the current adopted turn for dialogue correlation."""
        return self._turn_id

    @property
    def run_id(self) -> str | None:
        """Return the current run's ID without exposing its Grant."""
        return self._grant.run_id if self._grant is not None else None

    def reset_connection(self) -> None:
        """Forget Goal and turn facts when a fresh runtime connection is created."""
        self._goal_status = None
        self._turn_id = None

    def prepare(self, message: str) -> None:
        """Set initial permission before yielding, so later stops remain authoritative."""
        self._input_allowed = self._goal_preparing or self._goal_status in {None, "complete"}
        self._message = message

    async def begin(
        self,
        thread_id: str,
        observations: "ObservationService",
        recording: "RecordingService",
    ) -> "InputGrant":
        """Begin a prepared run and return its Grant for endpoint binding."""
        self._grant = await self._input.begin_run(self._message, thread_id, observations, recording)
        self._input.suspend()
        if not self._input_allowed:
            self._input.stop("実行準備中に停止または入力接続の変更がありました")
        self._waiting_answers.clear()
        self._sending_answers.clear()
        return self._grant

    def stop(self) -> None:
        """Synchronously revoke permission, including before a Grant is issued."""
        self._input_allowed = False
        self._input.stop()

    def receive(self, event: "ConnectionEvent") -> None:
        """Apply current-connection facts without owning runtime phase or transport."""
        if isinstance(event, GoalEvent):
            self._goal_status = event.goal.status if event.goal is not None else None
            if self._goal_status != "active" and not (
                self._goal_preparing and self._turn_id is None
            ):
                self.stop()
                if self._turn_id is None:
                    self.finish("goal_inactive")
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
                self.stop()
            elif event.phase == "running":
                self._resume_input()
            elif event.phase in {"ready", "stopped", "error", "closed"}:
                self._turn_id = None
                if event.phase == "ready" and self.goal_active_or_preparing:
                    self._input.suspend()
                else:
                    self.finish("completed" if event.phase == "ready" else event.phase)

    async def prepare_goal(self, call: "Callable[[], Awaitable[bool]]") -> bool:
        """Hold Goal preparation policy until its public operation returns."""
        previous_grant = self._grant
        self._goal_preparing = True
        try:
            return await call()
        finally:
            self._goal_preparing = False
            if self._grant is not previous_grant and self._goal_status != "active":
                self.stop()
                if self._turn_id is None:
                    self.finish("goal_inactive")

    async def answer(self, token: str, call: "Callable[[], Awaitable[None]]") -> None:
        """Hold input through delivery; uncertainty never clears the sending token."""
        self._sending_answers.add(token)
        try:
            await call()
        except AnswerDeliveryError:
            raise
        except CodexConnectionError:
            self._sending_answers.discard(token)
            self._resume_input()
            raise
        self._sending_answers.discard(token)
        self._waiting_answers.discard(token)
        self._resume_input()

    def _resume_input(self) -> None:
        if (
            self._input_allowed
            and self._turn_running
            and not self._waiting_answers
            and not self._sending_answers
        ):
            self._input.resume()

    def finish(self, reason: str) -> None:
        """Revoke the current run and retain its one asynchronous finish operation."""
        if self._grant is None:
            return
        self._input.stop(reason)
        self._grant = None
        self._waiting_answers.clear()
        self._sending_answers.clear()
        self._finishing = asyncio.create_task(self._input.finish_run(reason))

    async def drain(self) -> None:
        """Join owned run termination before another run or resource release."""
        if self._finishing is not None:
            await asyncio.shield(self._finishing)
