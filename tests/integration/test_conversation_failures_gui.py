from pathlib import Path

from PySide6.QtGui import QTextCursor
from pytestqt.qtbot import QtBot

from gigas.runtime.codex.transport import JsonObject, RpcNotification, TransportClosed
from gigas.storage.records import GameIoRecordWriter
from tests.integration.peers.conversation_gui import ConversationGui, conversation_gui


def _page(identifier: str, text: str, cursor: str | None = None) -> JsonObject:
    return {
        "data": [
            {
                "id": identifier,
                "status": "completed",
                "itemsView": "full",
                "items": [{"id": identifier, "type": "agentMessage", "text": text}],
            }
        ],
        "nextCursor": cursor,
    }


def _browse(session: ConversationGui) -> None:
    session.peer.goal = {
        "objective": "保持する目的",
        "status": "paused",
        "tokensUsed": 20,
        "timeUsedSeconds": 1,
    }
    assert not session.command("connect", session.panel.connect_action.trigger).error
    rows = session.shell.window.sidebar._groups[None].threads
    session.qtbot.waitUntil(lambda: rows.count() == 1)
    rows.itemActivated.emit(rows.item(0))
    session.qtbot.waitUntil(
        lambda: (
            session.panel.heading.title.text() == "保存会話"
            and not session.panel.heading.loading.isVisible()
        )
    )


def _assert_not_executed(session: ConversationGui) -> None:
    assert not any(
        method in {"thread/start", "thread/resume", "turn/start", "thread/goal/set"}
        for peer in session.peers
        for method, _ in peer.calls
    )
    assert not (session.shell.path.parent / "records").exists()


def test_history_failure_keeps_document_position_and_cursor_for_explicit_retry(
    qtbot: QtBot, tmp_path: Path
) -> None:
    with conversation_gui(qtbot, tmp_path) as session:
        peer, panel = session.peer, session.panel
        peer.responses["thread/turns/list"] = _page(
            "recent", "\n".join(f"保存行 {index}" for index in range(150)), "opaque:/+?"
        )
        _browse(session)
        document = panel.transcript.document()
        original = panel.transcript.toPlainText()
        scroll = panel.transcript.verticalScrollBar()
        qtbot.waitUntil(lambda: scroll.maximum() > 0)
        cursor = panel.transcript.textCursor()
        cursor.setPosition(12)
        cursor.setPosition(25, QTextCursor.MoveMode.KeepAnchor)
        panel.transcript.setTextCursor(cursor)
        scroll.setValue(scroll.maximum() // 2)
        position = scroll.value()
        peer.rpc_errors["thread/turns/list"] = -32603
        panel.history_button.click()
        qtbot.waitUntil(
            lambda: (
                "codex" in session.shell.window.notifications.notices
                and panel.history_button.isEnabled()
            )
        )
        assert panel.transcript.document() is document
        assert panel.transcript.toPlainText() == original
        assert panel.transcript.textCursor().selectedText() == cursor.selectedText()
        assert scroll.value() == position
        assert len(session.params("thread/turns/list")) == 2
        assert session.shell.window.notifications.notices["codex"].details
        del peer.rpc_errors["thread/turns/list"]
        peer.responses["thread/turns/list"] = _page("older", "さらに以前の本文")
        panel.history_button.click()
        qtbot.waitUntil(lambda: "さらに以前の本文" in panel.transcript.toPlainText())
        requests = session.params("thread/turns/list")
        assert requests[-2] == requests[-1]
        assert requests[-1]["cursor"] == "opaque:/+?"
        assert panel.transcript.document() is document
        assert original in panel.transcript.toPlainText()
        assert not panel.history_button.isVisible()
        _assert_not_executed(session)


def test_missing_workspace_preserves_browse_and_draft_until_user_restores_directory(
    qtbot: QtBot, tmp_path: Path
) -> None:
    missing = tmp_path / "missing-workspace"
    with conversation_gui(qtbot, tmp_path) as session:
        response = session.peer.responses["thread/read"]
        assert isinstance(response, dict)
        thread = response["thread"]
        assert isinstance(thread, dict)
        thread["cwd"] = str(missing)
        _browse(session)
        panel = session.panel
        document = panel.transcript.document()
        target = session.service.selected_target
        panel.prompt.setPlainText("場所を戻して続行")
        result = session.command("send", panel.send_button.click)
        assert result.error
        assert result.message is not None
        assert result.message.outcome == "not_sent"
        assert not missing.exists()
        assert session.service.selected_target is target
        assert panel.transcript.document() is document
        assert "保存された本文" in panel.transcript.toPlainText()
        assert panel.plan_panel.goal_value.toPlainText() == "保持する目的"
        assert panel.draft == "場所を戻して続行"
        assert session.shell.window.notifications.notices["codex"].details
        _assert_not_executed(session)
        missing.mkdir()
        assert not session.command("send", panel.send_button.click).error
        assert session.params("thread/resume")[0]["cwd"] == str(missing)
        assert panel.transcript.document() is document
        assert panel.draft == ""
        assert (missing / "records" / "saved" / "events.jsonl").is_file()
        assert not session.params("thread/start")


def test_sent_resume_failure_requires_reconnect_and_retains_unsent_draft(
    qtbot: QtBot, tmp_path: Path
) -> None:
    with conversation_gui(qtbot, tmp_path) as session:
        _browse(session)
        peer, panel = session.peer, session.panel
        document = panel.transcript.document()
        target = session.service.selected_target
        peer.failure = "thread/resume"
        panel.prompt.setPlainText("受付を確認できなかった指示")
        first = session.command("send", panel.send_button.click)
        assert first.error
        assert first.message is not None
        assert first.message.outcome == "not_sent"
        peer.failure = None
        retry = session.command("send", panel.send_button.click)
        assert retry.error
        assert len(session.params("thread/resume")) == 1
        assert not session.params("turn/start")
        assert not session.params("thread/start")
        assert panel.transcript.document() is document
        assert panel.plan_panel.goal_value.toPlainText() == "保持する目的"
        assert panel.draft == "受付を確認できなかった指示"
        assert session.service.selected_target is target
        assert session.shell.window.notifications.notices["codex"].details
        assert not session.command("disconnect", panel.disconnect_action.trigger).error
        assert not session.command("connect", panel.connect_action.trigger).error
        assert len(session.params("thread/resume")) == 1
        assert not session.command("send", panel.send_button.click).error
        assert len(session.params("thread/resume")) == 2
        assert len(session.params("turn/start")) == 1
        assert panel.transcript.document() is document
        assert panel.draft == ""


def test_browse_goal_clear_rejection_keeps_goal_and_retry_needs_no_execution(
    qtbot: QtBot, tmp_path: Path
) -> None:
    with conversation_gui(qtbot, tmp_path) as session:
        _browse(session)
        peer, panel = session.peer, session.panel
        document = panel.transcript.document()
        target = session.service.selected_target
        peer.rpc_errors["thread/goal/clear"] = -32603
        panel.prompt.setPlainText("未送信の本文")
        assert session.command("goal_clear", panel.plan_panel.clear_button.click).error
        assert panel.plan_panel.goal_value.toPlainText() == "保持する目的"
        assert panel.plan_panel.clear_button.isEnabled()
        assert panel.draft == "未送信の本文"
        assert panel.transcript.document() is document
        assert session.shell.window.notifications.notices["codex"].details
        del peer.rpc_errors["thread/goal/clear"]
        assert not session.command("goal_clear", panel.plan_panel.clear_button.click).error
        assert "保持する目的" not in panel.plan_panel.goal_value.toPlainText()
        assert not panel.plan_panel.clear_button.isEnabled()
        assert not panel.plan_panel.resume_button.isEnabled()
        assert session.service.selected_target is target
        assert panel.transcript.document() is document
        assert panel.draft == "未送信の本文"
        assert session.params("thread/goal/clear") == [{"threadId": "saved", "origin": "user"}] * 2
        _assert_not_executed(session)


def test_live_transport_loss_releases_records_and_preserves_document_until_explicit_reconnect(
    qtbot: QtBot, tmp_path: Path
) -> None:
    with conversation_gui(qtbot, tmp_path) as session:
        _browse(session)
        panel, peer = session.panel, session.peer
        panel.prompt.setPlainText("続ける")
        assert not session.command("send", panel.send_button.click).error
        session.emit(
            RpcNotification(
                "item/completed",
                {
                    "threadId": "saved",
                    "turnId": "turn-1",
                    "item": {"id": "live", "type": "agentMessage", "text": "切断前の応答"},
                },
            )
        )
        qtbot.waitUntil(lambda: "切断前の応答" in panel.transcript.toPlainText())
        document = panel.transcript.document()
        original = panel.transcript.toPlainText()
        panel.prompt.setPlainText("次に提出する原稿")
        session.service._enqueue(lambda: peer.events.put_nowait(TransportClosed("connection lost")))
        qtbot.waitUntil(lambda: session.service._connection is None)
        qtbot.waitUntil(lambda: "codex" in session.shell.window.notifications.notices)
        assert session.shell.window.notifications.notices["codex"].details
        assert panel.transcript.document() is document
        assert panel.transcript.toPlainText() == original
        assert panel.draft == "次に提出する原稿"
        assert not panel.send_button.isEnabled()
        assert not panel.connect_action.isEnabled()
        assert panel.disconnect_action.isEnabled()
        assert len(session.options) == 1
        assert len(session.params("thread/resume")) == 1
        writer = GameIoRecordWriter.open(tmp_path / "records" / "saved")
        writer.close()
        assert not session.command("disconnect", panel.disconnect_action.trigger).error
        assert not session.command("connect", panel.connect_action.trigger).error
        assert len(session.params("thread/resume")) == 1
        assert panel.draft == "次に提出する原稿"
        assert not session.command("send", panel.send_button.click).error
        assert len(session.params("thread/resume")) == 2
        assert panel.transcript.document() is document
        assert original in panel.transcript.toPlainText()
        assert panel.draft == ""
