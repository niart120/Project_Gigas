import asyncio
import json
from pathlib import Path
from threading import Event

import httpx
from pytestqt.qtbot import QtBot

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.runtime.codex.models import (
    ConnectionOptions,
    DialogueEvent,
    ExecutionSettings,
    GoalState,
)
from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcNotification
from gigas.services.conversations import StoredConversationTarget
from tests.integration.peers.codex_resume import ResumePeer
from tests.integration.test_codex_service import _call_game_tool, _mcp_endpoint
from tests.integration.test_conversation_selection import _saved
from tests.integration.test_conversation_switching import _release_after, _session
from tests.integration.test_observation_storage import sample_frame


class _WaitingResume(ResumePeer):
    """Hold the public resume reply while notifications and MCP keep running."""

    def __init__(self) -> None:
        super().__init__("project-1")
        self.entered, self.release = Event(), Event()

    async def request(self, method: str, params: JsonObject | None) -> JsonValue:
        result = await super().request(method, params)
        if method == "thread/resume":
            self.entered.set()
            assert await asyncio.to_thread(self.release.wait, 10)
        return result


def test_early_resume_notifications_and_mcp_observation_share_saved_records_before_reply(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    peer = _WaitingResume()
    _saved(peer, cwd)
    frame = sample_frame()
    reader = CaptureReader(
        lambda: CaptureSnapshot(phase="streaming", frame=frame, generation=frame.generation)
    )
    with _session(qtbot, tmp_path, peer, reader) as s, _release_after(peer.release):
        assert not s.manage("select", lambda: s.service.select_conversation("stored-1")).error
        s.service.send("after resume")
        qtbot.waitUntil(peer.entered.is_set)
        total: dict[str, int] = {
            "inputTokens": 800,
            "cachedInputTokens": 640,
            "cacheWriteInputTokens": 32,
            "outputTokens": 200,
            "reasoningOutputTokens": 120,
            "totalTokens": 1000,
        }
        usage: JsonObject = {
            "threadId": "stored-1",
            "turnId": "previous-turn",
            "tokenUsage": {
                "total": {**total, "unexpected": "private usage text"},
                "last": {"totalTokens": 100},
                "modelContextWindow": 128_000,
            },
        }
        updates: list[JsonObject] = [
            {**usage, "threadId": "unrelated"},
            {**usage, "tokenUsage": {"total": {"totalTokens": True}}},
            usage,
            usage,
        ]
        for update in updates:
            s.notify(RpcNotification("thread/tokenUsage/updated", update))
        s.notify(
            RpcNotification(
                "turn/started",
                {
                    "threadId": "stored-1",
                    "turn": {"id": "early", "status": "inProgress"},
                },
            )
        )
        s.notify(
            RpcNotification(
                "item/completed",
                {
                    "threadId": "stored-1",
                    "turnId": "early",
                    "item": {
                        "id": "progress",
                        "type": "agentMessage",
                        "text": "early fact",
                        "phase": "commentary",
                    },
                },
            )
        )
        qtbot.waitUntil(
            lambda: any(
                isinstance(item.event, DialogueEvent)
                and item.event.kind == "message"
                and item.event.payload.get("text") == "early fact"
                for item in s.events
            )
        )
        params = next(p for m, p in peer.calls if m == "thread/resume")
        assert params is not None
        url, headers = _mcp_endpoint(params)
        with httpx.Client(headers=headers, trust_env=False) as client:
            observed = _call_game_tool(client, url, "observe").json()["result"]
        assert not observed.get("isError", False)
        observation_id = json.loads(observed["content"][1]["text"])["observation_id"]
        records = cwd / "records" / "stored-1"
        # RecordingService owns asynchronous saves. This barrier waits for its queue,
        # without closing the endpoint or completing the held resume request.
        saved = Event()

        async def drain_records() -> None:
            recording = s.service.codex._resources._recording
            assert recording is not None
            await recording.drain()
            saved.set()

        def start_drain() -> None:
            s.service._track(asyncio.create_task(drain_records()))

        s.service._enqueue(start_drain)
        qtbot.waitUntil(saved.is_set)
        events = [
            json.loads(line)
            for line in (records / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        usage_rows = [e for e in events if e["type"] == "codex.token_usage_updated"]
        assert [e["payload"] for e in usage_rows] == [
            {"turn_id": "previous-turn", "total": total}
        ] * 2
        assert all(e["schema_version"] == 1 and e["recorded_at"].endswith("Z") for e in usage_rows)
        assert any(
            e["type"] == "dialogue.message" and e["payload"]["text"] == "early fact" for e in events
        )
        assert any(
            e["type"] == "observation.recorded" and e["payload"]["observation_id"] == observation_id
            for e in events
        )
        assert (records / "observations" / f"{observation_id}.png").exists()
        assert not any(m == "turn/start" for m, _ in peer.calls)
        assert s.command("select_model", lambda: s.service.select_model("other", "low")).error
        assert s.command("goal_clear", s.service.clear_goal).error
        peer.release.set()
        qtbot.waitUntil(lambda: any(r.operation == "send" for r in s.results))
        assert not next(r.error for r in s.results if r.operation == "send")


def test_disconnect_drains_sent_resume_without_submitting_body_and_retains_target(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    peer = _WaitingResume()
    _saved(peer, cwd)
    with _session(qtbot, tmp_path, peer) as s, _release_after(peer.release):
        assert not s.manage("select", lambda: s.service.select_conversation("stored-1")).error
        target = s.service.selected_target
        assert isinstance(target, StoredConversationTarget)
        s.service.send("not submitted")
        qtbot.waitUntil(peer.entered.is_set)
        s.notify(
            RpcNotification(
                "thread/goal/updated",
                {
                    "threadId": "stored-1",
                    "goal": {
                        "objective": "early fact",
                        "status": "paused",
                        "tokensUsed": 41,
                        "timeUsedSeconds": 3,
                    },
                },
            )
        )
        s.service.disconnect_runtime()
        qtbot.waitUntil(lambda: peer.closed)
        assert not any(r.operation == "disconnect" for r in s.results)
        assert s.service.codex._resources.records_bound
        peer.release.set()
        qtbot.waitUntil(lambda: any(r.operation == "disconnect" for r in s.results))
        assert not next(r.error for r in s.results if r.operation == "disconnect")
        assert not any(m == "turn/start" for m, _ in peer.calls)
        assert s.service.selected_target is target
        assert s.service.codex._selection.goal == GoalState("early fact", "paused", 41, 3)
        assert not s.service.codex._resources.records_bound
        # Reconnection constructs a fresh loader while retaining the same saved ID.
        s.peer = resumed = ResumePeer("project-1")
        resumed.responses["project/read"] = peer.responses["project/read"]
        s.service.set_execution_settings(ExecutionSettings(False, False, "user"))
        assert not s.command(
            "connect",
            lambda: s.service.connect_runtime(
                ConnectionOptions(
                    tmp_path / "runtime", execution_settings=ExecutionSettings(False, False, "user")
                )
            ),
        ).error
        assert not s.command("send", lambda: s.service.send("explicit retry")).error
        params = next(p for m, p in resumed.calls if m == "thread/resume")
        assert params is not None
        assert params["threadId"] == target.thread_id
        assert params["approvalsReviewer"] == "user"
        assert isinstance(params["config"], dict)
        assert params["config"]["web_search"] == "disabled"
        assert not any(m == "thread/start" for m, _ in resumed.calls)
