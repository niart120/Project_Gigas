import asyncio
from pathlib import Path

import pytest
from tests.unit.test_codex_management import _client

from gigas.runtime.codex.connection import CodexConnectionError
from gigas.runtime.codex.models import GoalState
from gigas.runtime.codex.transport import JsonValue, RpcError


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            {
                "goal": {
                    "objective": "全国図鑑を入手",
                    "status": "active",
                    "tokensUsed": 30,
                    "timeUsedSeconds": 40,
                    "tokenBudget": 1000,
                }
            },
            GoalState("全国図鑑を入手", "active", 30, 40, 1000),
        ),
        ({"goal": None}, None),
        ({}, None),
    ],
)
def test_goal_get_uses_saved_id_without_execution_readiness_or_workspace(
    tmp_path: Path, response: JsonValue, expected: GoalState | None
) -> None:
    async def scenario() -> None:
        missing = tmp_path / "missing-workspace"
        async with _client(tmp_path) as (client, peer):
            peer.responses["thread/read"] = {"thread": {"cwd": str(missing)}}
            peer.responses["thread/goal/get"] = response
            assert await client.get_goal("stored-thread") == expected
            assert [(m, p) for m, p in peer.calls if m.startswith(("thread/", "turn/"))] == [
                ("thread/goal/get", {"threadId": "stored-thread"})
            ]
            assert not missing.exists()

    asyncio.run(scenario())


@pytest.mark.parametrize("cleared", [True, False])
def test_goal_clear_returns_deletion_result_without_resume_stop_or_notification_wait(
    tmp_path: Path, cleared: bool
) -> None:
    async def scenario() -> None:
        missing = tmp_path / "missing-workspace"
        async with _client(tmp_path) as (client, peer):
            peer.responses["thread/read"] = {"thread": {"cwd": str(missing)}}
            peer.responses["thread/goal/clear"] = {"cleared": cleared}
            assert await client.clear_goal("stored-thread") is cleared
            assert [(m, p) for m, p in peer.calls if m.startswith(("thread/", "turn/"))] == [
                ("thread/goal/clear", {"threadId": "stored-thread", "origin": "user"})
            ]
            assert not missing.exists()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("method", "response"),
    [("get", None), ("get", {"goal": False}), ("clear", {}), ("clear", {"cleared": 1})],
)
def test_goal_management_rejects_invalid_response_without_further_requests(
    tmp_path: Path, method: str, response: JsonValue
) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (client, peer):
            rpc = f"thread/goal/{method}"
            peer.responses[rpc] = response
            operation = (
                client.get_goal("stored-thread")
                if method == "get"
                else client.clear_goal("stored-thread")
            )
            with pytest.raises(CodexConnectionError):
                await operation
            assert [m for m, _ in peer.calls if m.startswith(("thread/", "turn/"))] == [rpc]

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["get", "clear"])
def test_goal_management_propagates_rpc_failure_without_retry(tmp_path: Path, method: str) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as (client, peer):
            rpc = f"thread/goal/{method}"
            peer.rpc_errors[rpc] = -32602
            operation = (
                client.get_goal("stored-thread")
                if method == "get"
                else client.clear_goal("stored-thread")
            )
            with pytest.raises(RpcError, match="-32602"):
                await operation
            expected: dict[str, JsonValue] = {"threadId": "stored-thread"}
            if method == "clear":
                expected["origin"] = "user"
            assert [(m, p) for m, p in peer.calls if m.startswith(("thread/", "turn/"))] == [
                (rpc, expected)
            ]

    asyncio.run(scenario())
