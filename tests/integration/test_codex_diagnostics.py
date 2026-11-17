"""Codex diagnostic pipes preserve failures without disrupting RPC output."""

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

from gigas.runtime.codex.transport import RpcNotification, _ProcessProtocol
from gigas.storage.diagnostics import DiagnosticSession
from tests.integration.test_conversation_control import goal
from tests.integration.test_conversation_execution import Conversation

if TYPE_CHECKING:
    from gigas.runtime.codex.transport import JsonObject


def test_turn_failure_and_goal_recovery_leave_correlated_records(tmp_path: Path) -> None:
    session = DiagnosticSession(tmp_path / "settings.toml", lambda *_: None)
    session.start()

    async def scenario() -> None:
        (tmp_path / "project").mkdir()
        c = Conversation(tmp_path / "project")
        await c.connection.open()
        try:
            await c.execution.send("private conversation text")
            goal(c, "active", "private objective", tokens=1)
            goal(c, "active", "private objective", tokens=2)
            error: JsonObject = {
                "message": "Bad Request",
                "additionalDetails": "Bearer private-token",
            }
            c.execution.receive(
                RpcNotification(
                    "error",
                    {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "error": error,
                        "willRetry": False,
                    },
                )
            )
            c.execution.receive(
                RpcNotification(
                    "turn/completed",
                    {
                        "threadId": "thread-1",
                        "turn": {
                            "id": "turn-1",
                            "status": "failed",
                            "error": error,
                            "items": [{"text": "private conversation text"}],
                        },
                    },
                )
            )
            goal(c, "blocked", "private objective")
            goal(c, "active", "private objective")
        finally:
            await c.close()

    try:
        asyncio.run(scenario())
    finally:
        session.close()
    contents = session.path.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in contents.splitlines()]
    lifecycle = [r for r in rows if r.get("event") == "codex.turn_completed"]
    assert len(lifecycle) == 1
    assert lifecycle[0]["thread_id"] == "thread-1"
    assert lifecycle[0]["turn_id"] == "turn-1"
    assert lifecycle[0]["level"] == "ERROR"
    assert lifecycle[0]["details"]["message"] == "Bad Request"
    errors = [r for r in rows if r.get("event") == "codex.turn_error"]
    assert len(errors) == 1
    assert errors[0]["will_retry"] is False
    changes = [r for r in rows if r.get("event") == "codex.goal_changed"]
    assert [r["status"] for r in changes] == ["active", "blocked", "active"]
    assert changes[1]["previous_status"] == "active"
    assert "private conversation text" not in contents
    assert "private objective" not in contents
    assert "private-token" not in contents


def test_stderr_split_utf8_long_lines_and_eof_are_saved(tmp_path: Path) -> None:
    failures: list[str] = []
    session = DiagnosticSession(tmp_path / "settings.toml", lambda _, r: failures.append(r))
    session.start()

    async def scenario() -> None:
        protocol = _ProcessProtocol()
        text = (
            json.dumps(
                {"level": "ERROR", "message": "日本語の失敗", "access_token": "private-value"},
                ensure_ascii=False,
            ).encode("utf-8")
            + b"\n"
        )
        for byte in text:
            protocol.pipe_data_received(2, bytes([byte]))
        protocol.pipe_data_received(2, b"x" * 300_000)
        protocol.pipe_data_received(1, b'{"result":true}\n')
        assert await protocol.stdout.readline() == b'{"result":true}\n'
        protocol.pipe_data_received(2, b"\nlast warning Bearer secret-tail")
        protocol.pipe_connection_lost(2, None)

    try:
        asyncio.run(scenario())
    finally:
        session.close()
    contents = session.path.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in contents.splitlines()]
    assert len(rows) == 3
    assert rows[0]["level"] == "ERROR"
    assert rows[0]["details"]["message"] == "日本語の失敗"
    assert len(rows[1]["details"]) == 300_000
    assert "last warning" in rows[2]["details"]
    assert "private-value" not in contents
    assert "secret-tail" not in contents
    assert failures == []
