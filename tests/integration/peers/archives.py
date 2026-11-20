"""Persistent archive fixture for service and GUI integration tests."""

import asyncio
import time
from pathlib import Path

from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcError, TransportError
from tests.integration.test_project_management import ProjectPeer


class ArchivePeer(ProjectPeer):
    """Persist membership and revision changes across paginated management calls."""

    def __init__(self, root: Path, *, count: int = 7, page_size: int = 2) -> None:
        """Initialize deterministic read states and independent failure controls."""
        super().__init__(root, count=count)
        self.page_size = page_size
        self.read: dict[str, JsonValue] = {
            tid: {"firstUnread": None, "revision": tid} for tid in self.members
        }
        self.recent_on_read: str | None = None
        self.reject_archive: str | None = None
        self.lose_archive_response: str | None = None
        self.fail_reconciliation = False

    async def start(self) -> None:
        """Open a fresh notification stream when reconnecting to the same catalog."""
        if self.closed:
            self.events = asyncio.Queue()
            self.closed = False
        await super().start()

    async def request(self, method: str, params: JsonObject | None) -> JsonValue:
        """Apply archive operations while retaining membership across requests."""
        if params is None:
            return await super().request(method, params)
        tid = str(params.get("threadId"))
        if method == "thread/list":
            self.calls.append((method, params))
            if params.get("archived") and self.fail_reconciliation:
                message = "connection lost"
                raise TransportError(message)
            rows = sorted(
                (
                    member
                    for key, member in self.members.items()
                    if (key in self.archived) == params.get("archived", False)
                    and ("projectId" not in params or member["projectId"] == params["projectId"])
                ),
                key=lambda member: int(str(member["updatedAt"])),
                reverse=params.get("sortDirection") != "asc",
            )
            start = int(str(params.get("cursor", 0)))
            limit = min(self.page_size, int(str(params.get("limit", self.page_size))))
            page = rows[start : start + limit]
            return {
                "data": list(page),
                "readStates": {str(m["id"]): self.read.get(str(m["id"])) for m in page},
                "nextCursor": str(start + limit) if start + limit < len(rows) else None,
            }
        if method == "thread/read":
            self.calls.append((method, params))
            if self.recent_on_read == tid:
                self.members[tid]["updatedAt"] = int(time.time())
            return {"thread": dict(self.members[tid]), "readState": self.read.get(tid)}
        if method == "thread/archive":
            self.calls.append((method, params))
            if tid == self.reject_archive:
                raise RpcError(-32600, method)
            self.archived.add(tid)
            if tid == self.lose_archive_response:
                message = "archive response lost"
                raise TransportError(message)
            return {}
        if method == "thread/unarchive":
            self.calls.append((method, params))
            self.archived.remove(tid)
            self.members[tid]["updatedAt"] = int(time.time())
            return {"thread": dict(self.members[tid])}
        if method == "thread/readState/update":
            self.calls.append((method, params))
            self.read[tid] = {"firstUnread": None, "revision": "read"}
            return {"readState": self.read[tid]}
        return await super().request(method, params)
