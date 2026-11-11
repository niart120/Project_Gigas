import asyncio
from collections.abc import Coroutine
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from gigas.runtime.codex.connection import CodexConnection, CodexConnectionError
from gigas.runtime.codex.models import ConnectionOptions, LoginEvent, SessionStatus
from gigas.runtime.codex.transport import (
    JsonObject,
    JsonValue,
    RpcId,
    RpcNotification,
    TransportClosed,
    TransportError,
    TransportEvent,
)

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch

    from gigas.runtime.codex.models import ConnectionEvent


def _model(name: str, *, default: bool = False) -> JsonObject:
    return {
        "id": name,
        "model": name,
        "isDefault": default,
        "defaultReasoningEffort": "medium",
        "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "medium"}],
    }


class FakePeer:
    """A scripted RPC boundary; transport parsing and processes are tested separately."""

    def __init__(self) -> None:
        """Prepare a successful catalog and an empty notification queue."""
        self.calls: list[tuple[str, JsonObject | None]] = []
        self.replies: list[tuple[RpcId, JsonValue]] = []
        self.rejections: list[tuple[RpcId, int, str]] = []
        self.events: asyncio.Queue[TransportEvent] = asyncio.Queue()
        self.started = False
        self.closed = False
        self.authenticated = True
        self.failure: str | None = None
        self.responses: dict[str, JsonValue] = {}

    async def start(self) -> None:
        """Record process startup."""
        self.started = True

    async def request(self, method: str, params: JsonObject | None) -> JsonValue:
        """Return protocol fixtures, preserving observable call ordering."""
        self.calls.append((method, params))
        if method == self.failure:
            message = "PRIVATE-UPSTREAM-TEXT"
            raise TransportError(message)
        if method in self.responses:
            return self.responses[method]
        if method == "initialize":
            return {"userAgent": "codex/0.167.0", "platformOs": "windows"}
        if method == "account/read":
            return {
                "account": {"type": "chatgpt"} if self.authenticated else None,
                "requiresOpenaiAuth": True,
            }
        if method == "config/read":
            return {"config": {"mcp_servers": {"existing": {"enabled": True}}}}
        if method == "model/list":
            if params and params.get("cursor") == "page-2":
                return {"data": [_model("catalog-default", default=True)], "nextCursor": None}
            return {"data": [_model("other")], "nextCursor": "page-2"}
        if method == "thread/start":
            assert params is not None
            return {
                "thread": {"id": "thread-1"},
                "approvalPolicy": params["approvalPolicy"],
                "approvalsReviewer": params["approvalsReviewer"],
                "model": params["model"],
                "cwd": params["cwd"],
                "sandbox": {"type": "readOnly"},
            }
        if method == "turn/start":
            return {"turn": {"id": "turn-1", "status": "inProgress", "items": []}}
        if method == "account/login/start":
            return {"type": "chatgpt", "loginId": "login-1", "authUrl": "https://auth.openai.com/"}
        if method == "account/login/cancel":
            return {"status": "canceled"}
        return {}

    async def notify(self, method: str, params: JsonObject | None = None) -> None:
        """Record client notifications."""
        self.calls.append((method, params))

    async def respond(self, request_id: RpcId, result: JsonValue) -> None:
        """Record server-request answers."""
        self.replies.append((request_id, result))

    async def reject(self, request_id: RpcId, code: int, message: str) -> None:
        """Record explicit protocol rejection."""
        self.rejections.append((request_id, code, message))

    async def next_event(self) -> TransportEvent:
        """Wait for one scripted notification or request."""
        return await self.events.get()

    async def close(self) -> None:
        """Publish the terminal event without retaining a worker."""
        self.closed = True
        self.events.put_nowait(TransportClosed("closed"))


def _run(scenario: Coroutine[object, object, None]) -> None:
    async def bounded() -> None:
        async with asyncio.timeout(5):
            await scenario

    asyncio.run(bounded())


async def _events_processed() -> None:
    # FakePeer RPCs finish immediately; let the event consumer drain its queue.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def test_open_validates_catalog_and_delivers_packaged_instructions(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        assert peer.started
        assert [method for method, _ in peer.calls] == [
            "initialize",
            "initialized",
            "account/read",
            "config/read",
            "model/list",
            "model/list",
        ]
        status = [event for event in events if isinstance(event, SessionStatus)][-1]
        assert (status.phase, status.model, status.effort, status.authenticated) == (
            "ready",
            "catalog-default",
            "medium",
            True,
        )
        await connection.send("確認だけ")
        start = next(params for method, params in peer.calls if method == "thread/start")
        assert start is not None
        assert start["developerInstructions"] == resources.files("gigas.prompts").joinpath(
            "play.md"
        ).read_text(encoding="utf-8")
        assert start["sandbox"] == "read-only"
        assert start["approvalsReviewer"] == "user"
        assert start["approvalPolicy"] == "on-request"
        assert start["ephemeral"] is True
        assert start["config"] == {"mcp_servers.existing.enabled": False}
        turn = next(params for method, params in peer.calls if method == "turn/start")
        assert turn == {
            "threadId": "thread-1",
            "input": [{"type": "text", "text": "確認だけ"}],
            "model": "catalog-default",
            "effort": "medium",
        }
        await connection.close()
        assert peer.closed

    _run(scenario())


def test_login_is_explicit_and_completion_rechecks_account(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.authenticated = False
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        await connection.open()
        assert not any(method == "account/login/start" for method, _ in peer.calls)
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "login_required"
        with pytest.raises(CodexConnectionError):
            await connection.send("未認証")
        await connection.login()
        assert any(isinstance(event, LoginEvent) for event in events)
        assert not any(method == "thread/start" for method, _ in peer.calls)
        peer.authenticated = True
        peer.events.put_nowait(
            RpcNotification("account/login/completed", {"loginId": "login-1", "success": True})
        )
        await _events_processed()
        assert isinstance(events[-1], SessionStatus)
        assert events[-1].phase == "ready"
        assert sum(method == "account/read" for method, _ in peer.calls) == 2
        await connection.close()

    _run(scenario())


@pytest.mark.parametrize(("model", "effort"), [("missing", ""), ("other", "ultra")])
def test_invalid_model_or_effort_never_starts_a_thread(
    tmp_path: Path, model: str, effort: str
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        connection = CodexConnection(
            peer, ConnectionOptions(tmp_path, model, effort), lambda _: None
        )
        with pytest.raises(CodexConnectionError):
            await connection.open()
        assert not any(method == "thread/start" for method, _ in peer.calls)
        assert peer.closed

    _run(scenario())


@pytest.mark.parametrize("contents", ["", "   "])
def test_empty_packaged_instructions_do_not_start_a_thread(
    tmp_path: Path, monkeypatch: "MonkeyPatch", contents: str
) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _: None)
        await connection.open()
        monkeypatch.setattr(
            "gigas.runtime.codex.connection.read_play_instructions", lambda: contents
        )
        with pytest.raises(CodexConnectionError):
            await connection.send("開始")
        assert not any(method == "thread/start" for method, _ in peer.calls)
        await connection.close()

    _run(scenario())


def test_initialization_failure_closes_peer_without_leaking_diagnostics(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.failure = "initialize"
        events: list[ConnectionEvent] = []
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), events.append)
        with pytest.raises(CodexConnectionError) as error:
            await connection.open()
        assert "PRIVATE" not in str(error.value)
        assert "PRIVATE" not in repr(events)
        assert peer.closed

    _run(scenario())


def test_unimplemented_auth_method_is_explicit(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        connection = CodexConnection(
            peer, ConnectionOptions(tmp_path, auth_method="api_key"), lambda _: None
        )
        with pytest.raises(CodexConnectionError):
            await connection.open()
        assert not any(method == "account/login/start" for method, _ in peer.calls)

    _run(scenario())


def test_unreadable_instructions_never_reach_thread_start(
    tmp_path: Path, monkeypatch: "MonkeyPatch"
) -> None:
    def unavailable() -> str:
        message = "PRIVATE-RESOURCE-PATH"
        raise OSError(message)

    async def scenario() -> None:
        peer = FakePeer()
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _: None)
        await connection.open()
        monkeypatch.setattr("gigas.runtime.codex.connection.read_play_instructions", unavailable)
        with pytest.raises(CodexConnectionError) as error:
            await connection.send("開始")
        assert "PRIVATE" not in str(error.value)
        assert not any(method == "thread/start" for method, _ in peer.calls)
        await connection.close()

    _run(scenario())


def test_unconfirmed_policy_does_not_start_a_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        peer.responses["thread/start"] = {
            "thread": {"id": "thread-1"},
            "model": "catalog-default",
            "approvalPolicy": "on-request",
            "approvalsReviewer": "auto_review",
            "sandbox": {"type": "readOnly"},
        }
        connection = CodexConnection(peer, ConnectionOptions(tmp_path), lambda _: None)
        await connection.open()
        with pytest.raises(CodexConnectionError):
            await connection.send("開始")
        assert not any(method == "turn/start" for method, _ in peer.calls)
        await connection.close()

    _run(scenario())
