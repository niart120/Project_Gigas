"""Shared formatting and credential removal for diagnostic records."""

import logging
import re
import threading
from datetime import UTC, datetime
from typing import cast, override

from pythonjsonlogger.core import LogData
from pythonjsonlogger.json import JsonEncoder, JsonFormatter

_CREDENTIAL = re.compile(
    r"""(?i)(["']?(?:authorization|access_token|refresh_token|id_token|api_key|apikey|password|client_secret)["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}]+)"""
)
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s\"',;}]+")


class Redactor:
    """Remove known credentials from diagnostic text across producer threads."""

    def __init__(self) -> None:
        """Prepare a process-local registry of issued connection secrets."""
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def register(self, secret: str) -> None:
        """Register a nonempty secret before it can appear in diagnostics.

        Args:
            secret: Exact connection credential to redact for this process lifetime.
        """
        if secret:
            with self._lock:
                self._secrets.add(secret)

    def clean(self, text: str) -> str:
        """Return text with registered secrets and known credential forms removed.

        Args:
            text: Diagnostic text, possibly containing serialized data.

        Returns:
            Text retaining surrounding diagnostic context.
        """
        with self._lock:
            secrets = sorted(self._secrets, key=len, reverse=True)
        for secret in secrets:
            text = text.replace(secret, "[REDACTED]")
        text = _BEARER.sub("Bearer [REDACTED]", text)
        return _CREDENTIAL.sub(r'\1"[REDACTED]"', text)


redactor = Redactor()


class DiagnosticFormatter(JsonFormatter):
    """Render UTC JSON records after credential removal, including exceptions."""

    def __init__(self) -> None:
        """Configure stable common fields with additional logging extras preserved."""
        super().__init__(
            "%(asctime)s %(levelname)s %(name)s %(message)s %(process)s",
            rename_fields={
                "asctime": "timestamp",
                "levelname": "level",
                "name": "source",
                "process": "pid",
            },
            json_ensure_ascii=False,
            json_default=_encode_clean,
        )

    @override
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        """Format a record timestamp in UTC.

        Args:
            record: Source logging record.
            datefmt: Unused compatibility argument; timestamps always use ISO 8601.

        Returns:
            UTC timestamp with milliseconds and a Z suffix.
        """
        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds")
        return timestamp.replace("+00:00", "Z")

    @override
    def process_log_record(self, log_data: LogData) -> LogData:
        """Remove credentials before JSON serialization.

        Args:
            log_data: Fields assembled by the logging formatter.

        Returns:
            A sanitized copy of the fields.
        """
        return cast("LogData", _clean_value(log_data))


def _encode_clean(value: object) -> object:
    # Non-JSON objects become strings or containers only during encoding.
    return _clean_value(JsonEncoder().default(value))


def _clean_value(value: object) -> object:
    if isinstance(value, str):
        return redactor.clean(value)
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if isinstance(key, str) and _CREDENTIAL.match(f"{key}=value")
            else _clean_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_clean_value(item) for item in value]
    return value
