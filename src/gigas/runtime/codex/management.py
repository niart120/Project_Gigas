"""Read and manage server-owned projects independently of conversation execution."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from gigas.runtime.codex.connection import CodexConnectionError

if TYPE_CHECKING:
    from gigas.runtime.codex.connection import CodexConnection
    from gigas.runtime.codex.transport import JsonObject, JsonValue


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
    root: Path


@dataclass(frozen=True, slots=True)
class ProjectPage:
    """One page of projects returned by the server.

    Attributes:
        projects: Projects in the server's order.
        next_cursor: Opaque continuation token, or None when no next page is offered.
    """

    projects: tuple[ProjectInfo, ...]
    next_cursor: str | None


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

    async def create_project(self, name: str, root: Path, *, idempotency_key: str) -> ProjectInfo:
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
        return _project(_object(result).get("project"))

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
        result = _object(await self._connection.request("project/list", params))
        data = result.get("data")
        if not isinstance(data, list):
            raise CodexConnectionError("CodexのProject一覧の応答形式が不正")
        next_cursor = result.get("nextCursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise CodexConnectionError("CodexのProject一覧のcursorが不正")
        return ProjectPage(tuple(_project(value) for value in data), next_cursor)

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
        return _project(_object(result).get("project"))


def _object(value: "JsonValue") -> "JsonObject":
    if isinstance(value, dict):
        return value
    raise CodexConnectionError("CodexのProject応答形式が不正")


def _text(value: "JsonValue") -> str:
    if isinstance(value, str):
        return value
    raise CodexConnectionError("CodexのProject応答形式が不正")


def _project(value: "JsonValue") -> ProjectInfo:
    project = _object(value)
    roots = project.get("roots")
    if not isinstance(roots, list) or len(roots) != 1:
        raise CodexConnectionError("CodexのProjectには作業フォルダーが1件必要")
    root = Path(_text(_object(roots[0]).get("path")))
    if not root.is_absolute():
        raise CodexConnectionError("CodexのProjectの作業フォルダーが絶対パスではない")
    return ProjectInfo(_text(project.get("id")), _text(project.get("name")), root)
