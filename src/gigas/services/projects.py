"""Public project details derived from Codex metadata and a directory probe."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from gigas.runtime.codex.management import ProjectInfo, ThreadInfo


@dataclass(frozen=True, slots=True)
class ProjectDetails:
    """A complete project inspection, without an independent persistent catalog.

    Attributes:
        project: Current server registration.
        threads: All non-archived member threads.
        archived: All archived member threads.
        directory_status: Result of checking the registered folder.
    """

    project: "ProjectInfo"
    threads: "tuple[ThreadInfo, ...]"
    archived: "tuple[ThreadInfo, ...]"
    directory_status: Literal["available", "missing", "unavailable"]

    @property
    def pending(self) -> "tuple[ThreadInfo, ...]":
        """Threads whose saved workspace differs from the registration."""
        return tuple(thread for thread in self.threads if thread.cwd != self.project.root)


@dataclass(frozen=True, slots=True)
class ProjectDeleted:
    """Evidence that a complete project listing no longer contains an identity.

    Attributes:
        project_id: Deleted registration, independent of its name or folder.
    """

    project_id: str


@dataclass(frozen=True, slots=True)
class ProjectFolderStatus:
    """Folder probe for a particular registration snapshot, without fetching members.

    Attributes:
        project: Snapshot whose folder was checked.
        status: Folder availability; an access failure is distinct from absence.
    """

    project: "ProjectInfo"
    status: Literal["available", "missing", "unavailable"]
