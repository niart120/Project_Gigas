"""Shared fixtures for integration tests."""

from datetime import UTC, datetime, timedelta
from itertools import count
from unittest.mock import Mock

import pytest

from gigas.app import video
from gigas.game_io import observation
from tests.integration.peers.controlled_capture import ControlledCapture


@pytest.fixture(autouse=True)
def isolated_default_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use an empty catalog unless a test explicitly injects its capture service."""
    monkeypatch.setattr(video, "CaptureService", lambda: ControlledCapture(auto_enumerate=True))


@pytest.fixture
def distinct_observation_times(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give observations distinct milliseconds in tests that require separate files."""
    start = datetime(2026, 11, 18, tzinfo=UTC)
    milliseconds = count()
    clock = Mock(wraps=datetime)
    clock.now.side_effect = lambda _tz: start + timedelta(milliseconds=next(milliseconds))
    monkeypatch.setattr(observation, "datetime", clock)
