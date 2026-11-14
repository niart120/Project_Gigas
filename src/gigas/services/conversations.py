"""Immutable conversation selections and public history results."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from gigas.runtime.codex.management import ThreadInfo, TurnPage
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
class BrowseResult:
    """Saved metadata, Goal and first history page returned by one selection.

    Attributes:
        thread: Public metadata for the selected thread.
        goal: Saved Goal, or None. An active Goal does not mean local execution started.
        history: One complete public turn page, without fetching subsequent pages.
    """

    thread: "ThreadInfo"
    goal: "GoalState | None"
    history: "TurnPage"


@dataclass(frozen=True, slots=True)
class HistoryResult:
    """One history page tagged with the target captured when retrieval started.

    Attributes:
        target: Target instance captured for the request. Compare identity with the
            current selection; equal fields after reselection do not make a page current.
        page: Public turn page and its opaque pagination cursors.
    """

    target: StoredConversationTarget
    page: "TurnPage"
