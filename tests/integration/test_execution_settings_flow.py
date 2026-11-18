from pathlib import Path
from threading import Event

import pytest
from pytestqt.qtbot import QtBot

from gigas.runtime.codex.transport import JsonObject, RpcNotification
from gigas.storage.settings import load_settings
from tests.integration.peers.conversation_gui import ConversationGui, conversation_gui


def _save_execution(session: ConversationGui, *, enabled: bool) -> None:
    shell = session.shell
    shell.window.settings_action.trigger()
    dialog = shell.dialog
    assert dialog is not None
    dialog.tabs.setCurrentIndex(dialog.codex_tab)
    dialog.web_search_enabled.setChecked(enabled)
    dialog.memory_enabled.setChecked(enabled)
    dialog.approvals_reviewer.setCurrentIndex(
        dialog.approvals_reviewer.findData("auto_review" if enabled else "user")
    )
    dialog.ok_button.click()
    session.qtbot.waitUntil(lambda: shell.operation is None and not dialog.isVisible())
    saved = load_settings(shell.path)
    assert saved.web_search_enabled is enabled
    assert saved.memory_enabled is enabled
    assert saved.approvals_reviewer == ("auto_review" if enabled else "user")


def _choose_model(session: ConversationGui, model: str, effort: str) -> None:
    composer = session.panel.composer
    composer.model_button.click()
    composer.model_choice.setCurrentText(model)
    assert not session.command(
        "select_model",
        lambda: composer.model_choice.activated.emit(composer.model_choice.currentIndex()),
    ).error
    composer.effort_choice.setCurrentText(effort)
    assert not session.command(
        "select_model",
        lambda: composer.effort_choice.activated.emit(composer.effort_choice.currentIndex()),
    ).error
    composer.popup.hide()


def _assert_execution(params: JsonObject, *, enabled: bool, model: str, effort: str) -> None:
    assert params["model"] == model
    assert params["approvalsReviewer"] == ("auto_review" if enabled else "user")
    config = params["config"]
    assert isinstance(config, dict)
    assert config["model_reasoning_effort"] == effort
    assert config["web_search"] == ("live" if enabled else "disabled")
    assert config["features.memories"] is enabled
    assert config["memories.generate_memories"] is enabled
    assert config["memories.use_memories"] is enabled


@pytest.mark.parametrize("saved_conversation", [False, True], ids=["new", "browse"])
@pytest.mark.parametrize("fast_mode_disabled", [False, True], ids=["fast-enabled", "fast-disabled"])
def test_saved_ui_settings_and_model_choices_reach_start_resume_and_live_history(
    qtbot: QtBot, tmp_path: Path, *, saved_conversation: bool, fast_mode_disabled: bool
) -> None:
    with conversation_gui(qtbot, tmp_path) as session:
        panel, peer = session.panel, session.peer
        peer.fast_mode_disabled = fast_mode_disabled
        assert not session.command("connect", panel.connect_action.trigger).error
        if saved_conversation:
            peer.goal = {
                "objective": "継続中の目標",
                "status": "active",
                "tokensUsed": 30,
                "timeUsedSeconds": 2,
            }
            rows = session.shell.window.sidebar._groups[None].threads
            qtbot.waitUntil(lambda: rows.count() == 1)
            rows.itemActivated.emit(rows.item(0))
            qtbot.waitUntil(lambda: "保存された本文" in panel.transcript.toPlainText())
            assert panel.plan_panel.goal_value.toPlainText() == "継続中の目標"
        _save_execution(session, enabled=False)
        panel.prompt.setPlainText("続きの指示")
        _choose_model(session, "other", "low")
        assert panel.draft == "続きの指示"
        baseline = load_settings(session.shell.path)
        assert baseline.model == ""
        assert baseline.effort == ""
        assert not any(
            method in {"thread/start", "thread/resume", "thread/settings/update"}
            for method, _ in peer.calls
        )
        assert len(session.options) == 1
        assert not session.command("send", panel.send_button.click).error
        method = "thread/resume" if saved_conversation else "thread/start"
        params = session.params(method)
        assert len(params) == 1
        assert params[0]["serviceTier"] == "default"
        _assert_execution(params[0], enabled=False, model="other", effort="low")
        methods = [name for name, _ in peer.calls]
        assert methods.index(method) < methods.index("turn/start")
        thread_id = "saved" if saved_conversation else "thread-1"
        session.emit(
            RpcNotification(
                "item/completed",
                {
                    "threadId": thread_id,
                    "turnId": "turn-1",
                    "item": {"id": "live", "type": "agentMessage", "text": "新着の本文"},
                },
            )
        )
        qtbot.waitUntil(lambda: "新着の本文" in panel.transcript.toPlainText())
        if saved_conversation:
            text = panel.transcript.toPlainText()
            assert text.index("保存された本文") < text.index("新着の本文")
            assert panel.plan_panel.goal_value.toPlainText() == "継続中の目標"
            assert not any(m == "thread/goal/set" for m, _ in peer.calls)
        _save_execution(session, enabled=True)
        panel.prompt.setPlainText("追加の指示")
        assert not session.command("send", panel.send_button.click).error
        assert len(session.params(method)) == 1
        assert not session.params("thread/settings/update")
        assert len(session.options) == 1
        turns = session.params("turn/start")
        assert len(turns) == 2
        assert all(turn["model"] == "other" and turn["effort"] == "low" for turn in turns)
        assert all(turn["serviceTier"] == "default" for turn in turns)
        if saved_conversation:
            assert peer.goal is not None
            assert peer.goal["status"] == "active"
            assert not session.params("thread/goal/set")
        document = panel.transcript.document()
        assert not session.command("disconnect", panel.disconnect_action.trigger).error
        assert not session.command("connect", panel.connect_action.trigger).error
        panel.prompt.setPlainText("再接続後の指示")
        _choose_model(session, "other", "low")
        assert not session.command("send", panel.send_button.click).error
        _assert_execution(
            session.params("thread/resume")[-1], enabled=True, model="other", effort="low"
        )
        assert panel.transcript.document() is document
        assert "新着の本文" in panel.transcript.toPlainText()
        assert load_settings(session.shell.path).model == baseline.model
        assert load_settings(session.shell.path).effort == baseline.effort


def test_loaded_model_selection_waits_for_update_and_retains_selection_on_rejection(
    qtbot: QtBot, tmp_path: Path
) -> None:
    with conversation_gui(qtbot, tmp_path) as session:
        panel, peer = session.panel, session.peer
        assert not session.command("connect", panel.connect_action.trigger).error
        panel.prompt.setPlainText("開始")
        assert not session.command("send", panel.send_button.click).error
        panel.prompt.setPlainText("未送信の原稿")
        composer = panel.composer
        original = composer.model_button.text()
        release = peer.update_release = Event()
        composer.model_button.click()
        composer.model_choice.setCurrentText("other")
        composer.model_choice.activated.emit(composer.model_choice.currentIndex())
        qtbot.waitUntil(peer.update_entered.is_set)
        assert composer.model_button.text() == original
        assert not composer.model_button.isEnabled()
        assert panel.draft == "未送信の原稿"
        release.set()
        qtbot.waitUntil(composer.model_button.isEnabled)
        assert composer.model_button.text() == "other · medium · Standard"
        assert session.params("thread/settings/update") == [
            {"threadId": "thread-1", "model": "other", "effort": "medium"}
        ]
        peer.rpc_errors["thread/settings/update"] = -32000
        composer.effort_choice.setCurrentText("low")
        result = session.command(
            "select_model",
            lambda: composer.effort_choice.activated.emit(composer.effort_choice.currentIndex()),
        )
        assert result.error
        assert composer.model_button.text() == "other · medium · Standard"
        assert composer.model_button.isEnabled()
        assert panel.draft == "未送信の原稿"
        assert "codex" in session.shell.window.notifications.notices
        assert len(session.params("thread/start")) == 1
        assert not session.params("thread/resume")
        saved = load_settings(session.shell.path)
        assert saved.model == ""
        assert saved.effort == ""
        assert saved.codex_auto_connect
        assert not session.command("send", panel.send_button.click).error
        assert session.params("turn/start")[-1]["model"] == "other"
        assert session.params("turn/start")[-1]["effort"] == "medium"


@pytest.mark.parametrize("running_turn", [True, False], ids=["steer", "new-turn"])
def test_goal_speed_change_is_submitted_only_with_additional_chat(
    qtbot: QtBot, tmp_path: Path, *, running_turn: bool
) -> None:
    with conversation_gui(qtbot, tmp_path) as session:
        panel, peer = session.panel, session.peer
        peer.responses["model/list"] = {
            "data": [
                {
                    "model": "supported",
                    "isDefault": True,
                    "defaultReasoningEffort": "medium",
                    "supportedReasoningEfforts": [{"reasoningEffort": "medium"}],
                    "serviceTiers": [
                        {"id": "priority", "name": "Fast"},
                        {"id": "ultrafast", "name": "Ultrafast"},
                    ],
                },
            ],
            "nextCursor": None,
        }
        assert not session.command("connect", panel.connect_action.trigger).error
        composer = panel.composer
        composer.model_button.click()
        assert composer.speed_choice.currentText() == "Standard"
        composer.speed_choice.setCurrentText("Fast")
        composer.speed_choice.activated.emit(composer.speed_choice.currentIndex())
        composer.popup.hide()
        composer.goal_mode.click()
        panel.prompt.setPlainText("ゲームの目標")
        assert not session.command("goal_set", panel.send_button.click).error
        assert session.params("thread/start")[-1]["serviceTier"] == "priority"
        if running_turn:
            session.emit(
                RpcNotification(
                    "turn/started",
                    {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "inProgress"}},
                )
            )
            qtbot.waitUntil(lambda: panel.status.text() == "実行中")
        composer.model_button.click()
        assert composer.speed_choice.isEnabled()
        composer.speed_choice.setCurrentText("Ultrafast")
        composer.speed_choice.activated.emit(composer.speed_choice.currentIndex())
        composer.popup.hide()
        assert not session.params("thread/settings/update")
        assert not session.params("turn/start")
        composer.goal_mode.setChecked(False)
        panel.prompt.setPlainText("追加の指示")
        assert not session.command("send", panel.send_button.click).error
        (turn,) = session.params("turn/start")
        assert turn["serviceTier"] == "ultrafast"
        assert "serviceTierForTurn" not in turn
        assert not session.params("turn/interrupt")


def test_loaded_goal_resume_waits_for_speed_confirmation(qtbot: QtBot, tmp_path: Path) -> None:
    with conversation_gui(qtbot, tmp_path) as session:
        panel, peer = session.panel, session.peer
        assert not session.command("connect", panel.connect_action.trigger).error
        panel.composer.goal_mode.click()
        panel.prompt.setPlainText("ゲームの目標")
        assert not session.command("goal_set", panel.send_button.click).error
        assert not session.command("stop", panel.interrupt_button.click).error
        assert peer.goal is not None
        assert peer.goal["status"] == "paused"
        goal_mutations_before_resume = len(session.params("thread/goal/set"))

        def confirm_speed() -> None:
            panel.plan_panel.resume_button.click()
            qtbot.waitUntil(lambda: bool(session.params("thread/settings/update")))
            assert session.params("thread/settings/update") == [
                {"threadId": "thread-1", "serviceTier": "default"}
            ]
            assert len(session.params("thread/goal/set")) == goal_mutations_before_resume
            session.emit(
                RpcNotification(
                    "thread/settings/updated",
                    {"threadId": "thread-1", "threadSettings": {"serviceTier": "default"}},
                )
            )

        assert not session.command("goal_resume", confirm_speed).error
        assert len(session.params("thread/goal/set")) == goal_mutations_before_resume + 1
        assert peer.goal["status"] == "active"
