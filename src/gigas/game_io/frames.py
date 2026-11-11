"""A bounded latest-frame slot shared by host-side readers."""

from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gigas.game_io.models import Frame


class FrameStore:
    """Keep one immutable frame, rejecting invalidated or older acquisitions."""

    def __init__(self) -> None:
        """Create an empty slot without starting capture."""
        self._lock = Lock()
        self._generation: int | None = None
        self._frame: Frame | None = None

    def begin(self, generation: int) -> None:
        """Invalidate the previous image and accept frames for this generation."""
        with self._lock:
            self._generation = generation
            self._frame = None

    def publish(self, frame: "Frame") -> bool:
        """Replace the slot only with a newer frame of the active generation.

        Returns:
            True when the frame became the latest image; false for stale input.
        """
        with self._lock:
            if frame.generation != self._generation or (
                self._frame is not None and frame.sequence <= self._frame.sequence
            ):
                return False
            self._frame = frame
            return True

    def latest(self) -> "Frame | None":
        """Return the immutable image, or None before acquisition or after invalidation."""
        with self._lock:
            return self._frame

    def invalidate(self) -> None:
        """Remove the image and reject any further frames of the old generation."""
        with self._lock:
            self._generation = None
            self._frame = None
