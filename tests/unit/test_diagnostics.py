"""Diagnostic formatting retains useful failures without credentials."""

import json
import logging
import sys

from gigas.diagnostics import DiagnosticFormatter, Redactor, redactor


def test_redacts_credentials_without_removing_failure_context() -> None:
    cleaner = Redactor()
    cleaner.register("issued-secret")
    result = cleaner.clean(
        "Bad Request: Authorization: Bearer auth-secret; "
        'access_token="access-secret" password=pass-secret path=C:/logs issued-secret'
    )
    for secret in ("auth-secret", "access-secret", "pass-secret", "issued-secret"):
        assert secret not in result
    assert "Bad Request" in result
    assert "path=C:/logs" in result


def test_json_exception_and_nested_details_are_sanitized() -> None:
    redactor.register("test-issued-secret")
    try:
        raise ValueError("Bad Request Bearer exception-secret")  # noqa: TRY003, TRY301 -- synthetic failure
    except ValueError:
        record = logging.LogRecord(
            "gigas.test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )
    details = {"refresh_token": "nested-secret", "path": "C:/logs"}
    record.details = details
    record.thread_id = "thread-test"
    record.message_detail = "test-issued-secret"
    rendered = DiagnosticFormatter().format(record)
    parsed = json.loads(rendered)
    assert parsed["timestamp"].endswith("Z")
    assert parsed["thread_id"] == "thread-test"
    assert parsed["details"]["path"] == "C:/logs"
    assert "ValueError" in parsed["exc_info"]
    assert "Bad Request" in parsed["exc_info"]
    for secret in ("nested-secret", "exception-secret", "test-issued-secret"):
        assert secret not in rendered
    assert details["refresh_token"] == "nested-secret"  # noqa: S105 -- synthetic credential
