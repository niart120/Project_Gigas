"""Coordinate project changes through public APIs without starting a turn."""

import asyncio
import json
import stat
from typing import TYPE_CHECKING, Literal

from gigas.runtime.codex._execution_settings import config_overrides
from gigas.runtime.codex.connection import CodexConnectionError
from gigas.runtime.codex.management import CodexManagement
from gigas.runtime.codex.transport import RpcError, TransportError
from gigas.services._workspaces import _require_directory
from gigas.services.projects import ProjectDeleted, ProjectDetails

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from gigas.runtime.codex.connection import CodexConnection
    from gigas.runtime.codex.management import ProjectInfo, ThreadInfo
    from gigas.runtime.codex.models import ExecutionSettings
    from gigas.runtime.codex.transport import JsonObject


class ProjectUpdateError(CodexConnectionError):
    """A submitted project mutation may have left partial changes or loaded threads."""


class ProjectManagement:
    """Own one management operation; connection and execution resources are borrowed.

    Args:
        connection: Open connection on the caller's loop.
        check: Recheck connection identity and operation admission after awaits.
        release: Close the caller's idle execution resources before a mutation.
        settings: Current product execution boundaries used for management loading.
    """

    def __init__(
        self,
        connection: "CodexConnection",
        *,
        check: "Callable[[], None]",
        release: "Callable[[], Awaitable[None]]",
        settings: "ExecutionSettings",
    ) -> None:
        """Retain borrowed dependencies without fetching or changing metadata."""
        self.connection = connection
        self.management = CodexManagement(connection)
        self.check = check
        self.release = release
        self.settings = settings

    async def _threads(self, project_id: str, *, archived: bool) -> "tuple[ThreadInfo, ...]":
        rows = {}
        cursor = None
        cursors: set[str] = set()
        while True:
            page = await self.management.list_threads(project_id, cursor=cursor, archived=archived)
            self.check()
            for thread in page.threads:
                if thread.project_id != project_id:
                    raise CodexConnectionError(
                        "取得中にチャットの所属が変わりました。再取得してください"
                    )
                rows[thread.id] = thread
            cursor = page.next_cursor
            if cursor is None:
                return tuple(rows.values())
            if cursor in cursors:
                raise CodexConnectionError("チャット一覧の続きの取得を確認できません")
            cursors.add(cursor)

    async def inspect(self, project_id: str) -> ProjectDetails:
        """Read complete membership and folder availability without loading threads."""
        project = await self.management.read_project(project_id)
        self.check()
        threads = await self._threads(project_id, archived=False)
        archived = await self._threads(project_id, archived=True)
        status = await asyncio.to_thread(directory_status, project.root)
        self.check()
        return ProjectDetails(project, threads, archived, status)

    async def _require_idle(self, thread: "ThreadInfo") -> None:
        current = await self.management.read_thread(thread.id)
        self.check()
        if current.project_id != thread.project_id or current.cwd != thread.cwd:
            raise CodexConnectionError("チャットの所属・作業場所が変わりました。再取得してください")
        goal = await self.management.get_goal(thread.id)
        self.check()
        if current.status.get("type") not in {"idle", "notLoaded"} or (
            goal is not None and goal.status == "active"
        ):
            message = (
                f"チャット {thread.name or thread.id} を停止してからフォルダーを変更してください"
            )
            raise CodexConnectionError(message)

    async def update(self, expected: "ProjectInfo", *, name: str, root: "Path") -> ProjectDetails:
        """Save a registration and move idle members forward, retaining partial changes."""
        name = name.strip()
        if not name:
            raise CodexConnectionError("プロジェクトの表示名を入力してください")
        if not root.is_absolute():
            raise CodexConnectionError("変更先フォルダーには絶対パスが必要です")
        details = await self.inspect(expected.id)
        if details.project != expected:
            raise CodexConnectionError(
                "プロジェクト情報が変わりました。編集画面を開き直してください"
            )
        rename_only = (
            root == expected.root
            and name != expected.name
            and (not details.pending or bool(details.archived))
        )
        if rename_only:
            await self.management.update_project(expected.id, name=name)
            self.check()
            renamed = await self.inspect(expected.id)
            if renamed.project.name != name or renamed.project.root != expected.root:
                raise CodexConnectionError("表示名の保存結果を確認できません。再取得してください")
            return renamed
        changing = root != expected.root or bool(details.pending)
        if not changing:
            return details
        if details.archived:
            raise CodexConnectionError(
                "アーカイブ済みチャットを含むプロジェクトのフォルダーは変更できません。"
                "表示名の変更とプロジェクトの削除は可能です"
            )
        if details.pending and root != expected.root:
            raise CodexConnectionError(
                "フォルダー更新が未完了です。現在の登録フォルダーで保存を再試行してください"
            )
        try:
            await asyncio.to_thread(_require_directory, root)
        except OSError as error:
            message = f"変更先フォルダーにアクセスできません。所在と権限を確認してください: {root}"
            raise CodexConnectionError(message) from error
        self.check()
        for thread in details.threads:
            await self._require_idle(thread)
        await self.release()
        self.check()
        confirmed = 0
        try:
            if root != expected.root or name != expected.name:
                await self.management.update_project(
                    expected.id, name=name, root=root if root != expected.root else None
                )
                self.check()
            for thread in details.threads:
                if thread.cwd != root:
                    await self._require_idle(thread)
                    await self._relocate(thread, root)
                confirmed += 1
            final = await self.inspect(expected.id)
            if (
                final.project.root != root
                or final.project.name != name
                or final.pending
                or final.archived
                or {thread.id for thread in final.threads}
                != {thread.id for thread in details.threads}
            ):
                message = "全チャットの作業場所を確認できません"
                raise CodexConnectionError(message)  # noqa: TRY301 -- wrap as partial update below.
        except Exception as error:
            unconfirmed = (
                ", ".join(thread.id for thread in details.threads[confirmed:]) or "最終確認"
            )
            if isinstance(error, CodexConnectionError):
                reason = str(error)
            elif isinstance(error, RpcError):
                reason = f"Codexが{error.method}を拒否しました (code {error.code})"
            else:
                reason = "通信または応答の確認に失敗しました"
            message = (
                f"フォルダー更新の完了を確認できません (チャット確認済み {confirmed}件)。"
                f"原因: {reason}。"
                "更新済みの登録・チャットは保持しています。接続を確認し、編集画面を開き直して"
                f"同じフォルダーで保存を再試行してください。未確認の対象: {unconfirmed}"
            )
            raise ProjectUpdateError(message) from error
        return final

    async def _unsubscribe(self, thread_id: str) -> None:
        result = await self.connection.request("thread/unsubscribe", {"threadId": thread_id})
        self.check()
        if not isinstance(result, dict) or result.get("status") not in {
            "unsubscribed",
            "notSubscribed",
            "notLoaded",
        }:
            raise CodexConnectionError("チャットの購読解除を確認できません")

    async def _relocate(self, thread: "ThreadInfo", root: "Path") -> None:
        # Unsubscribe first: resume joins a loaded session and ignores overrides.
        await self._unsubscribe(thread.id)
        config = await self.connection.request(
            "config/read", {"includeLayers": False, "cwd": str(root)}
        )
        self.check()
        overrides: JsonObject = config_overrides(self.settings)
        # Management loads do not execute tools and must not require Windows write setup.
        # Normal preparation validates workspace-write on explicit execution.
        overrides["sandbox_mode"] = "read-only"
        configuration = config.get("config") if isinstance(config, dict) else None
        if not isinstance(configuration, dict):
            raise CodexConnectionError("Codexの実行設定を取得できません")
        servers = configuration.get("mcp_servers", {})
        if not isinstance(servers, dict):
            raise CodexConnectionError("CodexのMCP設定を確認できません")
        for server in servers:
            overrides[f"mcp_servers.{json.dumps(server)}.enabled"] = False
        result = await self.connection.request(
            "thread/resume",
            {
                "threadId": thread.id,
                "cwd": str(root),
                "runtimeWorkspaceRoots": [str(root)],
                "sandbox": "read-only",
                "approvalPolicy": "on-request",
                "approvalsReviewer": self.settings.approvals_reviewer,
                "config": overrides,
                "excludeTurns": True,
            },
        )
        self.check()
        if not isinstance(result, dict):
            raise CodexConnectionError("チャットの作業場所変更の応答が不正")
        metadata = result.get("thread")
        sandbox = result.get("sandbox")
        if (
            not isinstance(metadata, dict)
            or metadata.get("id") != thread.id
            or metadata.get("projectId") != thread.project_id
            or result.get("cwd") != str(root)
            or result.get("runtimeWorkspaceRoots") != [str(root)]
            or not isinstance(sandbox, dict)
            or sandbox.get("type") != "readOnly"
            or sandbox.get("networkAccess") is not False
        ):
            raise CodexConnectionError("変更後の作業場所・書込み範囲を確認できません")
        await self._unsubscribe(thread.id)

    async def delete(self, expected: "ProjectInfo") -> ProjectDeleted:
        """Remove one registration after resource recovery, without resuming members."""
        try:
            current = await self.management.read_project(expected.id)
        except RpcError as error:
            if error.code != -32602:
                raise
            confirmed = await self._confirm_absent(expected.id)
            await self.release()
            self.check()
            return confirmed
        self.check()
        if current != expected:
            raise CodexConnectionError("プロジェクト情報が変わりました。削除対象を確認してください")
        await self.release()
        self.check()
        try:
            await self.management.delete_project(expected.id)
        except RpcError:
            raise
        except (TransportError, CodexConnectionError):
            # A response can be lost after deletion. Re-read; never repeat the mutation.
            return await self._confirm_absent(expected.id)
        self.check()
        return await self._confirm_absent(expected.id)

    async def _confirm_absent(self, project_id: str) -> ProjectDeleted:
        cursor = None
        cursors: set[str] = set()
        while True:
            page = await self.management.list_projects(cursor=cursor)
            self.check()
            if any(project.id == project_id for project in page.projects):
                raise CodexConnectionError("プロジェクトの削除を確認できません。再取得してください")
            cursor = page.next_cursor
            if cursor is None:
                return ProjectDeleted(project_id)
            if cursor in cursors:
                raise CodexConnectionError("プロジェクト一覧を確認できません")
            cursors.add(cursor)


def directory_status(path: "Path") -> Literal["available", "missing", "unavailable"]:
    """Return folder availability, distinguishing absence from failed inspection."""
    try:
        return "available" if stat.S_ISDIR(path.stat().st_mode) else "missing"
    except (FileNotFoundError, NotADirectoryError):
        return "missing"
    except OSError:
        return "unavailable"
