"""Spawnable diagnostic owner for cross-process retention checks."""

import logging
from multiprocessing.connection import Connection
from pathlib import Path

from gigas.storage.diagnostics import DiagnosticSession


def hold_session(root: str, channel: Connection) -> None:
    """Hold the real run lock until the parent requests normal shutdown."""
    session = DiagnosticSession(Path(root) / "settings.toml", lambda *_: None)
    session.start()
    try:
        logging.getLogger(__name__).info("child started", extra={"event": "test.started"})
        channel.send(str(session.path))
        channel.recv()
    finally:
        session.close()
        channel.close()
