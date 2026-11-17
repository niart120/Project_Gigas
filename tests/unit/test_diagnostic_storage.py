"""Exercise queued file persistence, retention locks and failure recovery."""

import json
import logging
from pathlib import Path

from gigas.storage.diagnostics import DiagnosticSession, _FileHandler, _lock, clean_old_runs


def test_close_drains_queue_and_preserves_exception(tmp_path: Path) -> None:
    failures: list[str] = []
    session = DiagnosticSession(
        tmp_path / "settings.toml", lambda _, reason: failures.append(reason)
    )
    session.start()
    try:
        logger = logging.getLogger("gigas.test")
        for number in range(100):
            logger.info("operation finished", extra={"event": "operation.done", "number": number})
        try:
            int("invalid")
        except ValueError:
            logger.exception("failed with Bearer private-value")
    finally:
        session.close()
    rows = [json.loads(line) for line in session.path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 101
    assert rows[99]["number"] == 99
    assert "ValueError" in rows[-1]["message"]
    assert "private-value" not in session.path.read_text(encoding="utf-8")
    assert failures == []


def test_retention_keeps_active_and_latest_inactive_runs(tmp_path: Path) -> None:
    runs = []
    for number in range(13):
        path = tmp_path / f"20260101T0000{number:02d}Z_123"
        path.mkdir()
        (path / ".lock").touch()
        (path / "gigas.jsonl").write_text("{}\n")
        runs.append(path)
    unrelated = tmp_path / "user-notes"
    unrelated.mkdir()
    with _lock(runs[0] / ".lock"):
        clean_old_runs(tmp_path)
        assert runs[0].exists()
        assert not runs[1].exists()
        assert not runs[2].exists()
        assert all(path.exists() for path in runs[3:])
    clean_old_runs(tmp_path)
    assert not runs[0].exists()
    assert unrelated.exists()


def test_initialization_failure_is_reported(tmp_path: Path) -> None:
    (tmp_path / "logs").write_text("not a directory")
    failures: list[tuple[Path, str]] = []
    session = DiagnosticSession(tmp_path / "settings.toml", lambda p, r: failures.append((p, r)))
    session.start()
    session.close()
    assert len(failures) == 1
    assert failures[0][0] == session.path


def test_rotation_failure_notifies_once_then_records_recovery(tmp_path: Path) -> None:
    failures: list[str] = []
    path = tmp_path / "gigas.jsonl"
    handler = _FileHandler(path, failures.append)
    handler.maxBytes = 1
    handler.backupCount = 1
    # A directory blocks the normal rotation target without mocking filesystem I/O.
    blocked = tmp_path / "gigas.jsonl.1"
    blocked.mkdir()
    record = logging.LogRecord("gigas.test", logging.ERROR, __file__, 1, "failed", (), None)
    try:
        handler.handle(record)
        handler.handle(record)
        assert len(failures) == 1
        blocked.rmdir()
        handler.handle(record)
    finally:
        handler.close()
    all_text = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("gigas.jsonl*"))
    assert "logging.recovered" in all_text
    assert "failed" in all_text
