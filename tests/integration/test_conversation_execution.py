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
from gigas.runtime.codex.models import ConnectionOptions, ExecutionSettings, GoalState
from gigas.runtime.codex.transport import RpcNotification
from gigas.services._conversation_execution import ConversationExecution
from gigas.services._conversation_resources import ConversationResources
from gigas.services._conversation_selection import ConversationSelection
from gigas.services.input import InputService
from gigas.storage.records import GameIoRecordWriter
from tests.integration.peers.codex_resume import ResumePeer
from tests.unit.test_codex_connection import FakePeer, _until

if TYPE_CHECKING:
    from gigas.runtime.codex.connection import RuntimeEvent
    from gigas.runtime.codex.models import ConnectionEvent


class Conversation:
    """Use the real conversation/resources with a scripted runtime boundary."""

    def __init__(self, path: Path, *, peer: FakePeer | None = None) -> None:
        """Assemble independent connection and conversation owners before opening."""
        self.peer = peer if peer is not None else FakePeer()
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
        self.selection = ConversationSelection()
        self.execution = ConversationExecution(
            self.connection,
            self.resources,
            self.inputs,
            self.events.append,
            selection=self.selection,
            settings=ExecutionSettings(),
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
            turns = [params for method, params in c.peer.calls if method == "turn/start"]
            assert all(
                params is not None and params.get("summary") == "detailed" for params in turns
            )
            assert "turn/steer" not in methods
            assert "thread/resume" not in methods
            assert "thread/unsubscribe" not in methods
        finally:
            await c.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("use_goal", [False, True])
def test_summary_is_enabled_before_normal_or_goal_execution(
    tmp_path: Path, *, saved: bool, use_goal: bool
) -> None:
    async def scenario() -> None:
        c = Conversation(tmp_path, peer=ResumePeer())
        if saved:
            c.selection.adopt_started("thread-1", tmp_path, None)
            c.selection.retain_goal("thread-1", GoalState("進めて", "paused", 0, 0))
            c.peer.goal = {
                "objective": "進めて",
                "status": "paused",
                "tokensUsed": 0,
                "timeUsedSeconds": 0,
            }
        await c.connection.open()
        try:
            if use_goal:
                if saved:
                    assert await c.execution.resume_goal()
                else:
                    assert await c.execution.set_goal("進めて")
            else:
                assert await c.execution.send("進めて") == "turn-1"
            preparation = "thread/resume" if saved else "thread/start"
            methods = [method for method, _ in c.peer.calls]
            params = next(params for method, params in c.peer.calls if method == preparation)
            assert params is not None
            config = params["config"]
            assert isinstance(config, dict)
            assert config["model_reasoning_summary"] == "detailed"
            trigger = "thread/goal/set" if use_goal else "turn/start"
            assert methods.index(preparation) < methods.index(trigger)
            if use_goal:
                assert "turn/start" not in methods
            else:
                turn = next(params for method, params in c.peer.calls if method == trigger)
                assert turn is not None
                assert turn["summary"] == "detailed"
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


@pytest.mark.parametrize("after_stop", ["disconnect", "retry"])
def test_stop_during_bind_recovers_writer_for_retry_or_disconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_stop: str
) -> None:
    entered, release, created = Event(), Event(), Event()
    writers: list[GameIoRecordWriter] = []
    create = GameIoRecordWriter.create

    def delayed_create(_cls: type[GameIoRecordWriter], path: Path) -> GameIoRecordWriter:
        entered.set()
        if not release.wait(5):
            raise TimeoutError
        writer = create(path)
        writers.append(writer)
        created.set()
        return writer

    monkeypatch.setattr(GameIoRecordWriter, "create", classmethod(delayed_create))

    async def scenario() -> None:
        c = Conversation(tmp_path)
        await c.connection.open()
        sending = asyncio.create_task(c.execution.send("右へ"))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            stopping = asyncio.create_task(c.execution.stop())
            await _until(lambda: c.execution.snapshot.stopping)
            await asyncio.sleep(0)  # Let preparation enter its resource-recovery await.
            closing = c.close() if after_stop == "disconnect" else None
            release.set()
            if closing is not None:
                await closing
            await asyncio.gather(sending, stopping, return_exceptions=True)
            assert await asyncio.to_thread(created.wait, 5)
            assert not any(method == "turn/start" for method, _ in c.peer.calls)
            if after_stop == "retry":
                assert c.execution.snapshot.thread_id == "thread-1"
                assert await c.execution.send("次へ") == "turn-1"
                assert sum(method == "thread/start" for method, _ in c.peer.calls) == 1
                await c.close()
            archive = writers[0].path
            GameIoRecordWriter.open(archive).close()
        finally:
            release.set()
            await c.close()
            for writer in writers:
                writer.close()

    asyncio.run(scenario())
