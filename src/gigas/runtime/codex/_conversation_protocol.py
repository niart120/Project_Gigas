"""Stateless conversation request construction and response decoding."""

import json
from typing import TYPE_CHECKING, Literal

from gigas.runtime.codex._execution_settings import config_overrides
from gigas.runtime.codex.connection import CodexConnectionError, read_play_instructions
from gigas.runtime.codex.models import GoalState

if TYPE_CHECKING:
    from gigas.runtime.codex.models import ExecutionSettings, ModelSelection, ThreadConfiguration
    from gigas.runtime.codex.transport import JsonObject, JsonValue


def _object(value: "JsonValue") -> "JsonObject":
    if isinstance(value, dict):
        return value
    raise CodexConnectionError("Codexの応答形式が不正")


def _text(value: "JsonValue") -> str:
    if isinstance(value, str) and value:
        return value
    raise CodexConnectionError("Codexの応答形式が不正")


def thread_start_params(
    configuration: "ThreadConfiguration",
    selection: "ModelSelection",
    server_config: "JsonValue",
    *,
    settings: "ExecutionSettings",
) -> "JsonObject":
    """Build one thread request using the prepared workspace and runtime configuration.

    Args:
        configuration: Prepared workspace, Project assignment, and Gigas-owned MCP overrides.
        selection: Confirmed model selection from the connection's catalog.
        server_config: Complete config/read response for the workspace.
        settings: Execution options captured before preparation began.

    Returns:
        Parameters for thread/start with an explicit Project assignment. The workspace
        is writable; inherited MCP servers are disabled before applying the application's
        endpoint overrides. Packaged instructions contain no conversation-specific path.

    Raises:
        CodexConnectionError: Runtime configuration or packaged instructions are invalid.
        OSError: Packaged play instructions cannot be read.
        UnicodeError: Packaged play instructions are not UTF-8.
    """
    servers = _object(_object(_object(server_config).get("config")).get("mcp_servers", {}))
    config: JsonObject = {}
    for name in servers:
        key = name if name.replace("_", "").isalnum() else json.dumps(name)
        config[f"mcp_servers.{key}.enabled"] = False
    config.update(configuration.mcp_config)
    config.update(config_overrides(settings))
    instructions = read_play_instructions()
    if not instructions.strip():
        raise CodexConnectionError("共通指示が空")
    cwd = str(configuration.cwd)
    return {
        "projectId": configuration.project_id,
        "cwd": cwd,
        "model": selection.model,
        "developerInstructions": instructions,
        "sandbox": "workspace-write",
        "approvalPolicy": "on-request",
        "approvalsReviewer": settings.approvals_reviewer,
        "config": config,
        "runtimeWorkspaceRoots": [cwd],
        "ephemeral": False,
    }


def validate_thread(
    result: "JsonValue",
    configuration: "ThreadConfiguration",
    selection: "ModelSelection",
    *,
    settings: "ExecutionSettings",
) -> str:
    """Return the thread ID after confirming the requested execution boundaries.

    Args:
        result: Complete thread/start response.
        configuration: Workspace and Project assignment used to construct the request.
        selection: Model selection submitted with the request.
        settings: The same immutable options used to construct the request.

    Returns:
        Confirmed thread identity, without adopting execution state.

    Raises:
        CodexConnectionError: The response omits or changes the requested settings.
    """
    response = _object(result)
    thread = _object(response.get("thread"))
    sandbox = _object(response.get("sandbox"))
    expected_sandbox: JsonObject = {
        "type": "workspaceWrite",
        "writableRoots": [],
        "networkAccess": False,
        "excludeTmpdirEnvVar": True,
        "excludeSlashTmp": True,
    }
    if (
        "projectId" not in thread
        or thread.get("projectId") != configuration.project_id
        or response.get("approvalPolicy") != "on-request"
        or response.get("approvalsReviewer") != settings.approvals_reviewer
        or response.get("model") != selection.model
        or response.get("cwd") != str(configuration.cwd)
        or response.get("runtimeWorkspaceRoots") != [str(configuration.cwd)]
        or any(sandbox.get(key) != value for key, value in expected_sandbox.items())
    ):
        raise CodexConnectionError("Codexの実行設定が要求と不一致")
    return _text(thread.get("id"))


def turn_start_params(thread_id: str, prompt: str, selection: "ModelSelection") -> "JsonObject":
    """Build turn/start input for both ordinary and additional messages.

    The server chooses whether to start a turn or intervene in the current one.
    The caller owns admission and validates nonempty user input before preparation.
    """
    return {
        "threadId": thread_id,
        "input": [{"type": "text", "text": prompt}],
        "model": selection.model,
        "effort": selection.effort,
    }


def turn_id(result: "JsonValue") -> str:
    """Return the accepted turn ID without adopting the response's execution status.

    Raises:
        CodexConnectionError: The turn/start response does not contain a turn ID.
    """
    return _text(_object(_object(result).get("turn")).get("id"))


def turn_notification(params: "JsonObject") -> "tuple[str, JsonValue]":
    """Return a turn notification's identity and reported status for display.

    Raises:
        CodexConnectionError: The notification does not contain a turn ID.
    """
    turn = _object(params.get("turn"))
    return _text(turn.get("id")), turn.get("status")


def goal_params(
    thread_id: str,
    *,
    objective: str | None = None,
    status: Literal["active", "paused"] | None = None,
) -> "JsonObject":
    """Build a user Goal mutation without rewriting omitted objective or budget.

    Creation supplies objective and active status; editing supplies only objective.
    Resume and explicit stop supply active and paused respectively. Goal removal
    uses the same thread/origin parameters without objective or status.
    """
    params: JsonObject = {"threadId": thread_id, "origin": "user"}
    if objective is not None:
        params["objective"] = objective
    if status is not None:
        params["status"] = status
    return params


def goal_state(value: "JsonValue") -> GoalState | None:
    """Decode the server's Goal without deriving limits or changing execution.

    Args:
        value: A public Goal object, or None for a missing or removed Goal.

    Returns:
        Immutable objective, status, usage and nullable budget exactly as reported.

    Raises:
        CodexConnectionError: The Goal has invalid public fields.
    """
    if value is None:
        return None
    data = _object(value)
    reported_status = data.get("status")
    if not isinstance(reported_status, str):
        raise CodexConnectionError("目標の応答形式が不正")
    match reported_status:
        case (
            "active" | "paused" | "blocked" | "usageLimited" | "budgetLimited" | "complete"
        ) as status:
            pass
        case _:
            raise CodexConnectionError("目標の応答形式が不正")
    tokens, seconds, budget = (
        data.get("tokensUsed"),
        data.get("timeUsedSeconds"),
        data.get("tokenBudget"),
    )
    if (
        type(tokens) is not int
        or type(seconds) is not int
        or (budget is not None and type(budget) is not int)
    ):
        raise CodexConnectionError("目標の利用量・予算が不正")
    return GoalState(_text(data.get("objective")), status, tokens, seconds, budget)


def goal_set_result(result: "JsonValue") -> GoalState:
    """Return the set response's reported Goal without changing a local snapshot.

    Raises:
        CodexConnectionError: The response does not contain a valid Goal.
    """
    goal = goal_state(_object(result).get("goal"))
    if goal is None:
        raise CodexConnectionError("目標の変更結果にGoalがありません")
    return goal


def goal_clear_result(result: "JsonValue") -> bool:
    """Return whether clear deleted a Goal; false does not promise a notification.

    Raises:
        CodexConnectionError: The response omits the boolean deletion result.
    """
    cleared = _object(result).get("cleared")
    if not isinstance(cleared, bool):
        raise CodexConnectionError("目標の削除結果の形式が不正")
    return cleared
