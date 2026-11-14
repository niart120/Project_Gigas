import asyncio
from pathlib import Path

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.services._conversation_resources import ConversationResources
from gigas.services.input import InputService


def test_preparation_reuses_endpoint_and_archive_across_input_stop(tmp_path: Path) -> None:
    async def scenario() -> None:
        reader = CaptureReader(lambda: CaptureSnapshot(phase="idle", generation=0))
        failures = []
        resources = ConversationResources(tmp_path, reader, lambda *args: failures.append(args))
        inputs = InputService()
        try:
            first = await resources.prepare(inputs.start)
            resources.record_thread("thread")
            inputs.stop()
            await inputs.drain_input()
            second = await resources.prepare(inputs.start)
            assert second == first
            assert first.cwd.is_dir()
            assert (first.cwd / "memory").is_dir()
            assert not failures
        finally:
            await inputs.close()
            await resources.close()

    asyncio.run(scenario())
