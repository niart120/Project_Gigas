"""Codex diagnostic pipes preserve failures without disrupting RPC output."""

import asyncio
import json
from pathlib import Path

from gigas.runtime.codex.transport import _ProcessProtocol
from gigas.storage.diagnostics import DiagnosticSession


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
