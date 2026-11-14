import asyncio
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.runtime.codex.connection import (
    CodexConnection,
    CodexConnectionError,
    MessageDeliveryError,
)
from gigas.runtime.codex.models import ConnectionOptions
from gigas.runtime.codex.transport import RpcNotification
from gigas.services._conversation_execution import ConversationExecution
from gigas.services._conversation_resources import ConversationResources
from gigas.services.input import InputService
from gigas.storage.project import ProjectWriter
from tests.unit.test_codex_connection import FakePeer, _until

if TYPE_CHECKING:
    from gigas.runtime.codex.connection import RuntimeEvent
    from gigas.runtime.codex.models import ConnectionEvent


class Conversation:
    """Use the real conversation/resources with a scripted runtime boundary."""

    def __init__(self, path: Path) -> None:
        """Assemble independent connection and conversation owners before opening."""
        self.peer = FakePeer()
        self.events: list[ConnectionEvent] = []
        self.inputs = InputService()
        self.resources = ConversationResources(
            path,
            CaptureReader(lambda: CaptureSnapshot(phase="idle", generation=0)),
            lambda *_args: None,
        )
        self.closing: asyncio.Task[None] | None = None
        self.connection = CodexConnection(
            self.peer,
            ConnectionOptions(path),
            self.receive,
        )
        self.execution = ConversationExecution(
            self.connection,
            self.resources,
            self.inputs,
            self.events.append,
            closing=lambda: self.closing is not None,
            request_close=self.close,
        )

    def close(self) -> asyncio.Task[None]:
        """Own one resource shutdown without waiting for the conversation stop task."""
        if self.closing is None:
            self.closing = asyncio.create_task(self._close())
            self.execution.connection_closing()
        return self.closing

    def receive(self, event: "RuntimeEvent") -> None:
        """Route after both objects are assembled."""
        self.execution.receive(event)

    async def _close(self) -> None:
        self.inputs.stop()
        await self.connection.close()
        await self.execution.drain()
        await self.inputs.drain_input()
        await self.resources.close()

    def turn(self, method: str, turn_id: str = "turn-1") -> None:
        """Deliver a turn fact independently of any RPC response."""
        self.execution.receive(
            RpcNotification(
                method,
                {
                    "threadId": "thread-1",
                    "turn": {"id": turn_id, "status": "completed"},
                },
            )
        )


def test_additional_message_uses_same_start_and_prepared_endpoint(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        try:
            assert await c.execution.send("右へ") == "turn-1"
            assert c.execution.snapshot.turn_id == "turn-1"
            c.peer.auto_turn_started = False
            assert await c.execution.send("左へ") == "turn-1"
            assert c.execution.snapshot.turn_id == "turn-1"
            c.turn("turn/completed")
            assert c.execution.snapshot.turn_id is None
            methods = [method for method, _ in c.peer.calls]
            assert methods.count("thread/start") == 1
            assert methods.count("turn/start") == 2
            assert "turn/steer" not in methods
            assert "thread/resume" not in methods
            assert "thread/unsubscribe" not in methods
        finally:
            await c.close()

    asyncio.run(scenario())


def test_late_acceptance_cannot_restore_a_completed_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        c.peer.auto_turn_started = False
        reply = asyncio.Event()
        c.peer.gates["turn/start"] = reply
        await c.connection.open()
        try:
            sending = asyncio.create_task(c.execution.send("確認して"))
            await _until(lambda: any(method == "turn/start" for method, _ in c.peer.calls))
            c.turn("turn/started")
            c.turn("turn/completed")
            reply.set()
            assert await sending == "turn-1"
            assert c.execution.snapshot.turn_id is None
            assert not c.execution.snapshot.sending
        finally:
            reply.set()
            await c.close()

    asyncio.run(scenario())


def test_stop_collects_start_notification_after_the_acceptance_response(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        c.peer.auto_turn_started = False
        await c.connection.open()
        sending = asyncio.create_task(c.execution.send("右へ"))
        stopping: asyncio.Task[None] | None = None
        try:
            await _until(lambda: any(method == "turn/start" for method, _ in c.peer.calls))
            done, _pending = await asyncio.wait({sending}, timeout=0.02)
            assert not done
            assert c.execution.snapshot.turn_id is None
            stopping = asyncio.create_task(c.execution.stop())
            await _until(lambda: c.execution.snapshot.stopping)
            assert not stopping.done()
            c.turn("turn/started")
            await _until(lambda: any(method == "turn/interrupt" for method, _ in c.peer.calls))
            assert not stopping.done()
            c.turn("turn/completed")
            assert await sending == "turn-1"
            await stopping
            assert c.execution.snapshot.turn_id is None
            assert not c.execution.snapshot.stopping
            assert [params for method, params in c.peer.calls if method == "turn/interrupt"] == [
                {"threadId": "thread-1", "turnId": "turn-1"}
            ]
        finally:
            await c.close()
            await asyncio.gather(sending, *([stopping] if stopping else []), return_exceptions=True)

    asyncio.run(scenario())


def test_disconnect_releases_send_waiting_for_accepted_turn_notification(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        c.peer.auto_turn_started = False
        await c.connection.open()
        sending = asyncio.create_task(c.execution.send("右へ"))
        try:
            await _until(lambda: any(method == "turn/start" for method, _ in c.peer.calls))
            done, _pending = await asyncio.wait({sending}, timeout=0.02)
            assert not done
            await c.close()
            with pytest.raises(MessageDeliveryError):
                await sending
            assert not c.execution.snapshot.sending
        finally:
            await c.close()
            await asyncio.gather(sending, return_exceptions=True)

    asyncio.run(scenario())


def test_stop_during_preparation_prevents_submission_without_discarding_resources(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path)
        gate = asyncio.Event()
        c.peer.gates["thread/start"] = gate
        await c.connection.open()
        try:
            sending = asyncio.create_task(c.execution.send("右へ"))
            await _until(lambda: any(method == "thread/start" for method, _ in c.peer.calls))
            with pytest.raises(CodexConnectionError):
                await c.execution.send("左へ")
            await c.execution.stop()
            assert await sending is None
            assert not any(method == "turn/start" for method, _ in c.peer.calls)
            gate.set()
            assert await c.execution.send("次へ") == "turn-1"
        finally:
            gate.set()
            await c.close()

    asyncio.run(scenario())


def test_stop_then_disconnect_recovers_archive_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release, created = Event(), Event(), Event()
    writers: list[ProjectWriter] = []
    create = ProjectWriter.create

    def delayed_create(_cls: type[ProjectWriter], path: Path) -> ProjectWriter:
        entered.set()
        if not release.wait(5):
            raise TimeoutError
        writer = create(path)
        writers.append(writer)
        created.set()
        return writer

    monkeypatch.setattr(ProjectWriter, "create", classmethod(delayed_create))

    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        sending = asyncio.create_task(c.execution.send("右へ"))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            stopping = asyncio.create_task(c.execution.stop())
            await _until(lambda: c.execution.snapshot.stopping)
            await asyncio.sleep(0)  # Let preparation enter its resource-recovery await.
            closing = c.close()
            release.set()
            await closing
            await asyncio.gather(sending, stopping, return_exceptions=True)
            assert await asyncio.to_thread(created.wait, 5)
            assert not any(method == "turn/start" for method, _ in c.peer.calls)
            archive = next((tmp_path / "chats").iterdir())
            ProjectWriter.open(archive).close()
        finally:
            release.set()
            await c.close()
            for writer in writers:
                writer.close()

    asyncio.run(scenario())
