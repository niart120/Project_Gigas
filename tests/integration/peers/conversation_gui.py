"""A real application shell driven by public protocol replies without a model."""

import asyncio
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from threading import Event

from pytestqt.qtbot import QtBot

from gigas.app.shell import ApplicationShell
from gigas.runtime.codex.connection import CodexConnection, RuntimeEvent
from gigas.runtime.codex.models import ConnectionOptions
from gigas.runtime.codex.transport import JsonObject, JsonValue, RpcNotification
from gigas.services.application import ApplicationServiceThread, ServiceResult
from tests.integration.peers.codex_resume import ResumePeer
from tests.integration.peers.controlled_capture import ControlledCapture
from tests.unit.test_codex_history import _thread


class ConversationGuiPeer(ResumePeer):
    """Serve one saved unassigned conversation and optionally hold a model update."""

    def __init__(self, root: Path) -> None:
        """Retain public saved metadata separately from runtime request acceptance."""
        super().__init__()
        thread = {**_thread(root), "id": "saved", "name": "保存会話"}
        self.responses.update(
            {
                "thread/list": {"data": [thread], "nextCursor": None},
                "thread/read": {"thread": thread},
                "thread/turns/list": {
                    "data": [
                        {
                            "id": "old-turn",
                            "status": "completed",
                            "items": [],
                            "itemsView": "notLoaded",
                        }
                    ],
                },
                "thread/items/list": {
                    "data": [
                        {
                            "turnId": "old-turn",
                            "item": {
                                "id": "old-message",
                                "type": "agentMessage",
                                "text": "保存された本文",
                            },
                        }
                    ],
                    "nextCursor": None,
                },
                "thread/unsubscribe": {"status": "unsubscribed"},
            }
        )
        self.update_entered = Event()
        self.update_release: Event | None = None
        self.fast_mode_disabled = False

    async def request(self, method: str, params: JsonObject | None) -> JsonValue:
        """Use the existing resume fixture, delaying only an explicitly held update."""
        if method == "thread/settings/update" and self.update_release is not None:
            self.update_entered.set()
            if not await asyncio.to_thread(self.update_release.wait, 5):
                message = "model update was not released"
                raise TimeoutError(message)
        result = await super().request(method, params)
        if not self.fast_mode_disabled or not isinstance(result, dict):
            return result
        config = result.get("config")
        if method == "config/read" and isinstance(config, dict):
            return {**result, "config": {**config, "features": {"fast_mode": False}}}
        if method in {"thread/start", "thread/resume"} and result.get("serviceTier") == "default":
            return {**result, "serviceTier": None}
        return result


class ConversationGui:
    """Keep a Shell, real service, and observable operation results for GUI tests."""

    def __init__(self, qtbot: QtBot, root: Path) -> None:
        """Wire a deterministic peer and capture substitute without opening devices."""
        self.qtbot = qtbot
        self.peer = ConversationGuiPeer(root)
        self.peers = [self.peer]
        self.results: list[ServiceResult] = []
        self.options: list[ConnectionOptions] = []
        capture = ControlledCapture()

        def factory(
            options: ConnectionOptions, emit: Callable[[RuntimeEvent], None]
        ) -> CodexConnection:
            if self.options:
                previous = self.peer
                self.peer = ConversationGuiPeer(root)
                self.peer.goal = previous.goal
                self.peers.append(self.peer)
            self.options.append(options)
            return CodexConnection(self.peer, options, emit)

        self.service = ApplicationServiceThread(root, capture.reader(), factory)
        self.service.completed.connect(self.results.append)
        self.shell = ApplicationShell(
            root / "settings.toml", service=self.service, capture_service=capture
        )
        self.panel = self.shell.window.connection

    def command(self, operation: str, action: Callable[[], object]) -> ServiceResult:
        """Wait for one newly completed UI operation and return its actual service result."""
        before = len(self.results)
        action()
        self.qtbot.waitUntil(
            lambda: any(result.operation == operation for result in self.results[before:])
        )
        return next(result for result in self.results[before:] if result.operation == operation)

    def emit(self, *events: RpcNotification) -> None:
        """Queue public peer notifications on the service loop in the supplied order."""

        def publish() -> None:
            for event in events:
                self.peer.events.put_nowait(event)

        self.service._enqueue(publish)

    def params(self, method: str) -> list[JsonObject]:
        """Return nonempty request parameters in their observed call order."""
        return [
            params
            for peer in self.peers
            for name, params in peer.calls
            if name == method and params is not None
        ]


@contextmanager
def conversation_gui(qtbot: QtBot, root: Path) -> Generator[ConversationGui]:
    """Start and close the actual application around one isolated protocol test."""
    session = ConversationGui(qtbot, root)
    session.shell.start()
    qtbot.waitUntil(lambda: session.shell.ready)
    try:
        yield session
    finally:
        if session.peer.update_release is not None:
            session.peer.update_release.set()
        qtbot.waitUntil(lambda: session.shell.operation is None)
        session.shell.request_close()
        qtbot.waitUntil(lambda: not session.shell.window.isVisible())
