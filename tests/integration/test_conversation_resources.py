import asyncio
import json
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.mcp.game_io import GameIoMcpServer
from gigas.services._conversation_resources import ConversationResources
from gigas.services.input import InputService
from gigas.storage.records import GameIoRecordWriter, RecordError
from tests.unit.test_codex_management import _client

if TYPE_CHECKING:
    from gigas.storage.records import DialogueRecordType


@pytest.mark.parametrize("project_id", [None, "project-1"])
def test_existing_workspace_reuses_records_and_endpoint_without_creating_a_new_workspace(
    tmp_path: Path, project_id: str | None
) -> None:
    workspace = tmp_path / "existing"
    records = workspace / "records" / "saved-thread"
    records.parent.mkdir(parents=True)
    notes = workspace / "notes.md"
    notes.write_bytes(b"existing notes")
    initial_writer = GameIoRecordWriter.create(records)
    initial_writer.record_dialogue("dialogue.submitted", "before")
    initial_writer.close()
    original = (records / "events.jsonl").read_bytes()
    image = records / "observations" / "retained.png"
    image.write_bytes(b"existing image")

    async def scenario() -> None:
        resources = ConversationResources(tmp_path, CaptureReader(CaptureSnapshot), lambda *_: None)
        inputs = InputService()
        try:
            first = await resources.prepare_existing(workspace, inputs.start, project_id=project_id)
            assert first.cwd == workspace
            assert first.project_id == project_id
            assert not resources.records_bound
            await resources.bind_records("saved-thread")
            resources.record_dialogue("dialogue.message", "after")
            second = await resources.prepare_existing(
                workspace, inputs.start, project_id=project_id
            )
            assert second == first
            await resources.bind_records("saved-thread")
        finally:
            await resources.close()
            await inputs.close()

    asyncio.run(scenario())
    contents = (records / "events.jsonl").read_bytes()
    assert contents.startswith(original)
    assert [json.loads(line)["payload"]["text"] for line in contents.splitlines()] == [
        "before",
        "after",
    ]
    assert notes.read_bytes() == b"existing notes"
    assert image.read_bytes() == b"existing image"
    assert not (tmp_path / "unassigned").exists()
    GameIoRecordWriter.open(records).close()


@pytest.mark.parametrize("records_parent_exists", [False, True])
def test_existing_workspace_creates_missing_records_only_on_explicit_binding(
    tmp_path: Path, *, records_parent_exists: bool
) -> None:
    workspace = tmp_path / "existing"
    workspace.mkdir()
    if records_parent_exists:
        (workspace / "records").mkdir()

    async def scenario() -> None:
        resources = ConversationResources(tmp_path, CaptureReader(CaptureSnapshot), lambda *_: None)
        inputs = InputService()
        try:
            await resources.prepare_existing(workspace, inputs.start, project_id=None)
            assert (workspace / "records").exists() is records_parent_exists
            assert not (workspace / "records" / "saved-thread").exists()
            await resources.bind_records("saved-thread")
            assert resources.records_bound
            assert (workspace / "records" / "saved-thread" / "events.jsonl").is_file()
        finally:
            await resources.close()
            await inputs.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("missing", [False, True])
def test_existing_workspace_must_remain_a_directory_before_opening_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, missing: bool
) -> None:
    workspace = tmp_path / "existing"
    if not missing:
        workspace.write_bytes(b"not a directory")

    async def unexpected_start(_server: GameIoMcpServer) -> None:
        pytest.fail("MCP must not start for an unavailable workspace")

    monkeypatch.setattr(GameIoMcpServer, "start", unexpected_start)

    async def scenario() -> None:
        resources = ConversationResources(tmp_path, CaptureReader(CaptureSnapshot), lambda *_: None)
        inputs = InputService()
        try:
            with pytest.raises(FileNotFoundError if missing else NotADirectoryError):
                await resources.prepare_existing(workspace, inputs.start, project_id=None)
            assert not resources.records_bound
        finally:
            await resources.close()
            await inputs.close()

    asyncio.run(scenario())
    if missing:
        assert not workspace.exists()
    else:
        assert workspace.read_bytes() == b"not a directory"
    assert not (tmp_path / "unassigned").exists()


@pytest.mark.parametrize("bound", [False, True])
def test_existing_workspace_change_requires_closing_previous_resources(
    tmp_path: Path, *, bound: bool
) -> None:
    first_path, next_path = tmp_path / "first", tmp_path / "next"
    first_path.mkdir()
    next_path.mkdir()

    async def scenario() -> None:
        resources = ConversationResources(tmp_path, CaptureReader(CaptureSnapshot), lambda *_: None)
        inputs = InputService()
        try:
            first = await resources.prepare_existing(first_path, inputs.start, project_id=None)
            if bound:
                await resources.bind_records("first-thread")
            with pytest.raises(RecordError):
                await resources.prepare_existing(next_path, inputs.start, project_id=None)
            assert list(next_path.iterdir()) == []
            assert (
                await resources.prepare_existing(first_path, inputs.start, project_id=None) == first
            )
            await resources.close()
            next_configuration = await resources.prepare_existing(
                next_path, inputs.start, project_id=None
            )
            assert next_configuration.cwd == next_path
            assert next_configuration.mcp_config != first.mcp_config
        finally:
            await resources.close()
            await inputs.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("identifier", ["other-thread", "../escape"])
def test_bound_writer_cannot_silently_accept_another_thread_id(
    tmp_path: Path, identifier: str
) -> None:
    async def scenario() -> None:
        resources = ConversationResources(tmp_path, CaptureReader(CaptureSnapshot), lambda *_: None)
        inputs = InputService()
        async with _client(tmp_path) as (management, _peer):
            try:
                prepared = await resources.prepare(management, inputs.start)
                await resources.bind_records("saved-thread")
                with pytest.raises(RecordError):
                    await resources.bind_records(identifier)
                resources.record_dialogue("dialogue.message", "retained owner")
            finally:
                await resources.close()
                await inputs.close()
        assert [path.name for path in (prepared.cwd / "records").iterdir()] == ["saved-thread"]
        record = json.loads(
            (prepared.cwd / "records" / "saved-thread" / "events.jsonl").read_text()
        )
        assert record["payload"]["text"] == "retained owner"

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["failure", "cancel"])
def test_existing_workspace_recovers_a_partially_started_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    workspace = tmp_path / "existing"
    workspace.mkdir()
    started: list[GameIoMcpServer] = []
    start = GameIoMcpServer.start

    async def scenario() -> None:
        entered = asyncio.Event()

        async def interrupted_start(server: GameIoMcpServer) -> None:
            await start(server)
            started.append(server)
            if len(started) == 1:
                entered.set()
                if outcome == "failure":
                    message = "injected startup failure"
                    raise OSError(message)
                await asyncio.Future()

        monkeypatch.setattr(GameIoMcpServer, "start", interrupted_start)
        resources = ConversationResources(tmp_path, CaptureReader(CaptureSnapshot), lambda *_: None)
        inputs = InputService()
        preparing = asyncio.create_task(
            resources.prepare_existing(workspace, inputs.start, project_id=None)
        )
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if outcome == "cancel":
                preparing.cancel()
            with pytest.raises(asyncio.CancelledError if outcome == "cancel" else OSError):
                await preparing
            with pytest.raises(RuntimeError):
                _ = started[0].url
            retry = await resources.prepare_existing(workspace, inputs.start, project_id=None)
            assert retry.cwd == workspace
            assert started[1].url
            assert not (workspace / "records").exists()
        finally:
            await resources.close()
            await inputs.close()
        with pytest.raises(RuntimeError):
            _ = started[1].url

    asyncio.run(scenario())


@pytest.mark.parametrize("retry", [False, True])
def test_cancelled_existing_writer_acquisition_remains_owned_until_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, retry: bool
) -> None:
    workspace = tmp_path / "existing"
    records = workspace / "records" / "saved-thread"
    records.parent.mkdir(parents=True)
    GameIoRecordWriter.create(records).close()
    entered, release = Event(), Event()
    open_writer = GameIoRecordWriter.open

    def delayed_open(_cls: type[GameIoRecordWriter], path: Path) -> GameIoRecordWriter:
        writer = open_writer(path)
        entered.set()
        if not release.wait(5):
            writer.close()
            message = "test did not release acquisition"
            raise TimeoutError(message)
        return writer

    monkeypatch.setattr(GameIoRecordWriter, "open", classmethod(delayed_open))

    async def scenario() -> None:
        resources = ConversationResources(tmp_path, CaptureReader(CaptureSnapshot), lambda *_: None)
        inputs = InputService()
        try:
            await resources.prepare_existing(workspace, inputs.start, project_id=None)
            binding = asyncio.create_task(resources.bind_records("saved-thread"))
            assert await asyncio.to_thread(entered.wait, 5)
            binding.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await binding
            assert not resources.records_bound
            with pytest.raises(RecordError):
                open_writer(records)
            if retry:
                await resources.bind_records("saved-thread")
                assert resources.records_bound
                resources.record_dialogue("dialogue.message", "after retry")
        finally:
            release.set()
            await resources.close()
            await inputs.close()
        open_writer(records).close()
        if retry:
            assert (
                json.loads((records / "events.jsonl").read_text())["payload"]["text"]
                == "after retry"
            )

    asyncio.run(scenario())


def test_close_drains_existing_record_writes_before_releasing_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "existing"
    records = workspace / "records" / "saved-thread"
    records.parent.mkdir(parents=True)
    GameIoRecordWriter.create(records).close()
    entered, release = Event(), Event()
    record = GameIoRecordWriter.record_dialogue

    def delayed_record(writer: GameIoRecordWriter, kind: "DialogueRecordType", text: str) -> None:
        entered.set()
        if not release.wait(5):
            message = "test did not release write"
            raise TimeoutError(message)
        record(writer, kind, text)

    monkeypatch.setattr(GameIoRecordWriter, "record_dialogue", delayed_record)

    async def scenario() -> None:
        resources = ConversationResources(tmp_path, CaptureReader(CaptureSnapshot), lambda *_: None)
        inputs = InputService()
        try:
            await resources.prepare_existing(workspace, inputs.start, project_id=None)
            await resources.bind_records("saved-thread")
            resources.record_dialogue("dialogue.message", "pending write")
            assert await asyncio.to_thread(entered.wait, 5)
            closing = asyncio.create_task(resources.close())
            await asyncio.sleep(0)
            assert not closing.done()
            with pytest.raises(RecordError):
                GameIoRecordWriter.open(records)
            release.set()
            await closing
            GameIoRecordWriter.open(records).close()
            assert (
                json.loads((records / "events.jsonl").read_text())["payload"]["text"]
                == "pending write"
            )
        finally:
            release.set()
            await resources.close()
            await inputs.close()

    asyncio.run(scenario())


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
