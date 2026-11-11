"""Shared video capture with independent readers and bounded device supervision."""

from gigas.game_io.capture.service import (
    CaptureReader,
    CaptureService,
    CaptureSnapshot,
    CaptureTimeouts,
)

__all__ = ["CaptureReader", "CaptureService", "CaptureSnapshot", "CaptureTimeouts"]
