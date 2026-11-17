"""Exercise queued file persistence, retention locks and failure recovery."""

import json
import logging
from pathlib import Path

import pytest

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


def test_initialization_failure_does_not_write_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "logs").write_text("blocked")
    session = DiagnosticSession(tmp_path / "settings.toml", lambda *_: None)
    session.start()
    try:
        logging.getLogger("gigas.test").error("fallback Bearer hidden-credential")
    finally:
        session.close()
    output = capsys.readouterr().err
    assert output == ""


def test_library_success_traffic_is_not_recorded_but_warnings_are(tmp_path: Path) -> None:
    session = DiagnosticSession(tmp_path / "settings.toml", lambda *_: None)
    session.start()
    try:
        logging.getLogger("httpx").info("successful request")
        logging.getLogger("httpx").warning("upstream failure")
        logging.getLogger("gigas.test").info("operation completed")
    finally:
        session.close()
    rows = [json.loads(line) for line in session.path.read_text(encoding="utf-8").splitlines()]
    assert [r["message"] for r in rows] == ["upstream failure", "operation completed"]


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


def test_write_failure_redacts_notice_and_recovers_on_next_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_write(_text: str) -> int:
        message = "disk full Bearer private-disk-token"
        raise OSError(message)

    failures: list[str] = []
    path = tmp_path / "gigas.jsonl"
    handler = _FileHandler(path, failures.append)
    handler.maxBytes = 0
    original = handler.stream
    record = logging.LogRecord("gigas.test", logging.ERROR, __file__, 1, "diagnostic", (), None)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(original, "write", fail_write)
            handler.handle(record)
            handler.handle(record)
        assert len(failures) == 1
        assert "private-disk-token" not in failures[0]
        handler.handle(record)
    finally:
        handler.close()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[1]["event"] == "logging.recovered"


def test_rotation_retains_current_and_nine_backups(tmp_path: Path) -> None:
    handler = _FileHandler(tmp_path / "gigas.jsonl", lambda _: pytest.fail("unexpected failure"))
    assert handler.maxBytes == 10 * 1024 * 1024
    assert handler.backupCount == 9
    handler.maxBytes = 256
    try:
        for number in range(30):
            record = logging.LogRecord("gigas.test", logging.INFO, __file__, 1, "x" * 100, (), None)
            record.number = number
            handler.handle(record)
    finally:
        handler.close()
    files = list(tmp_path.glob("gigas.jsonl*"))
    assert len(files) == 10
    assert {p.name for p in files} == {"gigas.jsonl", *(f"gigas.jsonl.{n}" for n in range(1, 10))}
    rows = [json.loads(line) for p in files for line in p.read_text(encoding="utf-8").splitlines()]
    assert sorted(r["number"] for r in rows) == list(range(20, 30))


def test_failed_deletion_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = tmp_path / "20260101T000000Z_123"
    run.mkdir()
    (run / ".lock").touch()
    log = run / "gigas.jsonl"
    log.write_text("{}\n")
    unlink = Path.unlink

    def deny(path: Path) -> None:
        if path == log:
            raise PermissionError
        unlink(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", deny)
        clean_old_runs(tmp_path, keep=0)
    assert log.exists()
    clean_old_runs(tmp_path, keep=0)
    assert not run.exists()
