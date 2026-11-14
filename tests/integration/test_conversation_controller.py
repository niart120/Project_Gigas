import asyncio
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.runtime.codex.connection import CodexConnection
from gigas.runtime.codex.management_models import ProjectInfo, ProjectPage, TurnPage
from gigas.runtime.codex.models import ConnectionOptions, GoalState
from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcNotification
from gigas.services.codex import CodexServiceThread, ProjectResult
from gigas.services.conversations import (
    BrowseGoalEvent,
    HistoryResult,
    ManagementResult,
    StoredConversationTarget,
)
from tests.integration.peers.controlled_capture import ControlledCapture
from tests.integration.test_project_gui import ProjectsPeer, close, connect
from tests.unit.test_codex_connection import FakePeer
from tests.unit.test_codex_history import _thread

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.runtime.codex.connection import RuntimeEvent


class ConversationPeer(ProjectsPeer):
    """Serve independent public lists and saved history without running a model."""

    def __init__(self, root: Path) -> None:
        """Prepare one Project and one saved conversation in each assignment."""
        super().__init__()
        self.projects = [{"id": "project-1", "name": "Project", "roots": [{"path": str(root)}]}]
        self.threads = {
            "saved": {**_thread(root, "project-1"), "id": "saved", "name": "保存会話"},
            "unassigned": {**_thread(root), "id": "unassigned", "name": "未所属会話"},
        }
        self.history_entered = Event()
        self.history_release: Event | None = None
        self.thread_list_entered = Event()
        self.thread_list_release: Event | None = None
        self.project_pages: dict[str | None, JsonValue] = {}

    async def request(self, method: str, params: JsonObject | None) -> JsonValue:
        """Return the requested page with an opaque history continuation."""
        if method == "project/list" and self.project_pages:
            cursor = str(params["cursor"]) if params and "cursor" in params else None
            self.responses[method] = self.project_pages[cursor]
            return await FakePeer.request(self, method, params)
        if method == "thread/list":
            assert params is not None
            rows: list[JsonValue] = [
                t for t in self.threads.values() if t["projectId"] == params["projectId"]
            ]
            if self.thread_list_release is not None:
                release, self.thread_list_release = self.thread_list_release, None
                self.thread_list_entered.set()
                await asyncio.to_thread(release.wait, 5)
            self.responses[method] = {
                "data": rows,
                "nextCursor": None,
            }
        elif method == "thread/read":
            assert params is not None
            self.responses[method] = {"thread": self.threads[str(params["threadId"])]}
        elif method == "thread/turns/list":
            assert params is not None
            older = params.get("cursor") is not None
            if older and self.history_release is not None:
                self.history_entered.set()
                await asyncio.to_thread(self.history_release.wait, 5)
            self.responses[method] = {
                "data": [
                    {
                        "id": "old" if older else "turn",
                        "status": "completed",
                        "itemsView": "full",
                        "items": [
                            {
                                "id": "answer",
                                "type": "agentMessage",
                                "text": "以前の本文" if older else "保存された本文",
                            }
                        ],
                    }
                ],
                "nextCursor": None if older else "opaque:/+?",
            }
        elif method == "thread/unsubscribe":
            self.responses[method] = {"status": "notLoaded"}
        elif method == "thread/start":
            assert params is not None
            self.threads["thread-1"] = {
                **_thread(Path(str(params["cwd"]))),
                "name": "新規の会話",
                "projectId": params["projectId"],
            }
        elif method == "turn/interrupt":
            assert params is not None
            self.events.put_nowait(
                RpcNotification(
                    "turn/completed",
                    {
                        "threadId": params["threadId"],
                        "turn": {"id": params["turnId"], "status": "interrupted"},
                    },
                )
            )
        return await super().request(method, params)


def _shell(qtbot: QtBot, root: Path) -> tuple[ApplicationShell, ConversationPeer]:
    capture = ControlledCapture()
    peer = ConversationPeer(root)

    def factory(
        options: ConnectionOptions, emit: "Callable[[RuntimeEvent], None]"
    ) -> CodexConnection:
        return CodexConnection(peer, options, emit)

    service = CodexServiceThread(root, capture.reader(), factory)
    shell = ApplicationShell(root / "settings.toml", service=service, capture_service=capture)
    shell.start()
    qtbot.waitUntil(lambda: shell.ready)
    return shell, peer


def test_sidebar_lists_lazily_and_browses_history_without_start_or_resume(
    qtbot: QtBot, tmp_path: Path
) -> None:
    shell, peer = _shell(qtbot, tmp_path)
    try:
        connect(qtbot, shell)
        sidebar = shell.window.sidebar
        qtbot.waitUntil(lambda: "project-1" in sidebar._groups, timeout=1000)
        qtbot.waitUntil(lambda: sidebar._groups[None].threads.count() == 1)
        assert not any(
            m == "thread/list" and p and p["projectId"] == "project-1" for m, p in peer.calls
        )
        group = sidebar._groups["project-1"]
        group.heading.click()
        qtbot.waitUntil(lambda: group.threads.count() == 1)
        peer.goal = {
            "objective": "保存された目的",
            "status": "active",
            "tokensUsed": 0,
            "timeUsedSeconds": 0,
        }
        qtbot.mouseClick(
            group.threads.viewport(),
            Qt.MouseButton.LeftButton,
            pos=group.threads.visualItemRect(group.threads.item(0)).center(),
        )
        panel = shell.window.connection
        qtbot.waitUntil(lambda: "保存された本文" in panel.transcript.toPlainText())
        assert panel.heading.title.text() == "保存会話"
        assert panel.plan_panel.goal_value.toPlainText() == "保存された目的"
        assert panel.can_switch_conversation
        assert not panel.interrupt_button.isEnabled()
        panel.history_button.click()
        qtbot.waitUntil(lambda: "以前の本文" in panel.transcript.toPlainText())
        assert panel.transcript.toPlainText().index(
            "以前の本文"
        ) < panel.transcript.toPlainText().index("保存された本文")
        assert not panel.history_button.isVisible()
        assert not any(m in {"thread/start", "thread/resume", "turn/start"} for m, _ in peer.calls)
        assert (
            "thread/turns/list",
            {"threadId": "saved", "itemsView": "full", "cursor": "opaque:/+?"},
        ) in peer.calls
    finally:
        close(qtbot, shell)


def _select_unassigned(qtbot: QtBot, shell: ApplicationShell) -> None:
    rows = shell.window.sidebar._groups[None].threads
    qtbot.waitUntil(lambda: rows.count() > 0)
    qtbot.mouseClick(
        rows.viewport(), Qt.MouseButton.LeftButton, pos=rows.visualItemRect(rows.item(0)).center()
    )
    qtbot.waitUntil(
        lambda: (
            shell.window.connection.heading.title.text() == "未所属会話"
            and not shell.window.connection.heading.loading.isVisible()
        )
    )


def test_draft_confirmation_precedes_selection_and_failed_read_restores_content(
    qtbot: QtBot, tmp_path: Path
) -> None:
    shell, peer = _shell(qtbot, tmp_path)
    try:
        connect(qtbot, shell)
        _select_unassigned(qtbot, shell)
        panel = shell.window.connection
        panel.prompt.setPlainText("保持する原稿")
        rows = shell.window.sidebar._groups[None].threads
        rows.itemActivated.emit(rows.item(0))
        assert shell.conversations.confirmation is None
        assert panel.draft == "保持する原稿"
        group = shell.window.sidebar._groups["project-1"]
        group.heading.click()
        qtbot.waitUntil(lambda: group.threads.count() == 1)
        before = list(peer.calls)
        group.threads.itemActivated.emit(group.threads.item(0))
        box = shell.conversations.confirmation
        assert box is not None
        assert panel.transcript.isVisible()
        assert peer.calls == before
        box.button(QMessageBox.StandardButton.Cancel).click()
        assert panel.draft == "保持する原稿"
        peer.failure = "thread/read"
        group.threads.itemActivated.emit(group.threads.item(0))
        box = shell.conversations.confirmation
        assert box is not None
        box.button(QMessageBox.StandardButton.Discard).click()
        assert panel.draft == ""
        assert panel.transcript.isHidden()
        assert panel.prompt.isReadOnly()
        qtbot.waitUntil(panel.transcript.isVisible)
        assert panel.heading.title.text() == "未所属会話"
        assert "保存された本文" in panel.transcript.toPlainText()
        assert "codex" in shell.window.notifications.notices
        peer.failure = None
        group.new_button.click()
        qtbot.waitUntil(
            lambda: (
                panel.heading.title.text() == "新しい会話"
                and panel.heading.project.text() == "Project"
                and not panel.heading.loading.isVisible()
            )
        )
        assert panel.transcript.toPlainText() == ""
        assert shell.window.focusWidget() is panel.prompt
        assert str(tmp_path) in shell.window.windowTitle()
        QApplication.sendEvent(panel.prompt, QInputMethodEvent("未確定だけ", []))
        assert panel.prompt.is_composing
        shell.window.sidebar._groups[None].new_button.click()
        qtbot.waitUntil(
            lambda: (
                panel.heading.project.text() == "未所属" and not panel.heading.loading.isVisible()
            )
        )
        assert shell.window.windowTitle() == "GIGAS"
        assert not panel.prompt.is_composing
        assert not (tmp_path / "unassigned").exists()
        assert not any(m in {"thread/start", "thread/resume"} for m, _ in peer.calls)
    finally:
        close(qtbot, shell)


def test_old_history_and_goal_results_do_not_consume_new_target_request(
    qtbot: QtBot, tmp_path: Path
) -> None:
    shell, peer = _shell(qtbot, tmp_path)
    release = Event()
    try:
        generation = connect(qtbot, shell)
        _select_unassigned(qtbot, shell)
        old = shell.service.selected_target
        assert isinstance(old, StoredConversationTarget)
        shell.window.sidebar._groups[None].new_button.click()
        qtbot.waitUntil(
            lambda: (
                shell.window.connection.heading.title.text() == "新しい会話"
                and not shell.window.connection.heading.loading.isVisible()
            )
        )
        _select_unassigned(qtbot, shell)
        current = shell.service.selected_target
        assert current == old
        assert current is not old
        peer.history_release = release
        panel = shell.window.connection
        panel.history_button.click()
        qtbot.waitUntil(peer.history_entered.is_set)
        shell.service.management_completed.emit(
            ManagementResult(generation, "history", HistoryResult(old, TurnPage((), None, None)))
        )
        shell.service.browse_goal_changed.emit(
            generation, BrowseGoalEvent(old, GoalState("古い目的", "active"))
        )
        release.set()
        qtbot.waitUntil(lambda: "以前の本文" in panel.transcript.toPlainText())
        assert "古い目的" not in panel.plan_panel.goal_value.toPlainText()
        shell.service.management_completed.emit(
            ManagementResult(
                generation - 1,
                "projects",
                ProjectPage((ProjectInfo("stale", "古い所属", tmp_path),), None),
            )
        )
        assert "stale" not in shell.window.sidebar._groups
    finally:
        release.set()
        close(qtbot, shell)


def test_first_accepted_send_refreshes_an_inflight_list_without_clearing_live_text(
    qtbot: QtBot, tmp_path: Path
) -> None:
    shell, peer = _shell(qtbot, tmp_path)
    release = Event()
    peer.thread_list_release = release
    try:
        connect(qtbot, shell)
        qtbot.waitUntil(peer.thread_list_entered.is_set)
        panel = shell.window.connection
        panel.prompt.setPlainText("初回指示")
        panel.send_button.click()
        qtbot.waitUntil(lambda: panel.prompt.toPlainText() == "")
        before = panel.transcript.toPlainText()
        assert "初回指示" in before
        panel.prompt.setPlainText("次の原稿")
        release.set()
        rows = shell.window.sidebar._groups[None].threads
        qtbot.waitUntil(
            lambda: any(
                rows.item(i).data(Qt.ItemDataRole.UserRole) == "thread-1"
                for i in range(rows.count())
            )
        )
        assert panel.heading.title.text() == "新規の会話"
        assert panel.transcript.toPlainText() == before
        assert panel.draft == "次の原稿"
        assert len([m for m, _ in peer.calls if m == "thread/list"]) == 2
        assert not shell.window.sidebar._groups[None].new_button.isEnabled()
        panel.interrupt_button.click()
        qtbot.waitUntil(shell.window.sidebar._groups[None].new_button.isEnabled)
    finally:
        release.set()
        close(qtbot, shell)


def test_input_busy_blocks_switch_but_keeps_send_clear_and_stop_and_close_cancels_confirmation(
    qtbot: QtBot, tmp_path: Path
) -> None:
    shell, _peer = _shell(qtbot, tmp_path)
    try:
        generation = connect(qtbot, shell)
        _select_unassigned(qtbot, shell)
        panel = shell.window.connection
        target = shell.service.selected_target
        assert isinstance(target, StoredConversationTarget)
        shell.service.browse_goal_changed.emit(
            generation, BrowseGoalEvent(target, GoalState("保持した目的", "paused"))
        )
        panel.prompt.setPlainText("次の指示")
        shell.service.input_busy_changed.emit(True)
        assert panel.send_button.isEnabled()
        assert panel.plan_panel.clear_button.isEnabled()
        assert panel.interrupt_button.isEnabled()
        assert not shell.window.sidebar._groups[None].new_button.isEnabled()
        assert not shell.window.project_menu.isEnabled()
        shell.service.input_busy_changed.emit(False)
        shell.window.sidebar._groups[None].new_button.click()
        box = shell.conversations.confirmation
        assert box is not None
        shell.request_close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())
        assert shell.conversations.confirmation is None
        assert shell.service.selected_target is target
    finally:
        if not shell.committed:
            close(qtbot, shell)


def test_project_pages_and_reconnect_keep_expansion_display_and_ignore_stale_picker(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    shell, peer = _shell(qtbot, tmp_path)
    second: JsonObject = {
        "id": "project-2",
        "name": "別Project",
        "roots": [{"path": str(tmp_path)}],
    }
    peer.project_pages = {
        None: {"data": peer.projects, "nextCursor": "projects:/+?"},
        "projects:/+?": {"data": [second], "nextCursor": None},
    }
    try:
        generation = connect(qtbot, shell)
        sidebar, panel = shell.window.sidebar, shell.window.connection
        qtbot.waitUntil(sidebar.projects_more_button.isVisible)
        sidebar.projects_more_button.click()
        qtbot.waitUntil(lambda: "project-2" in sidebar._groups)
        group = sidebar._groups["project-2"]
        group.heading.click()
        qtbot.waitUntil(lambda: not group.loading.isVisible())
        assert group.threads.count() == 0
        assert group.new_button.isEnabled()
        _select_unassigned(qtbot, shell)
        document = panel.transcript.document()
        panel.prompt.setPlainText("再接続後も保持")
        panel.disconnect_action.trigger()
        qtbot.waitUntil(panel.connect_action.isEnabled)
        connect(qtbot, shell)
        qtbot.waitUntil(lambda: not sidebar.loading.isVisible())
        assert sidebar._groups["project-2"] is group
        assert group.heading.isChecked()
        assert panel.transcript.document() is document
        assert "保存された本文" in panel.transcript.toPlainText()
        assert panel.draft == "再接続後も保持"
        title = shell.window.windowTitle()
        shell._project_pending = True
        shell.service.project_completed.emit(
            ProjectResult(
                generation, tmp_path / "stale", ProjectInfo("stale", "古い所属", tmp_path / "stale")
            )
        )
        assert not shell._project_pending
        assert shell.window.windowTitle() == title
        assert shell.baseline.last_project_path == ""
        assert "stale" not in sidebar._groups
        assert ("project/list", {"cursor": "projects:/+?"}) in peer.calls
    finally:
        close(qtbot, shell)
