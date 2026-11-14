"""Resolve server-owned Projects and caller-owned new conversation directories."""

import asyncio
import stat
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from pathlib import Path

    from gigas.runtime.codex.management import CodexManagement, ProjectInfo


async def select_project(
    management: "CodexManagement", path: "Path", *, create: bool
) -> "ProjectInfo":
    """Register a new directory or reuse the first Project with an existing root.

    Directory creation is exclusive. Opening an existing directory follows the
    server's pages only until a matching root is found. Failed or cancelled
    registration leaves the directory intact; errors propagate without retry.
    The caller adopts the returned identity and owns selection state.
    """
    root = await asyncio.to_thread(_project_directory, path, create=create)
    if not create:
        cursor = None
        while True:
            page = await management.list_projects(cursor=cursor)
            for project in page.projects:
                if project.root == root:
                    return project
            cursor = page.next_cursor
            if cursor is None:
                break
    return await management.create_project(
        root.name or str(root), root, idempotency_key=str(uuid4())
    )


async def resolve_workspace(
    management: "CodexManagement", data_root: "Path", *, project_id: str | None
) -> "Path":
    """Read a Project's current root or create one new unassigned workspace.

    Existing roots must be directories and are never recreated. Each unassigned
    call creates a separate directory under data_root/unassigned; the caller
    retains the returned path across preparation retries. No recording files or
    local Project metadata are created.
    """
    if project_id is None:
        return await asyncio.to_thread(_unassigned_directory, data_root)
    project = await management.read_project(project_id)
    await asyncio.to_thread(_require_directory, project.root)
    return project.root


def _project_directory(path: "Path", *, create: bool) -> "Path":
    root = path.resolve()
    if create:
        root.mkdir()
    else:
        _require_directory(root)
    return root


def _require_directory(path: "Path") -> None:
    if not stat.S_ISDIR(path.stat().st_mode):
        message = f"作業場所がフォルダーではありません: {path}"
        raise NotADirectoryError(message)


def _unassigned_directory(data_root: "Path") -> "Path":
    parent = data_root.resolve() / "unassigned"
    parent.mkdir(parents=True, exist_ok=True)
    root = parent / str(uuid4())
    root.mkdir()
    return root
