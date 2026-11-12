"""Latest and post-operation observations with shared background persistence."""

import asyncio
from typing import TYPE_CHECKING

from gigas.game_io.observation import Observation, ObservationError

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.game_io.capture import CaptureReader
    from gigas.game_io.capture.models import Frame
    from gigas.game_io.observation import ObservationResolution
    from gigas.services.recording import RecordingService


class ObservationService:
    """Return encoded snapshots and save the same values without delaying observations.

    All async methods and setting changes belong to a single event loop. The
    application owns persistence; stop new requests, await drain(), then drain
    recording before closing the writer or loop. Saves do not occupy the encoding slot.

    Args:
        reader: Read-only access to the existing capture service.
        recording: Application-owned persistence service shared with input and chat.
        resolution: Image size used by the next observation; defaults to FullHD.
        thread_id: Conversation for latest-frame observations, or None when standalone.
        on_save_error: Optional callback receiving the observation and storage
            exception on this event loop. It must return promptly. Without a
            callback, failures are logged with the observation ID and archive path.

    Attributes:
        resolution: Applied size for subsequent observations. Each observe call
            snapshots it once, so an in-progress image is unaffected by changes.
        thread_id: Chat context for subsequent observe() calls. Post-operation
            observations receive their fixed context explicitly. Later changes do
            not change the context of submitted encoding or persistence.
    """

    def __init__(
        self,
        reader: "CaptureReader",
        recording: "RecordingService",
        *,
        resolution: "ObservationResolution" = "full_hd",
        thread_id: str | None = None,
        on_save_error: "Callable[[Observation, Exception], None] | None" = None,
    ) -> None:
        """Keep existing owners without starting a device or event loop."""
        self.resolution = resolution
        self.thread_id = thread_id
        self._recording = recording
        self._reader = reader
        self._on_save_error = on_save_error
        self._worker: asyncio.Task[Observation] | None = None

    async def observe(self, *, thread_id: str | None = None) -> "Observation":
        """Encode the latest streaming frame without waiting for persistence.

        Args:
            thread_id: Explicit conversation context; defaults to the service context.

        Returns:
            Final PNG bytes, observation ID, and source metadata. The matching
            file may not exist yet, and a later storage failure does not change
            this observation into an error.

        Raises:
            ObservationError: Encoding is busy, no image is available, or encoding fails.

        Cancellation suppresses delivery, not the submitted encoding and saving.
        Keep this service alive and await drain() before closing its event loop.
        """
        if self._worker is not None and not self._worker.done():
            message = "観測画像の符号化中です"
            raise ObservationError("BUSY", message)
        snapshot = self._reader.snapshot()
        if snapshot.phase != "streaming" or snapshot.frame is None:
            message = "観測できる映像がありません"
            raise ObservationError("DEVICE_UNAVAILABLE", message)
        return await asyncio.shield(
            self._start_encoding(snapshot.frame, self.thread_id if thread_id is None else thread_id)
        )

    def _start_encoding(self, frame: "Frame", thread_id: str | None) -> "asyncio.Task[Observation]":
        if self._worker is not None and not self._worker.done():
            message = "観測画像の符号化中です"
            raise ObservationError("BUSY", message)
        self._worker = asyncio.create_task(self._encode(frame, self.resolution, thread_id))
        self._worker.add_done_callback(_retrieve_exception)
        return self._worker

    async def _encode(
        self, frame: "Frame", resolution: "ObservationResolution", thread_id: str | None
    ) -> Observation:
        observation = await asyncio.to_thread(Observation.from_frame, frame, resolution=resolution)
        self._recording.submit_observation(
            observation, thread_id=thread_id, on_error=self._on_save_error
        )
        return observation

    async def drain(self) -> None:
        """Wait for submitted encoding, including after cancellation.

        Stop new requests before calling this. Cancelling the wait leaves encoding
        owned; call again to finish. The application must separately drain recording,
        even if this method raises, before closing the writer or event loop.

        Raises:
            ObservationError: The last observation failed to encode.
        """
        if self._worker is not None:
            await asyncio.shield(self._worker)


def _retrieve_exception(task: "asyncio.Task[Observation]") -> None:
    # A cancelled requester may never await the result; drain still exposes failures.
    if not task.cancelled():
        task.exception()
