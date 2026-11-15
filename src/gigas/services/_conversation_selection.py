"""Own the selected conversation without acquiring execution resources."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING

from gigas.runtime.codex import _conversation_protocol as protocol
from gigas.runtime.codex.connection import CodexConnectionError
from gigas.services._conversation_goals import GoalTracker
from gigas.services.conversations import (
    BrowseGoalEvent,
    BrowseResult,
    ConversationTarget,
    HistoryResult,
    NewConversationTarget,
    StoredConversationTarget,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable
    from pathlib import Path

    from gigas.runtime.codex.connection import CodexConnection
    from gigas.runtime.codex.management import CodexManagement, ThreadInfo
    from gigas.runtime.codex.models import GoalState
    from gigas.runtime.codex.transport import RpcNotification


class ConversationSelection:
    """Retain a target and metadata across connection lifetimes on the service loop.

    Initial selection is serialized until the caller releases the old execution.
    History pages are returned without retention and do not reserve this slot.
    """

    def __init__(self) -> None:
        """Select a new unassigned conversation without allocating a workspace."""
        self._target: ConversationTarget = NewConversationTarget()
        self._thread: ThreadInfo | None = None
        self._goals = GoalTracker()
        self._pending: asyncio.Task[object] | None = None

    @property
    def target(self) -> ConversationTarget:
        """Current new assignment or known saved thread; not a loaded-state flag."""
        return self._target

    @property
    def thread(self) -> "ThreadInfo | None":
        """Metadata from selection, absent for a new or just-started thread."""
        return self._thread

    @property
    def goal(self) -> "GoalState | None":
        """Goal observed during browsing, without marking it locally active."""
        return self._goals.state

    @property
    def busy(self) -> bool:
        """Whether selection or a browsing mutation owns the shared operation slot."""
        return self._pending is not None

    async def new(
        self, project_id: str | None, *, release: "Callable[[], Awaitable[None]]"
    ) -> NewConversationTarget:
        """Select an unstarted assignment after the caller releases old resources.

        No public request or filesystem operation is performed. A release failure
        leaves the current selection unchanged.
        """
        async with self._selecting():
            await release()
            target = NewConversationTarget(project_id)
            self._target = target
            self._thread = None
            self._goals = GoalTracker()
            return target

    async def select(
        self,
        management: "CodexManagement",
        thread_id: str,
        *,
        release: "Callable[[], Awaitable[None]]",
    ) -> BrowseResult:
        """Read a candidate, release old execution, then adopt its metadata.

        The release callback checks current admission and connection identity
        before closing old execution resources. Fetch or release failures leave
        the selection unchanged. This method neither resumes the candidate nor
        checks whether its saved workspace exists.
        """
        async with self._selecting():
            thread = await management.read_thread(thread_id)
            goal = await management.get_goal(thread_id)
            history = await management.list_turns(thread_id)
            await release()
            self._target = StoredConversationTarget(thread.id, thread.cwd, thread.project_id)
            self._thread = thread
            self._goals = GoalTracker(goal)
            return BrowseResult(thread, goal, history)

    async def history(
        self, management: "CodexManagement", *, cursor: str | None = None
    ) -> HistoryResult:
        """Return one page for the captured target without blocking selection.

        The caller discards results whose target is not the current target instance. No page is
        cached, appended to a transcript, or used to change selection metadata.
        """
        target = self._target
        if not isinstance(target, StoredConversationTarget):
            raise CodexConnectionError("新規会話には保存済みの履歴がありません")
        page = await management.list_turns(target.thread_id, cursor=cursor)
        return HistoryResult(target, page)

    def adopt_started(self, thread_id: str, cwd: "Path", project_id: str | None) -> None:
        """Retain a validated start identity before recording is bound.

        No metadata read is needed. The caller invokes this synchronously after
        start validation, so a later bind failure or disconnect retains the ID.
        """
        self._target = StoredConversationTarget(thread_id, cwd, project_id)
        self._thread = None
        self._goals = GoalTracker()

    def refresh_project(self, project_id: str, root: "Path | None") -> None:
        """Refresh only the selected assignment after a confirmed management change.

        Args:
            project_id: Registration affected by the completed operation.
            root: Confirmed new workspace, or None when the registration was deleted.
        """
        if self._target.project_id != project_id:
            return
        assignment = project_id if root is not None else None
        if isinstance(self._target, StoredConversationTarget):
            self._target = replace(
                self._target, cwd=root or self._target.cwd, project_id=assignment
            )
            if self._thread is not None:
                self._thread = replace(
                    self._thread, cwd=root or self._thread.cwd, project_id=assignment
                )
        else:
            self._target = NewConversationTarget(assignment)

    def refresh_thread(self, thread: "ThreadInfo") -> None:
        """Adopt metadata for the same selected ID without replacing history or Goal."""
        if (
            isinstance(self._target, StoredConversationTarget)
            and self._target.thread_id == thread.id
        ):
            self._target = StoredConversationTarget(thread.id, thread.cwd, thread.project_id)
            self._thread = thread

    async def select_model(self, connection: "CodexConnection", model: str, effort: str) -> None:
        """Validate and retain catalog values without loading or updating a thread."""
        async with self._selecting():
            connection.commit_model(connection.propose_model(model, effort))

    async def clear_goal(
        self, connection: "CodexConnection", *, closing: "Callable[[], bool]"
    ) -> None:
        """Clear the browsing Goal without readiness checks or execution preparation."""
        async with self._selecting():
            target = self._target
            if not isinstance(target, StoredConversationTarget) or self.goal is None:
                raise CodexConnectionError("削除する目標がありません")
            await self._goals.change(
                connection,
                "thread/goal/clear",
                protocol.goal_params(target.thread_id),
                closing=closing,
            )

    def receive_goal(self, event: "RpcNotification") -> BrowseGoalEvent | None:
        """Apply only global Goal notifications belonging to the browsing target."""
        target = self._target
        params = event.params or {}
        if (
            not isinstance(target, StoredConversationTarget)
            or params.get("threadId") != target.thread_id
            or event.method not in {"thread/goal/updated", "thread/goal/cleared"}
        ):
            return None
        self._goals.observe(
            protocol.goal_state(params.get("goal") if event.method.endswith("updated") else None)
        )
        return BrowseGoalEvent(target, self.goal)

    def connection_closing(self) -> None:
        """Wake Goal waiters while retaining the selected target and metadata."""
        self._goals.closing()

    def retain_goal(self, thread_id: str | None, goal: "GoalState | None") -> None:
        """Take the last Goal from released execution when it owns this target."""
        if (
            isinstance(self._target, StoredConversationTarget)
            and self._target.thread_id == thread_id
        ):
            self._goals = GoalTracker(goal)

    @asynccontextmanager
    async def _selecting(self) -> "AsyncGenerator[None]":
        if self._pending is not None:
            raise CodexConnectionError("会話の選択が完了していません")
        self._pending = asyncio.current_task()
        try:
            yield
        finally:
            self._pending = None
