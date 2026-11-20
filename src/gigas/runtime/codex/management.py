"""Access server-owned metadata and history independently of conversation execution."""

from typing import TYPE_CHECKING, Literal

from gigas.runtime.codex import _management_protocol as protocol
from gigas.runtime.codex._conversation_protocol import goal_clear_result, goal_params
from gigas.runtime.codex.connection import CodexConnectionError
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
    from gigas.runtime.codex.models import GoalState
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

    async def update_project(
        self, project_id: str, *, name: str, root: "Path | None" = None
    ) -> ProjectInfo:
        """Update a registration without touching files or conversation execution.

        Args:
            project_id: Server-owned identity of the registration to edit.
            name: Nonempty display name; surrounding whitespace is removed.
            root: Optional absolute replacement directory. None leaves roots unchanged.

        Returns:
            Project metadata reported by the server after this request.

        Raises:
            ValueError: The name is empty or the replacement path is relative.
            CodexConnectionError: The response cannot be decoded.
            TransportError: Delivery fails or the server rejects the update. No retry occurs.
        """
        name = name.strip()
        if not name:
            raise ValueError("プロジェクトの表示名を入力してください")
        if root is not None and not root.is_absolute():
            raise ValueError("Projectの作業フォルダーには絶対パスが必要")
        params: JsonObject = {"projectId": project_id, "name": name}
        if root is not None:
            params["roots"] = [{"path": str(root)}]
        result = await self._connection.request("project/update", params)
        return protocol.project_response(result)

    async def delete_project(self, project_id: str) -> None:
        """Delete one registration, retaining threads and workspace files.

        Args:
            project_id: Server-owned identity to delete.

        Raises:
            CodexConnectionError: The reply is not an object.
            TransportError: Delivery fails or the server rejects deletion. No retry occurs.
        """
        result = await self._connection.request("project/delete", {"projectId": project_id})
        if not isinstance(result, dict):
            raise CodexConnectionError("プロジェクト削除の応答形式が不正")

    async def list_threads(
        self,
        project_id: str | None,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        sort_direction: Literal["asc", "desc"] | None = None,
        sort_key: Literal["updated_at"] | None = None,
        archived: bool | None = None,
    ) -> ThreadPage:
        """Read one page for a Project or for unassigned threads across all providers.

        Args:
            project_id: Project identity; None explicitly selects unassigned threads.
            cursor: Opaque continuation cursor, passed through unchanged.
            limit: Optional page-size limit; omitted to use the server default.
            sort_direction: Optional direction; the server defaults to descending.
            sort_key: Optional ordering by the last update timestamp.
            archived: True selects archived threads. None preserves the default listing.

        Returns:
            One page of thread metadata and both server pagination cursors.

        Raises:
            CodexConnectionError: The connection is closed or metadata is invalid.
            TransportError: The request fails or the server rejects it.
        """
        params = _page_params(cursor, limit, sort_direction)
        params.update({"projectId": project_id, "modelProviders": []})
        if sort_key is not None:
            params["sortKey"] = sort_key
        if archived is not None:
            params["archived"] = archived
        result = await self._connection.request("thread/list", params)
        return protocol.thread_page(result)

    async def list_all_threads(
        self,
        *,
        archived: bool,
        cursor: str | None = None,
        limit: int = 50,
        sort_direction: Literal["asc", "desc"] = "desc",
    ) -> ThreadPage:
        """Read a page across projects, including unassigned threads.

        Args:
            archived: Select archived or normal threads.
            cursor: Continuation from the preceding page of the same list.
            limit: Maximum number of threads in one response.
            sort_direction: Order by last update time.

        Returns:
            Metadata and read states from the server's index.
        """
        params = _page_params(cursor, limit, sort_direction)
        params.update(
            {
                "archived": archived,
                "modelProviders": [],
                "useStateDbOnly": True,
                "sortKey": "updated_at",
            }
        )
        return protocol.thread_page(await self._connection.request("thread/list", params))

    async def archive_thread(self, thread_id: str) -> None:
        """Archive a saved thread without changing its project assignment.

        Args:
            thread_id: Identity of the chat to archive.
        """
        result = await self._connection.request("thread/archive", {"threadId": thread_id})
        if not isinstance(result, dict):
            raise CodexConnectionError("Codexのチャットのアーカイブ応答が不正")

    async def unarchive_thread(self, thread_id: str) -> ThreadInfo:
        """Restore a saved thread and return its current workspace and assignment.

        Args:
            thread_id: Identity of the archived chat.

        Returns:
            Metadata of the restored thread.
        """
        result = await self._connection.request("thread/unarchive", {"threadId": thread_id})
        thread = protocol.thread_response(result)
        if thread.id != thread_id:
            raise CodexConnectionError("復元したチャットのIDを確認できません")
        return thread

    async def mark_read(self, thread_id: str, revision: str) -> None:
        """Mark the viewed revision as read. A conflicting update is not retried.

        Args:
            thread_id: Identity of the chat whose history was displayed.
            revision: Opaque revision observed before the history was displayed.
        """
        result = await self._connection.request(
            "thread/readState/update",
            {"threadId": thread_id, "expectedRevision": revision, "operation": {"type": "read"}},
        )
        state = result.get("readState") if isinstance(result, dict) else None
        if (
            not isinstance(state, dict)
            or "firstUnread" not in state
            or state["firstUnread"] is not None
        ):
            raise CodexConnectionError("チャットの既読状態を確認できません")

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
        items_view: Literal["notLoaded", "summary", "full"] = "full",
    ) -> TurnPage:
        """Read one turn page with the requested item detail.

        Args:
            thread_id: Server-owned identity of the thread to read.
            cursor: Opaque continuation cursor, passed through unchanged.
            limit: Optional page-size limit; omitted to use the server default.
            sort_direction: Optional direction; the server defaults to descending.
            items_view: Item detail; notLoaded retrieves only turn metadata.

        Returns:
            One turn page and both cursors. Full returns complete item objects;
            notLoaded returns metadata with empty items. Item text and tool
            payloads are not converted to presentation events.

        Raises:
            CodexConnectionError: The connection is closed or history is invalid.
            TransportError: The request fails or the server rejects it.
        """
        params = _page_params(cursor, limit, sort_direction)
        params.update({"threadId": thread_id, "itemsView": items_view})
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

    async def get_goal(self, thread_id: str) -> "GoalState | None":
        """Read a saved thread's Goal without preparing conversation execution.

        Args:
            thread_id: Server-owned identity of the thread whose Goal is requested.

        Returns:
            The existing GoalState value, or None when no Goal is reported. Usage,
            budget and status are returned as reported without inferred changes.

        Raises:
            CodexConnectionError: The connection is closed or the response is invalid.
            TransportError: The request fails or the server rejects it.
        """
        result = await self._connection.request("thread/goal/get", {"threadId": thread_id})
        return protocol.goal_response(result)

    async def clear_goal(self, thread_id: str) -> bool:
        """Delete a saved Goal for an explicit user action without stopping execution.

        This request does not resume a thread, prepare resources, read its workspace,
        or wait for a notification. It does not require a prior get_goal call.

        Args:
            thread_id: Server-owned identity of the thread whose Goal is to be deleted.

        Returns:
            True if the server deleted a Goal, or False if no deletion occurred.
            This result does not promise that a turn or device input has stopped.

        Raises:
            CodexConnectionError: The connection is closed or the response is invalid.
            TransportError: The request fails or the server rejects it.
        """
        result = await self._connection.request("thread/goal/clear", goal_params(thread_id))
        return goal_clear_result(result)


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
