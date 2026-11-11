"""Latest-frame recording on the caller's existing asyncio event loop."""

import asyncio
from typing import TYPE_CHECKING

from gigas.game_io.observation import Observation, ObservationError

if TYPE_CHECKING:
    from gigas.game_io.capture import CaptureReader
    from gigas.game_io.capture.models import Frame
    from gigas.storage.project import ProjectWriter


class ObservationService:
    """Record snapshots using one reader and a borrowed project writer.

    All async methods belong to a single event loop. The application owns the
    writer beyond individual calls and Codex connections. Await drain() before
    ending this service; only the project owner decides when to close the writer.

    Args:
        reader: Read-only access to the existing capture service.
        writer: Application-owned archive writer borrowed by this service.
    """

    def __init__(self, reader: "CaptureReader", writer: "ProjectWriter") -> None:
        """Keep the existing owners without starting a device or event loop."""
        self._reader = reader
        self._writer = writer
        self._worker: asyncio.Task[Observation] | None = None

    async def observe(self) -> "Observation":
        """Record the latest streaming frame, even if acquired before this request.

        Returns:
            The persisted PNG bytes, observation ID, and source metadata.

        Raises:
            ObservationError: Busy, no streaming image is available, or encoding fails.
            ProjectError: Persistence fails or the writer cannot accept a record.

        Cancellation suppresses delivery, not a submitted recording operation.
        Keep this service alive and await drain() before closing the event loop.
        """
        if self._worker is not None and not self._worker.done():
            message = "観測の保存処理中です"
            raise ObservationError("BUSY", message)
        snapshot = self._reader.snapshot()
        if snapshot.phase != "streaming" or snapshot.frame is None:
            message = "観測できる映像がありません"
            raise ObservationError("DEVICE_UNAVAILABLE", message)
        self._worker = asyncio.create_task(asyncio.to_thread(self._record, snapshot.frame))
        self._worker.add_done_callback(_retrieve_exception)
        return await asyncio.shield(self._worker)

    def _record(self, frame: "Frame") -> Observation:
        observation = Observation.from_frame(frame)
        self._writer.record(observation)
        return observation

    async def drain(self) -> None:
        """Wait for the submitted recording, including after requester cancellation.

        The owner must stop accepting new requests before calling this for shutdown.
        Cancelling this wait leaves the worker owned; call drain() again to finish.

        Raises:
            ObservationError: The last recording failed to encode.
            ProjectError: The last recording failed to persist.
        """
        if self._worker is not None:
            await asyncio.shield(self._worker)


def _retrieve_exception(task: "asyncio.Task[Observation]") -> None:
    # A cancelled requester may never await the result; drain still exposes failures.
    if not task.cancelled():
        task.exception()
