from typing import TYPE_CHECKING

import pytest

from gigas.runtime.codex import _conversation_protocol as protocol
from gigas.runtime.codex.connection import CodexConnectionError
from gigas.runtime.codex.models import ExecutionSettings, ModelSelection, ThreadConfiguration

if TYPE_CHECKING:
    from pathlib import Path

    from gigas.runtime.codex.transport import JsonObject, JsonValue


@pytest.mark.parametrize("project_id", [None, "project-1"])
@pytest.mark.parametrize("resume", [False, True])
def test_thread_preparation_uses_workspace_assignment_and_replaces_inherited_mcp(
    tmp_path: "Path", monkeypatch: pytest.MonkeyPatch, project_id: str | None, *, resume: bool
) -> None:
    packaged_instructions = "packaged instructions"
    monkeypatch.setattr(protocol, "read_play_instructions", lambda: packaged_instructions)
    configuration = ThreadConfiguration(
        tmp_path,
        {
            "mcp_servers.game.enabled": True,
            "mcp_servers.game.url": "http://127.0.0.1:3210/mcp",
            "mcp_servers.game.http_headers": {"Authorization": "Bearer private-value"},
        },
        project_id=project_id,
    )
    selection = ModelSelection((), "selected-model", "high")
    settings = ExecutionSettings(False, False, "user")
    server_config: JsonObject = {
        "config": {"mcp_servers": {"existing": {}, "name.with.dot": {}, "game": {}}}
    }

    if resume:
        params = protocol.thread_resume_params(
            "saved-thread", configuration, selection, server_config, settings=settings
        )
        assert params["threadId"] == "saved-thread"
        assert params["excludeTurns"] is True
        assert "projectId" not in params
        assert "ephemeral" not in params
        assert "path" not in params
        assert "history" not in params
    else:
        params = protocol.thread_start_params(
            configuration, selection, server_config, settings=settings
        )
        assert params["projectId"] == project_id
        assert params["ephemeral"] is False

    assert params["cwd"] == str(tmp_path)
    assert params["runtimeWorkspaceRoots"] == [str(tmp_path)]
    assert params["model"] == "selected-model"
    assert "effort" not in params
    assert params["sandbox"] == "workspace-write"
    assert params["approvalPolicy"] == "on-request"
    assert params["approvalsReviewer"] == "user"
    config = params["config"]
    assert isinstance(config, dict)
    expected: JsonObject = {
        "model_reasoning_effort": "high",
        "web_search": "disabled",
        "features.memories": False,
        "mcp_servers.existing.enabled": False,
        'mcp_servers."name.with.dot".enabled': False,
        **configuration.mcp_config,
        "sandbox_workspace_write.writable_roots": [],
        "sandbox_workspace_write.network_access": False,
        "sandbox_workspace_write.exclude_tmpdir_env_var": True,
        "sandbox_workspace_write.exclude_slash_tmp": True,
    }
    for key, value in expected.items():
        assert config[key] == value
    instructions = params["developerInstructions"]
    assert isinstance(instructions, str)
    assert instructions == packaged_instructions
    assert "private-value" not in instructions
    assert server_config == {
        "config": {"mcp_servers": {"existing": {}, "name.with.dot": {}, "game": {}}}
    }


def _thread_result(configuration: ThreadConfiguration) -> "JsonObject":
    return {
        "thread": {"id": "thread-1", "projectId": configuration.project_id},
        "model": "selected-model",
        "reasoningEffort": "high",
        "cwd": str(configuration.cwd),
        "runtimeWorkspaceRoots": [str(configuration.cwd)],
        "approvalPolicy": "on-request",
        "approvalsReviewer": "auto_review",
        "sandbox": {
            "type": "workspaceWrite",
            "writableRoots": [],
            "networkAccess": False,
            "excludeTmpdirEnvVar": True,
            "excludeSlashTmp": True,
        },
    }


@pytest.mark.parametrize("project_id", [None, "project-1"])
@pytest.mark.parametrize("expected_thread_id", [None, "thread-1"])
def test_confirmed_thread_returns_only_its_identity(
    tmp_path: "Path", project_id: str | None, expected_thread_id: str | None
) -> None:
    configuration = ThreadConfiguration(tmp_path, project_id=project_id)
    selection = ModelSelection((), "selected-model", "high")

    assert protocol.validate_thread(
        _thread_result(configuration),
        configuration,
        selection,
        settings=ExecutionSettings(),
        expected_thread_id=expected_thread_id,
    ) == ("thread-1")


@pytest.mark.parametrize(
    "override",
    [
        {"approvalPolicy": "never"},
        {"approvalsReviewer": "user"},
        {"model": "different-model"},
        {"reasoningEffort": "low"},
        {"reasoningEffort": None},
        {"cwd": "other-directory"},
        {"runtimeWorkspaceRoots": None},
        {"runtimeWorkspaceRoots": []},
        {"runtimeWorkspaceRoots": ["other-directory"]},
        {"sandbox": {"type": "dangerFullAccess"}},
        {"thread": {"id": "", "projectId": None}},
        {"thread": {"id": "thread-1"}},
        {"thread": {"id": "thread-1", "projectId": "unexpected-project"}},
    ],
)
def test_thread_rejects_unconfirmed_configuration(tmp_path: "Path", override: "JsonObject") -> None:
    configuration = ThreadConfiguration(tmp_path)
    selection = ModelSelection((), "selected-model", "high")

    with pytest.raises(CodexConnectionError):
        protocol.validate_thread(
            {**_thread_result(configuration), **override},
            configuration,
            selection,
            settings=ExecutionSettings(),
        )


def test_resume_rejects_a_different_thread_identity(tmp_path: "Path") -> None:
    configuration = ThreadConfiguration(tmp_path)
    with pytest.raises(CodexConnectionError, match="実行設定"):
        protocol.validate_thread(
            _thread_result(configuration),
            configuration,
            ModelSelection((), "selected-model", "high"),
            settings=ExecutionSettings(),
            expected_thread_id="different-thread",
        )


def test_ordinary_message_uses_thread_input_and_selected_model_without_a_target_turn() -> None:
    assert protocol.turn_start_params(
        "thread-1", "右へ進んで\n次は左へ", ModelSelection((), "selected-model", "high")
    ) == {
        "threadId": "thread-1",
        "input": [{"type": "text", "text": "右へ進んで\n次は左へ"}],
        "model": "selected-model",
        "effort": "high",
        "summary": "auto",
    }


@pytest.mark.parametrize("status", ["inProgress", "completed", "interrupted", "failed"])
def test_start_response_supplies_identity_without_interpreting_turn_status(status: str) -> None:
    assert protocol.turn_id({"turn": {"id": "turn-2", "status": status}}) == "turn-2"


def test_start_response_requires_a_turn_identity() -> None:
    with pytest.raises(CodexConnectionError):
        protocol.turn_id({"turn": {"status": "inProgress"}})


def test_turn_notification_returns_identity_and_reported_status() -> None:
    assert protocol.turn_notification(
        {"threadId": "thread-1", "turn": {"id": "turn-2", "status": "failed"}}
    ) == ("turn-2", "failed")


@pytest.mark.parametrize("turn", [None, {"status": "completed"}, {"id": 7}])
def test_turn_notification_requires_a_turn_identity(turn: "JsonValue") -> None:
    with pytest.raises(CodexConnectionError):
        protocol.turn_notification({"turn": turn})


def test_goal_mutations_send_only_the_requested_changes() -> None:
    assert protocol.goal_params("thread-1", objective="タマムシシティへ", status="active") == {
        "threadId": "thread-1",
        "origin": "user",
        "objective": "タマムシシティへ",
        "status": "active",
    }
    assert protocol.goal_params("thread-1", objective="クチバシティへ") == {
        "threadId": "thread-1",
        "origin": "user",
        "objective": "クチバシティへ",
    }
    assert protocol.goal_params("thread-1", status="active") == {
        "threadId": "thread-1",
        "origin": "user",
        "status": "active",
    }
    assert protocol.goal_params("thread-1", status="paused") == {
        "threadId": "thread-1",
        "origin": "user",
        "status": "paused",
    }
    assert protocol.goal_params("thread-1") == {"threadId": "thread-1", "origin": "user"}


@pytest.mark.parametrize(
    "status", ["active", "paused", "blocked", "budgetLimited", "usageLimited", "complete"]
)
def test_goal_snapshot_preserves_reported_status_usage_and_budget(status: str) -> None:
    goal = protocol.goal_state(
        {
            "threadId": "thread-1",
            "objective": "クチバシティへ",
            "status": status,
            "tokensUsed": 100,
            "timeUsedSeconds": 17,
            "tokenBudget": 100,
        }
    )

    assert goal is not None
    assert goal.objective == "クチバシティへ"
    assert goal.status == status
    assert goal.tokens_used == 100
    assert goal.time_used_seconds == 17
    assert goal.token_budget == 100


def test_goal_snapshot_retains_null_budget_and_absence() -> None:
    goal = protocol.goal_state(
        {
            "threadId": "thread-1",
            "objective": "進めて",
            "status": "paused",
            "tokensUsed": 100,
            "timeUsedSeconds": 17,
            "tokenBudget": None,
        }
    )

    assert goal is not None
    assert goal.token_budget is None
    assert protocol.goal_state(None) is None


def test_goal_set_result_can_remain_limited() -> None:
    goal = protocol.goal_set_result(
        {
            "goal": {
                "objective": "進めて",
                "status": "budgetLimited",
                "tokensUsed": 100,
                "timeUsedSeconds": 17,
                "tokenBudget": 100,
            }
        }
    )

    assert goal.status == "budgetLimited"
    assert goal.tokens_used == 100
    assert goal.token_budget == 100


@pytest.mark.parametrize("response", [None, {}, {"goal": None}])
def test_goal_set_result_requires_a_reported_goal(response: "JsonValue") -> None:
    with pytest.raises(CodexConnectionError):
        protocol.goal_set_result(response)


@pytest.mark.parametrize("cleared", [True, False])
def test_goal_clear_result_preserves_whether_deletion_occurred(cleared: bool) -> None:
    assert protocol.goal_clear_result({"cleared": cleared}) is cleared


@pytest.mark.parametrize("response", [None, {}, {"cleared": 0}])
def test_goal_clear_result_requires_explicit_boolean(response: "JsonValue") -> None:
    with pytest.raises(CodexConnectionError):
        protocol.goal_clear_result(response)


@pytest.mark.parametrize(
    "override", [{"status": "unrecognized"}, {"tokensUsed": True}, {"tokenBudget": True}]
)
def test_goal_snapshot_does_not_guess_invalid_server_values(override: "JsonObject") -> None:
    with pytest.raises(CodexConnectionError):
        protocol.goal_state(
            {
                "threadId": "thread-1",
                "objective": "進めて",
                "status": "active",
                "tokensUsed": 100,
                "timeUsedSeconds": 17,
                "tokenBudget": None,
                **override,
            }
        )
