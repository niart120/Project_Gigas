"""Access server-owned metadata and history independently of conversation execution."""

from typing import TYPE_CHECKING, Literal

from gigas.runtime.codex import _management_protocol as protocol
from gigas.runtime.codex.management_models import (
    ItemPage,
    ProjectInfo,
    ProjectPage,
    ThreadInfo,
    ThreadItemInfo,
    ThreadPage,
    TurnInfo,
    TurnPage,
)

if TYPE_CHECKING:
    from pathlib import Path

    from gigas.runtime.codex.connection import CodexConnection
    from gigas.runtime.codex.transport import JsonObject

__all__ = [
    "CodexManagement",
    "ItemPage",
    "ProjectInfo",
    "ProjectPage",
    "ThreadInfo",
    "ThreadItemInfo",
    "ThreadPage",
    "TurnInfo",
    "TurnPage",
]


class CodexManagement:
    """Access server-owned metadata using an already opened connection.

    This client does not own the connection, cache project locations, or prepare
    conversation execution. It neither requires a valid model or prepared sandbox
    nor starts or resumes a thread. RPC failures propagate without automatic retry.

    """

    def __init__(self, connection: "CodexConnection") -> None:
        """Bind management requests to a caller-owned connection.

        Args:
            connection: Connection opened and closed by the caller on the same event loop.
        """
        self._connection = connection

    async def create_project(self, name: str, root: "Path", *, idempotency_key: str) -> ProjectInfo:
        """Register one workspace directory with the server.

        Args:
            name: Project display name.
            root: Absolute workspace directory, selected or created by the caller.
            idempotency_key: Caller-owned key identifying this creation operation.

        Returns:
            The project reported by project/create. No local directory is created.

        Raises:
            ValueError: The supplied root is relative.
            CodexConnectionError: The connection is closed or the response is invalid
                or contains an unsupported project with multiple roots.
            TransportError: The request cannot be delivered or the server rejects it.
        """
        if not root.is_absolute():
            raise ValueError("Projectの作業フォルダーには絶対パスが必要")
        result = await self._connection.request(
            "project/create",
            {"name": name, "roots": [{"path": str(root)}], "idempotencyKey": idempotency_key},
        )
        return protocol.project_response(result)

    async def list_projects(
        self, *, cursor: str | None = None, limit: int | None = None
    ) -> ProjectPage:
        """Read one project page without fetching subsequent pages automatically.

        Args:
            cursor: Continuation token from a prior page, passed through unchanged.
            limit: Optional server page-size limit; omitted to use its default.

        Returns:
            Projects in server order and its next cursor. An absent or null cursor
            means that no next page is offered.

        Raises:
            CodexConnectionError: The connection is closed or the response is invalid
                or contains an unsupported project with multiple roots.
            TransportError: The request cannot be delivered or the server rejects it.
        """
        params: JsonObject = {}
        if cursor is not None:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = limit
        result = await self._connection.request("project/list", params)
        return protocol.project_page(result)

    async def read_project(self, project_id: str) -> ProjectInfo:
        """Read current project metadata without probing its directory.

        Args:
            project_id: Server-owned identity of the project to read.

        Returns:
            Project metadata from this request, including its absolute workspace path.

        Raises:
            CodexConnectionError: The connection is closed or the response is invalid
                or contains an unsupported project with multiple roots.
            TransportError: The request cannot be delivered or the server rejects it.
        """
        result = await self._connection.request("project/read", {"projectId": project_id})
        return protocol.project_response(result)

    async def list_threads(
        self,
        project_id: str | None,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        sort_direction: Literal["asc", "desc"] | None = None,
    ) -> ThreadPage:
        """Read one page for a Project or for unassigned threads across all providers.

        Args:
            project_id: Project identity; None explicitly selects unassigned threads.
            cursor: Opaque continuation cursor, passed through unchanged.
            limit: Optional page-size limit; omitted to use the server default.
            sort_direction: Optional direction; the server defaults to descending.

        Returns:
            One page of thread metadata and both server pagination cursors.

        Raises:
            CodexConnectionError: The connection is closed or metadata is invalid.
            TransportError: The request fails or the server rejects it.
        """
        params = _page_params(cursor, limit, sort_direction)
        params.update({"projectId": project_id, "modelProviders": []})
        result = await self._connection.request("thread/list", params)
        return protocol.thread_page(result)

    async def read_thread(self, thread_id: str) -> ThreadInfo:
        """Read saved metadata without loading turns or resuming the thread.

        Args:
            thread_id: Server-owned identity of the thread to read.

        Returns:
            Current metadata, including the saved workspace even when it is missing.

        Raises:
            CodexConnectionError: The connection is closed or metadata is invalid.
            TransportError: The request fails or the server rejects it.
        """
        result = await self._connection.request(
            "thread/read", {"threadId": thread_id, "includeTurns": False}
        )
        return protocol.thread_response(result)

    async def list_turns(
        self,
        thread_id: str,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        sort_direction: Literal["asc", "desc"] | None = None,
    ) -> TurnPage:
        """Read one turn page with full public items instead of display summaries.

        Args:
            thread_id: Server-owned identity of the thread to read.
            cursor: Opaque continuation cursor, passed through unchanged.
            limit: Optional page-size limit; omitted to use the server default.
            sort_direction: Optional direction; the server defaults to descending.

        Returns:
            One turn page with complete returned item objects and both cursors.
            Item text and tool payloads are not converted to presentation events.

        Raises:
            CodexConnectionError: The connection is closed or history is invalid.
            TransportError: The request fails or the server rejects it.
        """
        params = _page_params(cursor, limit, sort_direction)
        params.update({"threadId": thread_id, "itemsView": "full"})
        result = await self._connection.request("thread/turns/list", params)
        return protocol.turn_page(result)

    async def list_items(
        self,
        thread_id: str,
        *,
        turn_id: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
        sort_direction: Literal["asc", "desc"] | None = None,
    ) -> ItemPage:
        """Read one item page without collecting other pages or starting execution.

        Args:
            thread_id: Server-owned identity of the thread to read.
            turn_id: Optional containing turn; None reads items across the thread.
            cursor: Opaque continuation cursor, passed through unchanged.
            limit: Optional page-size limit; omitted to use the server default.
            sort_direction: Optional direction; the server defaults to ascending.

        Returns:
            Public item payloads with their containing turns, timing, and both cursors.

        Raises:
            CodexConnectionError: The connection is closed or history is invalid.
            TransportError: The request fails or the server rejects it.
        """
        params = _page_params(cursor, limit, sort_direction)
        params["threadId"] = thread_id
        if turn_id is not None:
            params["turnId"] = turn_id
        result = await self._connection.request("thread/items/list", params)
        return protocol.item_page(result)


def _page_params(
    cursor: str | None, limit: int | None, direction: Literal["asc", "desc"] | None
) -> "JsonObject":
    params: JsonObject = {}
    if cursor is not None:
        params["cursor"] = cursor
    if limit is not None:
        params["limit"] = limit
    if direction is not None:
        params["sortDirection"] = direction
    return params
