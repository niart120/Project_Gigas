"""Public values returned by Codex project and history management."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path

    from gigas.runtime.codex.transport import JsonObject


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    """Project identity and its single workspace directory.

    Attributes:
        id: Server-owned project identity.
        name: Project display name.
        root: Absolute workspace path reported by the server; existence is not checked.
    """

    id: str
    name: str
    root: "Path"


@dataclass(frozen=True, slots=True)
class ProjectPage:
    """One page of projects returned by the server.

    Attributes:
        projects: Projects in the server's order.
        next_cursor: Opaque continuation token, or None when no next page is offered.
    """

    projects: tuple[ProjectInfo, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ThreadInfo:
    """Stored thread metadata, independent of any selected execution.

    Attributes:
        id: Server-owned thread identity.
        name: Optional title; no display fallback is applied.
        preview: Server-provided preview, without truncation by this client.
        cwd: Saved absolute workspace path, without an existence check.
        project_id: Server-owned assignment, or None for an unassigned thread.
        created_at: Creation time in Unix seconds.
        updated_at: Last update time in Unix seconds.
        model: Stored or loaded model, or None when unavailable.
        reasoning_effort: Stored or loaded effort, or None when unavailable.
        status: Complete public runtime-status object; not adopted as local execution state.
        is_read: Confirmed read state, or None when the server did not supply one.
        unread_revision: Opaque revision for marking this unread snapshot as read.
    """

    id: str
    name: str | None
    preview: str
    cwd: "Path"
    project_id: str | None
    created_at: int
    updated_at: int
    model: str | None
    reasoning_effort: str | None
    status: "JsonObject"
    is_read: bool | None = None
    unread_revision: str | None = None


@dataclass(frozen=True, slots=True)
class ThreadPage:
    """One server-ordered thread page.

    Attributes:
        threads: Metadata for each returned thread.
        next_cursor: Opaque continuation cursor, or None.
        backwards_cursor: Opaque cursor for reversing the requested sort direction, or None.
    """

    threads: tuple[ThreadInfo, ...]
    next_cursor: str | None
    backwards_cursor: str | None

    @property
    def read_ids(self) -> frozenset[str]:
        """Return the IDs the server explicitly reported as read."""
        return frozenset(thread.id for thread in self.threads if thread.is_read is True)


@dataclass(frozen=True, slots=True)
class TurnInfo:
    """A saved turn and its public item payloads.

    Attributes:
        id: Server-owned turn identity.
        status: Public turn status.
        items: Complete returned item objects, including text and tool-specific data.
        items_view: Server-reported completeness of items.
        error: Complete public error object, or None.
        started_at: Start time in Unix seconds, or None when unavailable.
        completed_at: Completion time in Unix seconds, or None when unavailable.
        duration_ms: Duration in milliseconds, or None when unavailable.
        root_turn_id: Initial turn in this work chain, or None when unavailable.
    """

    id: str
    status: str
    items: tuple["JsonObject", ...]
    items_view: Literal["notLoaded", "summary", "full"]
    error: "JsonObject | None"
    started_at: int | None
    completed_at: int | None
    duration_ms: int | None
    root_turn_id: str | None


@dataclass(frozen=True, slots=True)
class TurnPage:
    """One server-ordered turn page.

    Attributes:
        turns: Saved turns and the items returned for them.
        next_cursor: Opaque continuation cursor, or None.
        backwards_cursor: Opaque cursor for reversing the requested sort direction, or None.
    """

    turns: tuple[TurnInfo, ...]
    next_cursor: str | None
    backwards_cursor: str | None


@dataclass(frozen=True, slots=True)
class ThreadItemInfo:
    """A public history item with its containing turn and recorded timing.

    Attributes:
        turn_id: Identity of the turn containing the item.
        item: Complete public item object, including its id, type, and variant fields.
        started_at_ms: Start time in Unix milliseconds, or None when unavailable.
        completed_at_ms: Completion time in Unix milliseconds, or None when unavailable.
    """

    turn_id: str
    item: "JsonObject"
    started_at_ms: int | None
    completed_at_ms: int | None


@dataclass(frozen=True, slots=True)
class ItemPage:
    """One server-ordered item page.

    Attributes:
        items: Public items with their turn identities and timing.
        next_cursor: Opaque continuation cursor, or None.
        backwards_cursor: Opaque cursor for reversing the requested sort direction, or None.
    """

    items: tuple[ThreadItemInfo, ...]
    next_cursor: str | None
    backwards_cursor: str | None
