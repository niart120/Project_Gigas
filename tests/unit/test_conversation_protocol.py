from typing import TYPE_CHECKING

import pytest

from gigas.runtime.codex import _conversation_protocol as protocol
from gigas.runtime.codex.connection import CodexConnectionError
from gigas.runtime.codex.models import ModelSelection, ThreadConfiguration

if TYPE_CHECKING:
    from pathlib import Path

    from gigas.runtime.codex.transport import JsonObject, JsonValue


def test_thread_preparation_limits_workspace_and_replaces_inherited_mcp(tmp_path: "Path") -> None:
    configuration = ThreadConfiguration(
        tmp_path,
        {
            "mcp_servers.game.enabled": True,
            "mcp_servers.game.url": "http://127.0.0.1:3210/mcp",
            "mcp_servers.game.http_headers": {"Authorization": "Bearer private-value"},
        },
    )
    selection = ModelSelection((), "selected-model", "high")
    server_config: JsonObject = {
        "config": {"mcp_servers": {"existing": {}, "name.with.dot": {}, "game": {}}}
    }

    params = protocol.thread_start_params(configuration, selection, server_config)

    assert params["cwd"] == str(tmp_path / "memory")
    assert params["runtimeWorkspaceRoots"] == [str(tmp_path / "memory")]
    assert params["model"] == "selected-model"
    assert params["ephemeral"] is False
    assert params["sandbox"] == "workspace-write"
    assert params["approvalPolicy"] == "on-request"
    assert params["approvalsReviewer"] == "user"
    assert params["config"] == {
        "mcp_servers.existing.enabled": False,
        'mcp_servers."name.with.dot".enabled': False,
        **configuration.mcp_config,
        "sandbox_workspace_write.writable_roots": [],
        "sandbox_workspace_write.network_access": False,
        "sandbox_workspace_write.exclude_tmpdir_env_var": True,
        "sandbox_workspace_write.exclude_slash_tmp": True,
    }
    instructions = params["developerInstructions"]
    assert isinstance(instructions, str)
    assert protocol.read_play_instructions() in instructions
    assert str(tmp_path) in instructions
    assert "private-value" not in instructions
    assert server_config == {
        "config": {"mcp_servers": {"existing": {}, "name.with.dot": {}, "game": {}}}
    }


def _thread_result(configuration: ThreadConfiguration) -> "JsonObject":
    return {
        "thread": {"id": "thread-1"},
        "model": "selected-model",
        "cwd": str(configuration.cwd / "memory"),
        "approvalPolicy": "on-request",
        "approvalsReviewer": "user",
        "sandbox": {
            "type": "workspaceWrite",
            "writableRoots": [],
            "networkAccess": False,
            "excludeTmpdirEnvVar": True,
            "excludeSlashTmp": True,
        },
    }


def test_confirmed_thread_returns_only_its_identity(tmp_path: "Path") -> None:
    configuration = ThreadConfiguration(tmp_path)
    selection = ModelSelection((), "selected-model", "high")

    assert protocol.validate_thread(_thread_result(configuration), configuration, selection) == (
        "thread-1"
    )


@pytest.mark.parametrize(
    "override",
    [
        {"approvalPolicy": "never"},
        {"approvalsReviewer": "auto_review"},
        {"model": "different-model"},
        {"cwd": "other-directory"},
        {"sandbox": {"type": "dangerFullAccess"}},
        {"thread": {"id": ""}},
    ],
)
def test_thread_rejects_unconfirmed_configuration(tmp_path: "Path", override: "JsonObject") -> None:
    configuration = ThreadConfiguration(tmp_path)
    selection = ModelSelection((), "selected-model", "high")

    with pytest.raises(CodexConnectionError):
        protocol.validate_thread(
            {**_thread_result(configuration), **override}, configuration, selection
        )


def test_ordinary_message_uses_thread_input_and_selected_model_without_a_target_turn() -> None:
    assert protocol.turn_start_params(
        "thread-1", "右へ進んで\n次は左へ", ModelSelection((), "selected-model", "high")
    ) == {
        "threadId": "thread-1",
        "input": [{"type": "text", "text": "右へ進んで\n次は左へ"}],
        "model": "selected-model",
        "effort": "high",
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
