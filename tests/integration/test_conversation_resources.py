import asyncio
from pathlib import Path

import pytest

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.services._conversation_resources import ConversationResources
from gigas.services.input import InputService
from gigas.storage.records import GameIoRecordWriter, RecordError
from tests.unit.test_codex_management import _client


def test_preparation_reuses_endpoint_and_archive_across_input_stop(tmp_path: Path) -> None:
    async def scenario() -> None:
        reader = CaptureReader(lambda: CaptureSnapshot(phase="idle", generation=0))
        failures = []
        resources = ConversationResources(tmp_path, reader, lambda *args: failures.append(args))
        inputs = InputService()
        async with _client(tmp_path) as (management, _peer):
            try:
                first = await resources.prepare(management, inputs.start)
                assert first.cwd.parent == tmp_path / "unassigned"
                assert list(first.cwd.iterdir()) == []
                await resources.bind_records("thread")
                inputs.stop()
                await inputs.drain_input()
                second = await resources.prepare(management, inputs.start)
                await resources.bind_records("thread")
                assert second == first
                assert (first.cwd / "records" / "thread" / "events.jsonl").is_file()
                assert not failures
            finally:
                await inputs.close()
                await resources.close()
            GameIoRecordWriter.open(first.cwd / "records" / "thread").close()

    asyncio.run(scenario())


@pytest.mark.parametrize("identifier", ["../escape", "..\\escape", ".", "..", "absolute"])
def test_bind_rejects_thread_ids_that_cannot_be_used_as_a_single_folder_name(
    tmp_path: Path, identifier: str
) -> None:
    async def scenario() -> None:
        resources = ConversationResources(tmp_path, CaptureReader(CaptureSnapshot), lambda *_: None)
        inputs = InputService()
        async with _client(tmp_path) as (management, _peer):
            try:
                prepared = await resources.prepare(management, inputs.start)
                thread_id = str(tmp_path / "escape") if identifier == "absolute" else identifier
                with pytest.raises(RecordError):
                    await resources.bind_records(thread_id)
                assert list(prepared.cwd.iterdir()) == []
                assert not (tmp_path / "escape").exists()
            finally:
                await inputs.close()
                await resources.close()

    asyncio.run(scenario())
