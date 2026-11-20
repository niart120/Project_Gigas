"""Read archive candidates without loading conversations or changing server pages."""

from typing import TYPE_CHECKING

from gigas.runtime.codex.connection import CodexConnectionError

if TYPE_CHECKING:
    from gigas.runtime.codex.management import CodexManagement, ThreadInfo


def eligible(thread: "ThreadInfo", cutoff: float, selected_id: str | None) -> bool:
    """Return whether the observed metadata permits an automatic archive."""
    return (
        thread.id != selected_id
        and thread.updated_at < cutoff
        and thread.is_read is True
        and thread.status.get("type") in {"idle", "notLoaded"}
    )


async def expired_threads(
    management: "CodexManagement", cutoff: float, selected_id: str | None
) -> list[tuple[str, str | None]]:
    """Collect eligible IDs oldest first, before mutations change pagination."""
    targets: dict[str, str | None] = {}
    cursor = None
    seen: set[str] = set()
    while True:
        page = await management.list_all_threads(
            archived=False, cursor=cursor, sort_direction="asc"
        )
        for thread in page.threads:
            if thread.updated_at >= cutoff:
                return list(targets.items())
            if eligible(thread, cutoff, selected_id):
                targets[thread.id] = thread.project_id
        cursor = page.next_cursor
        if cursor is None:
            return list(targets.items())
        if cursor in seen:
            raise CodexConnectionError("自動アーカイブの対象一覧の続きを取得できません")
        seen.add(cursor)


async def find_archived(management: "CodexManagement", thread_id: str) -> "ThreadInfo | None":
    """Reconcile a lost response even if the project assignment has since changed."""
    cursor = None
    seen: set[str] = set()
    while True:
        page = await management.list_all_threads(archived=True, cursor=cursor)
        for row in page.threads:
            if row.id == thread_id:
                return row
        cursor = page.next_cursor
        if cursor is None:
            return None
        if cursor in seen:
            raise CodexConnectionError("アーカイブ結果を確認できません")
        seen.add(cursor)
