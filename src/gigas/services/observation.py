"""Latest-frame recording on the caller's existing asyncio event loop."""

import asyncio
from typing import TYPE_CHECKING

from gigas.game_io.observation import ObservationError

if TYPE_CHECKING:
    from gigas.game_io.capture import CaptureReader
    from gigas.game_io.observation import Observation
    from gigas.storage.project import ProjectWriter


class ObservationService:
    """Record snapshots using one reader and one fixed project writer.

    All async methods belong to a single event loop. The caller owns the writer
    and must await drain() before closing it or switching the selected project.

    Args:
        reader: Read-only access to the existing capture service.
        writer: Validated project held for this service's lifetime.
    """

    def __init__(self, reader: "CaptureReader", writer: "ProjectWriter") -> None:
        """Keep the existing owners without starting a device or event loop."""
        self._reader = reader
        self._writer = writer

    async def observe(self) -> "Observation":
        """Record the latest streaming frame, even if acquired before this request.

        Returns:
            The persisted PNG bytes, observation ID, and original frame.

        Raises:
            ObservationError: No streaming image is available or encoding fails.
            ProjectError: Persistence fails or the writer cannot accept a record.
        """
        snapshot = self._reader.snapshot()
        if snapshot.phase != "streaming" or snapshot.frame is None:
            message = "観測できる映像がありません"
            raise ObservationError("DEVICE_UNAVAILABLE", message)
        return await asyncio.to_thread(self._writer.record, snapshot.frame)
