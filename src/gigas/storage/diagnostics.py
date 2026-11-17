"""Queued rotating diagnostic files and retention across application launches."""

import logging
import os
import queue
import re
import sys
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from typing import BinaryIO, override

from gigas.diagnostics import DiagnosticFormatter, redactor

if sys.platform == "win32":
    import msvcrt

_RUN_NAME = re.compile(r"\d{8}T\d{6}Z_\d+\Z")
_LOG_NAME = re.compile(r"gigas\.jsonl(?:\.[1-9])?\Z")


def _lock(path: Path) -> BinaryIO:
    stream = path.open("a+b")
    try:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        stream.close()
        raise
    return stream


def clean_old_runs(root: Path, *, keep: int = 10) -> None:
    """Remove old inactive managed runs, skipping concurrent cleanup and failures.

    Args:
        root: Directory exclusively containing application diagnostic runs.
        keep: Number of inactive runs to retain.
    """
    try:
        cleanup_lock = _lock(root / ".cleanup.lock")
    except OSError:
        return
    with cleanup_lock:
        inactive: list[Path] = []
        for path in sorted(root.iterdir()):
            if not _RUN_NAME.fullmatch(path.name) or path.is_symlink() or path.is_junction():
                continue
            if not path.is_dir():
                continue
            try:
                # A previous deletion can have removed the lock before rmdir failed.
                if not (path / ".lock").is_file() and any(path.iterdir()):
                    continue
                with _lock(path / ".lock"):
                    inactive.append(path)
            except OSError:
                continue
        for path in inactive[: max(0, len(inactive) - keep)]:
            with suppress(OSError):
                # Only our fixed filenames are eligible; never recursively delete user data.
                entries = list(path.iterdir())
                if any(
                    entry.is_symlink()
                    or not entry.is_file()
                    or (entry.name != ".lock" and not _LOG_NAME.fullmatch(entry.name))
                    for entry in entries
                ):
                    continue
                # Keep the ownership marker if deleting a log fails partway through.
                for entry in sorted(entries, key=lambda item: item.name == ".lock"):
                    entry.unlink()
                path.rmdir()


class _FileHandler(RotatingFileHandler):
    def __init__(self, path: Path, failure: Callable[[str], None]) -> None:
        super().__init__(path, maxBytes=10 * 1024 * 1024, backupCount=9, encoding="utf-8")
        self.setFormatter(DiagnosticFormatter())
        self._failure = failure
        self._failed = False
        self._last_failure = ""

    @override
    def handleError(self, record: logging.LogRecord) -> None:
        self._failed = True
        reason = redactor.clean(str(sys.exception()))
        if reason != self._last_failure:
            self._last_failure = reason
            self._failure(reason)

    @override
    def emit(self, record: logging.LogRecord) -> None:
        was_failed = bool(self._last_failure)
        self._failed = False
        super().emit(record)
        if was_failed and not self._failed:
            self._last_failure = ""
            recovery = logging.LogRecord(
                "gigas.logging", logging.INFO, __file__, 0, "Diagnostic saving recovered", (), None
            )
            recovery.event = "logging.recovered"
            super().emit(recovery)


class DiagnosticSession:
    """Own one launch's logging queue, file handler and active-run lock."""

    def __init__(self, settings_path: Path, on_failure: Callable[[Path, str], None]) -> None:
        """Prepare the destination without starting the writer.

        Args:
            settings_path: Application settings path whose parent owns logs.
            on_failure: Thread-safe notification callback; must not log recursively.
        """
        self.root = settings_path.parent / "logs"
        started = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.path = self.root / f"{started}_{os.getpid()}" / "gigas.jsonl"
        self._notify = on_failure
        self._lock_file: BinaryIO | None = None
        self._handler: _FileHandler | None = None
        self._queue_handler: QueueHandler | None = None
        self._listener: QueueListener | None = None
        self._previous_level = logging.getLogger().level

    def start(self) -> None:
        """Start asynchronous logging, reporting initialization failure without raising."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._lock_file = _lock(self.path.parent / ".lock")
            self._handler = _FileHandler(self.path, self._failure)
            pending: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
            self._queue_handler = QueueHandler(pending)
            self._queue_handler.setLevel(logging.INFO)
            self._listener = QueueListener(pending, self._handler)
            self._listener.start()
            logging.getLogger().addHandler(self._queue_handler)
            logging.getLogger().setLevel(logging.INFO)
        except OSError as error:
            self._failure(str(error))
            if self._lock_file is not None:
                self._lock_file.close()
                self._lock_file = None
            return
        with suppress(OSError):
            clean_old_runs(self.root)

    def close(self) -> None:
        """Drain and close logging; call outside the GUI thread after services stop."""
        if self._queue_handler is not None:
            logging.getLogger().removeHandler(self._queue_handler)
            self._queue_handler.close()
            self._queue_handler = None
            logging.getLogger().setLevel(self._previous_level)
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        if self._handler is not None:
            self._handler.close()
            self._handler = None
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None
        with suppress(OSError):
            clean_old_runs(self.root)

    def _failure(self, reason: str) -> None:
        self._notify(self.path, redactor.clean(reason))
