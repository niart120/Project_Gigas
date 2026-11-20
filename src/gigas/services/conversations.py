"""Immutable conversation selections and public history results."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from gigas.services.projects import ProjectDeleted, ProjectDetails, ProjectFolderStatus

if TYPE_CHECKING:
    from pathlib import Path

    from gigas.runtime.codex.management import (
        ProjectPage,
        ThreadInfo,
        ThreadPage,
        TurnInfo,
        TurnPage,
    )
    from gigas.runtime.codex.models import GoalState


@dataclass(frozen=True, slots=True)
class NewConversationTarget:
    """Assignment for a conversation that has not started.

    Attributes:
        project_id: Selected Project, or None for a new unassigned workspace.
            This value does not allocate a directory or a server thread.
    """

    project_id: str | None = None


@dataclass(frozen=True, slots=True)
class StoredConversationTarget:
    """A known thread and its saved workspace, independent of execution readiness.

    Attributes:
        thread_id: Identity returned by a validated start or metadata response.
        cwd: Absolute workspace path from that response, without an existence check.
        project_id: Server-owned assignment, or None for an unassigned thread.
    """

    thread_id: str
    cwd: "Path"
    project_id: str | None = None


type ConversationTarget = NewConversationTarget | StoredConversationTarget


@dataclass(frozen=True, slots=True)
class HistoryCursor:
    """Continuation for independently paged item bodies and turn metadata.

    Attributes:
        thread_id: Conversation owning both server cursors.
        item_cursor: Next item page; None means item history is exhausted.
        turns: Retained metadata starting at the last displayed boundary turn.
        turn_cursor: Next metadata page; None means no older metadata remains.
    """

    thread_id: str
    item_cursor: str | None
    turns: "tuple[TurnInfo, ...]"
    turn_cursor: str | None


@dataclass(frozen=True, slots=True)
class HistoryPage:
    """Bounded item bodies combined with their ordered turn statuses.

    Attributes:
        content: Descending turns containing only this page's item bodies.
        next_cursor: Continuation, including undisplayed metadata-only turns.
    """

    content: "TurnPage"
    next_cursor: HistoryCursor | None


@dataclass(frozen=True, slots=True)
class BrowseResult:
    """Saved metadata, Goal and first history page returned by one selection.

    Attributes:
        thread: Public metadata for the selected thread.
        goal: Saved Goal, or None. An active Goal does not mean local execution started.
        history: Bounded item bodies and associated turn metadata.
    """

    thread: "ThreadInfo"
    goal: "GoalState | None"
    history: HistoryPage


@dataclass(frozen=True, slots=True)
class HistoryResult:
    """One history page tagged with the target captured when retrieval started.

    Attributes:
        target: Target instance captured for the request. Compare identity with the
            current selection; equal fields after reselection do not make a page current.
        page: Bounded item bodies, turn metadata and a combined continuation.
    """

    target: StoredConversationTarget
    page: HistoryPage


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    """Confirmed changes, including the completed portion of an interrupted batch.

    Attributes:
        threads: Archived threads with their assignments at the time of the operation.
        new_target: New assignment if the selected thread was archived.
    """

    threads: "tuple[ThreadInfo, ...]"
    new_target: NewConversationTarget | None


type ManagementOperation = Literal[
    "projects",
    "threads",
    "new",
    "select",
    "history",
    "project_inspect",
    "project_folder",
    "project_update",
    "project_delete",
    "archived",
    "archive",
    "archive_reconciled",
    "auto_archive",
    "restore",
]
type ManagementValue = (
    ProjectPage
    | ThreadPage
    | NewConversationTarget
    | BrowseResult
    | HistoryResult
    | ProjectDetails
    | ProjectDeleted
    | ProjectFolderStatus
    | ArchiveResult
    | ThreadInfo
)


@dataclass(frozen=True, slots=True)
class ManagementResult:
    """One management result, separate from execution and message acceptance.

    Attributes:
        generation: Connection generation that received the request.
        operation: Listing, selection or history operation.
        value: Public page or selection on success; None on failure.
        error: Display-safe failure reason, empty on success.
        project_id: Requested list or new-conversation assignment; None means unassigned.
    """

    generation: int
    operation: ManagementOperation
    value: ManagementValue | None = None
    error: str = ""
    project_id: str | None = None


@dataclass(frozen=True, slots=True)
class BrowseGoalEvent:
    """A confirmed Goal notification for the captured browsing selection.

    Attributes:
        target: Selection instance that owns the notification.
        goal: Public Goal, or None after removal. This is not local execution state.
    """

    target: StoredConversationTarget
    goal: "GoalState | None"
