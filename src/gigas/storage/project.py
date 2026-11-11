"""Single-owner project storage for recorded observations."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Self
from uuid import uuid4


class ProjectError(ValueError):
    """A project could not be created, read, or written without losing data."""


class ProjectWriter:
    """An explicitly closed owner of one project's observation records.

    Construct through create() or open(), not by calling the initializer.
    """

    def __init__(self, path: Path, project_id: str) -> None:
        """Initialize the owner after its project has been validated."""
        self.path = path
        self.project_id = project_id
        self._closed = False

    @classmethod
    def create(cls, path: Path) -> Self:
        """Create a project in a directory that does not yet exist.

        Args:
            path: Dedicated project directory; its parent must exist.

        Returns:
            An owner that must be explicitly closed after use.

        Raises:
            ProjectError: Creation failed. Existing and partial data are preserved.
        """
        path = path.resolve()
        project_id = f"project-{uuid4()}"
        try:
            path.mkdir()
            (path / "observations").mkdir()
            with (path / "events.jsonl").open("xb"):
                pass
            metadata = {
                "schema_version": 1,
                "project_id": project_id,
                "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
            with (path / "project.json").open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(metadata, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            message = f"{path}: プロジェクト作成失敗。作成途中の資材は保持します: {error}"
            raise ProjectError(message) from error
        return cls(path, project_id)

    def close(self) -> None:
        """Release this owner after its recording operation has finished."""
        self._closed = True
