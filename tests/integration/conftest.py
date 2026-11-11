"""Keep implicit application capture services away from physical devices."""

import pytest

from gigas.app import video
from tests.integration.peers.controlled_capture import ControlledCapture


@pytest.fixture(autouse=True)
def isolated_default_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use an empty catalog unless a test explicitly injects its capture service."""
    monkeypatch.setattr(video, "CaptureService", lambda: ControlledCapture(auto_enumerate=True))
