import json
from typing import TYPE_CHECKING, Literal

import pytest
from tests.unit.test_codex_connection import FakePeer
from tests.unit.test_conversation_protocol import _thread_result

from gigas.runtime.codex import _conversation_protocol as protocol
from gigas.runtime.codex import launch
from gigas.runtime.codex.connection import CodexConnectionError
from gigas.runtime.codex.models import (
    ConnectionOptions,
    ExecutionSettings,
    ModelSelection,
    ThreadConfiguration,
)

if TYPE_CHECKING:
    from pathlib import Path

    from gigas.runtime.codex.transport import JsonObject


@pytest.mark.parametrize("web", [True, False])
@pytest.mark.parametrize("memory", [True, False])
@pytest.mark.parametrize("reviewer", ["auto_review", "user"])
def test_launch_and_thread_use_execution_settings_without_suppressing_standard_instructions(
    tmp_path: "Path",
    monkeypatch: pytest.MonkeyPatch,
    web: bool,
    memory: bool,
    reviewer: Literal["auto_review", "user"],
) -> None:
    settings = ExecutionSettings(web, memory, reviewer)
    overrides: dict[str, object] = {}

    def transport(
        command: list[str], cwd: "Path", env: dict[str, str], *, unset_env: tuple[str, ...] = ()
    ) -> FakePeer:
        assert cwd == tmp_path
        assert isinstance(env, dict)
        assert unset_env
        for index, arg in enumerate(command):
            if arg == "--config":
                key, value = command[index + 1].split("=", 1)
                overrides[key] = json.loads(value)
        return FakePeer()

    monkeypatch.setattr(launch, "StdioTransport", transport)
    launch.create_connection(
        ConnectionOptions(tmp_path, execution_settings=settings), lambda _event: None
    )
    params = protocol.thread_start_params(
        ThreadConfiguration(tmp_path),
        ModelSelection((), "selected-model", "high"),
        {"config": {"mcp_servers": {}}},
        settings=settings,
    )
    expected: JsonObject = {
        "web_search": "live" if web else "disabled",
        "features.memories": memory,
        "memories.generate_memories": memory,
        "memories.use_memories": memory,
        "memories.disable_on_external_context": False,
        "features.apps": False,
        "features.plugins": False,
        "features.hooks": False,
        "features.codex_hooks": False,
        "features.plugin_hooks": False,
        "notify": [],
        "features.default_mode_request_user_input": True,
        "tools.update_plan.enabled": True,
        "approval_policy": "on-request",
        "approvals_reviewer": reviewer,
        "sandbox_mode": "workspace-write",
        "sandbox_workspace_write.writable_roots": [],
        "sandbox_workspace_write.network_access": False,
        "sandbox_workspace_write.exclude_tmpdir_env_var": True,
        "sandbox_workspace_write.exclude_slash_tmp": True,
    }
    assert overrides == expected
    assert params["config"] == {
        **expected,
        "model_reasoning_effort": "high",
        "model_reasoning_summary": "auto",
    }
    assert params["approvalPolicy"] == "on-request"
    assert params["approvalsReviewer"] == reviewer
    assert params["sandbox"] == "workspace-write"


@pytest.mark.parametrize("reviewer", ["auto_review", "user"])
def test_thread_confirms_the_requested_reviewer_and_rejects_a_different_one(
    tmp_path: "Path", reviewer: Literal["auto_review", "user"]
) -> None:
    settings = ExecutionSettings(approvals_reviewer=reviewer)
    configuration = ThreadConfiguration(tmp_path)
    selection = ModelSelection((), "selected-model", "high")
    result = {**_thread_result(configuration), "approvalsReviewer": reviewer}

    assert protocol.validate_thread(result, configuration, selection, settings=settings) == (
        "thread-1"
    )

    result["approvalsReviewer"] = "user" if reviewer == "auto_review" else "auto_review"
    with pytest.raises(CodexConnectionError, match="実行設定"):
        protocol.validate_thread(result, configuration, selection, settings=settings)


def test_default_execution_settings_enable_web_memory_and_delegated_approval(
    tmp_path: "Path",
) -> None:
    assert ConnectionOptions(tmp_path).execution_settings == ExecutionSettings(
        True, True, "auto_review"
    )
