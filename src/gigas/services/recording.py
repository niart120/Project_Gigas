"""One ordered persistence boundary shared by observations and input records."""

import asyncio
import logging
from copy import deepcopy
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from gigas.game_io.observation import Observation
    from gigas.runtime.codex.transport import JsonObject
    from gigas.storage.input_events import InputEvent
    from gigas.storage.project import ProjectWriter

logger = logging.getLogger(__name__)


class RecordingService:
    """Own submitted saves on one event loop and serialize use of one writer.

    Args:
        writer: Application-owned writer, closed only after drain().

    Attributes:
        on_failure: Optional callback for every save failure, called on this loop.
            Used for persistent diagnostics, independently of input permission.
            The callback must return promptly; awaited writes still raise errors.
    """

    def __init__(self, writer: "ProjectWriter") -> None:
        """Borrow a writer without performing I/O."""
        self.on_failure: Callable[[Exception], None] | None = None
        self._writer = writer
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    def submit_thread(self, thread_id: str) -> None:
        """Queue the thread record without delaying execution; report save failures."""
        self._submit(partial(self._writer.record_thread, thread_id), thread_id)

    async def ensure_memory(self) -> "Path":
        """Prepare missing memory entries under the same lock as accepted saves.

        Call during explicit thread preparation, never from observation delivery.
        Filesystem failures propagate to the preparation caller.
        """
        await asyncio.shield(self._submit(self._writer.ensure_memory, "memory"))
        return self._writer.path / "memory"

    def submit_dialogue(
        self,
        kind: str,
        content: "JsonObject",
        *,
        thread_id: str,
        turn_id: str | None = None,
    ) -> None:
        """Snapshot public content and queue it without delaying execution.

        Args:
            kind: Application-selected dialogue.* event name.
            content: Public fields only; caller excludes secrets, raw RPCs, and drafts.
            thread_id: Owning conversation.
            turn_id: Owning turn when known.
        """
        self._submit(
            partial(
                self._writer.record_dialogue,
                kind,
                deepcopy(content),
                thread_id=thread_id,
                turn_id=turn_id,
                received_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            ),
            turn_id or thread_id,
        )

    def submit_input(self, event: "InputEvent") -> None:
        """Submit an input event without waiting; report failures through callbacks/logging."""
        self._submit(partial(self._writer.record_input, event), event.operation_id)

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
        operation: "Callable[[], object]",
        reference: str,
        on_error: "Callable[[Exception], None] | None" = None,
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(self._save(operation, reference, on_error))
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        return task

    async def _save(
        self,
        operation: "Callable[[], object]",
        reference: str,
        on_error: "Callable[[Exception], None] | None",
    ) -> None:
        try:
            async with self._lock:
                await asyncio.to_thread(operation)
        except Exception as error:
            # Persistence failure is reported independently of input execution.
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
