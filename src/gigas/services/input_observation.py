"""Post-input frame selection on the existing capture and observation services."""

import asyncio
from time import monotonic_ns
from typing import TYPE_CHECKING

from gigas.game_io.observation import ObservationError

if TYPE_CHECKING:
    from gigas.game_io.capture import CaptureReader
    from gigas.game_io.observation import Observation
    from gigas.services.observation import ObservationService


async def observe_after(
    reader: "CaptureReader", service: "ObservationService", generation: int, boundary_ns: int
) -> "Observation":
    """Select and encode a frame whose device read began after input completed.

    Args:
        reader: Reader for the already owned capture service.
        service: Existing observation encoder and asynchronous recording owner.
        generation: Capture generation checked when the input request was accepted.
        boundary_ns: Host monotonic time after final neutral or trailing wait.

    Returns:
        One Observation shared with persistence, without waiting for its save.

    Raises:
        ObservationError: Capture stops/changes, no qualifying frame arrives within
            five seconds of boundary_ns, or the shared encoder is busy/fails.

    Cancellation interrupts frame waiting immediately. Once encoding starts, it
    is recovered before cancellation returns; saving remains independent. This
    host ordering does not establish game rendering or capture-card latency.
    """
    deadline = boundary_ns + 5_000_000_000
    while True:
        snapshot = reader.snapshot()
        if snapshot.phase != "streaming" or snapshot.generation != generation:
            message = "操作後の映像取得が停止、または取得元が変更されました"
            raise ObservationError("DEVICE_UNAVAILABLE", message)
        now = monotonic_ns()
        if now >= deadline:
            message = "操作完了後に取得を開始した画像を5秒以内に得られませんでした"
            raise ObservationError("OBSERVATION_TIMEOUT", message)
        frame = snapshot.frame
        if (
            frame is not None
            and frame.generation == generation
            and frame.read_started_ns > boundary_ns
        ):
            return await service.observe_frame(frame)
        await asyncio.sleep(min(0.02, (deadline - now) / 1_000_000_000))
