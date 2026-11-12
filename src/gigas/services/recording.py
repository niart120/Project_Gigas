"""One ordered persistence boundary shared by observations and input records."""

import asyncio
import logging
from functools import partial
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.game_io.observation import Observation
    from gigas.storage.input_events import InputEvent
    from gigas.storage.project import ProjectWriter

logger = logging.getLogger(__name__)


class RecordingService:
    """Own submitted saves on one event loop and serialize use of one writer.

    Args:
        writer: Application-owned writer, closed only after drain().

    Attributes:
        on_failure: Optional callback for every save failure, called on this loop.
            An input owner can revoke permission and stop input here. The callback
            must return promptly; awaited writes still raise their storage error.
    """

    def __init__(self, writer: "ProjectWriter") -> None:
        """Borrow a writer without performing I/O."""
        self.on_failure: Callable[[Exception], None] | None = None
        self._writer = writer
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    async def record_thread(self, thread_id: str) -> None:
        """Wait for a real thread record; cancellation leaves its save owned.

        Raises:
            ProjectError: The thread is invalid or persistence fails.
        """
        await asyncio.shield(
            self._submit(partial(self._writer.record_thread, thread_id), thread_id)
        )

    async def record_input(self, event: "InputEvent") -> None:
        """Wait for an input start or outcome to persist before continuing.

        Cancellation returns immediately while the submitted save retains its lock
        and ownership. It never grants permission to proceed with physical input.

        Raises:
            ProjectError: The input record is invalid or persistence fails.
        """
        await asyncio.shield(self._submit(partial(self._writer.record_input, event), event.run_id))

    def submit_input(self, event: "InputEvent") -> None:
        """Submit a result without waiting; report failures through on_failure/logging."""
        self._submit(partial(self._writer.record_input, event), event.run_id)

    def submit_observation(
        self,
        observation: "Observation",
        *,
        thread_id: str | None = None,
        on_error: "Callable[[Observation, Exception], None] | None" = None,
    ) -> None:
        """Submit final PNG bytes and metadata without delaying observation delivery.

        on_error preserves the observation-specific error callback, in addition to
        on_failure. Callbacks run on this loop and must return promptly.
        """
        self._submit(
            partial(self._writer.record, observation, thread_id=thread_id),
            observation.observation_id,
            partial(on_error, observation) if on_error is not None else None,
        )

    def _submit(
        self,
        operation: "Callable[[], None]",
        reference: str,
        on_error: "Callable[[Exception], None] | None" = None,
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(self._save(operation, reference, on_error))
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        return task

    async def _save(
        self,
        operation: "Callable[[], None]",
        reference: str,
        on_error: "Callable[[Exception], None] | None",
    ) -> None:
        try:
            async with self._lock:
                await asyncio.to_thread(operation)
        except Exception as error:
            # Includes executor submission and storage failures. Never turn failed
            # start records into successful permission to send physical input.
            if on_error is None:
                logger.exception("Recording failed: %s (%s)", reference, self._writer.path)
            for callback in (self.on_failure, on_error):
                if callback is not None:
                    try:
                        callback(error)
                    except Exception:
                        logger.exception("Recording failure callback failed: %s", reference)
            raise

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def drain(self) -> None:
        """Wait for accepted saves without cancelling them or re-raising save errors.

        Stop new submissions first. Cancelling this wait leaves every save owned;
        call drain() again before closing the writer. Errors are reported through
        callbacks/logging, so completion does not mean every save succeeded.
        """
        if self._tasks:
            await asyncio.shield(asyncio.gather(*self._tasks, return_exceptions=True))
