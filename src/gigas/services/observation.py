"""Latest-frame observations with independently owned background persistence."""

import asyncio
import logging
from typing import TYPE_CHECKING

from gigas.game_io.observation import Observation, ObservationError

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.game_io.capture import CaptureReader
    from gigas.game_io.capture.models import Frame
    from gigas.game_io.observation import ObservationResolution
    from gigas.storage.project import ProjectWriter

logger = logging.getLogger(__name__)


class ObservationService:
    """Return encoded snapshots and save the same values without delaying observations.

    All async methods and setting changes belong to a single event loop. The
    application owns the writer; stop new requests and await drain() before
    closing it or the event loop. Storage tasks do not occupy the encoding slot.

    Args:
        reader: Read-only access to the existing capture service.
        writer: Application-owned archive writer borrowed by this service.
        resolution: Image size used by the next observation; defaults to FullHD.
        thread_id: Real runtime conversation, or None for standalone observations.
        on_save_error: Optional callback receiving the observation and storage
            exception on this event loop. It must return promptly. Without a
            callback, failures are logged with the observation ID and archive path.

    Attributes:
        resolution: Applied size for subsequent observations. Each observe call
            snapshots it once, so an in-progress image is unaffected by changes.
        thread_id: Chat context for subsequent observations. Later changes do not
            change the context of already submitted encoding or persistence.
    """

    def __init__(
        self,
        reader: "CaptureReader",
        writer: "ProjectWriter",
        *,
        resolution: "ObservationResolution" = "full_hd",
        thread_id: str | None = None,
        on_save_error: "Callable[[Observation, Exception], None] | None" = None,
    ) -> None:
        """Keep existing owners without starting a device or event loop."""
        self.resolution = resolution
        self.thread_id = thread_id
        self._reader = reader
        self._writer = writer
        self._on_save_error = on_save_error
        self._worker: asyncio.Task[Observation] | None = None
        self._saves: set[asyncio.Task[None]] = set()
        self._save_lock = asyncio.Lock()

    async def observe(self) -> "Observation":
        """Encode the latest streaming frame without waiting for persistence.

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
        self._worker = asyncio.create_task(
            self._encode(snapshot.frame, self.resolution, self.thread_id)
        )
        self._worker.add_done_callback(_retrieve_exception)
        return await asyncio.shield(self._worker)

    async def _encode(
        self, frame: "Frame", resolution: "ObservationResolution", thread_id: str | None
    ) -> Observation:
        observation = await asyncio.to_thread(Observation.from_frame, frame, resolution=resolution)
        task = asyncio.create_task(self._save(observation, thread_id))
        self._saves.add(task)
        task.add_done_callback(self._save_finished)
        return observation

    async def _save(self, observation: Observation, thread_id: str | None) -> None:
        try:
            # Reuse the current synchronous writer safely. This lock only orders
            # storage operations; image encoding and delivery never acquire it.
            async with self._save_lock:
                await asyncio.to_thread(self._writer.record, observation, thread_id=thread_id)
        except Exception as error:
            # Background I/O and executor submission failures belong to recording,
            # including unexpected exceptions; they cannot retroactively fail observe.
            if self._on_save_error is None:
                logger.exception(
                    "Observation save failed: %s (%s)",
                    observation.observation_id,
                    self._writer.path,
                )
            else:
                self._on_save_error(observation, error)

    def _save_finished(self, task: "asyncio.Task[None]") -> None:
        self._saves.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Observation save callback failed", exc_info=task.exception())

    async def drain(self) -> None:
        """Wait for submitted encoding and all saves, including after cancellation.

        The owner must stop new requests before calling this for shutdown.
        Cancelling this wait leaves workers owned; call drain() again to finish.
        Save failures are reported separately through on_save_error or logging;
        a completed drain means processing ended, not that every save succeeded.

        Raises:
            ObservationError: The last observation failed to encode. Already
                submitted saves are still drained before this error is raised.
        """
        encoding_error: Exception | None = None
        if self._worker is not None:
            try:
                await asyncio.shield(self._worker)
            except Exception as error:  # noqa: BLE001 - re-raised after existing saves finish.
                encoding_error = error
        if self._saves:
            await asyncio.shield(asyncio.gather(*self._saves, return_exceptions=True))
        if encoding_error is not None:
            raise encoding_error


def _retrieve_exception(task: "asyncio.Task[Observation]") -> None:
    # A cancelled requester may never await the result; drain still exposes failures.
    if not task.cancelled():
        task.exception()
