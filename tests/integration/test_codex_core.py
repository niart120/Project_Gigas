import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.game_io.input.models import InputState, InputStep
from gigas.runtime.codex.connection import CodexConnection
from gigas.runtime.codex.models import ConnectionOptions
from gigas.services.codex import CodexService
from gigas.services.codex_models import MessageResult
from gigas.services.input import InputService
from tests.integration.test_input_service import Device
from tests.unit.test_codex_connection import FakePeer

if TYPE_CHECKING:
    from collections.abc import Callable

    from gigas.runtime.codex.connection import RuntimeEvent
    from gigas.services.codex_models import ServiceResult


def test_codex_lifecycle_on_callers_loop_retains_borrowed_input(tmp_path: Path) -> None:
    async def scenario() -> None:
        peer = FakePeer()
        inputs = InputService()
        device = Device()
        await inputs.connect(device)
        results: asyncio.Queue[ServiceResult] = asyncio.Queue()

        def factory(
            options: ConnectionOptions, emit: "Callable[[RuntimeEvent], None]"
        ) -> CodexConnection:
            return CodexConnection(peer, options, emit)

        service = CodexService(
            tmp_path,
            CaptureReader(CaptureSnapshot),
            inputs,
            factory=factory,
            received=lambda _event: None,
            completed=results.put_nowait,
            project_completed=lambda _result: None,
            management_completed=lambda _result: None,
            browse_goal_changed=lambda _generation, _goal: None,
            recording_failed=lambda _operation, _path, _reason: None,
        )
        try:
            service.connect_runtime(ConnectionOptions(tmp_path), 7)
            connected = await asyncio.wait_for(results.get(), 5)
            assert (connected.generation, connected.operation, connected.error) == (
                7,
                "connect",
                "",
            )
            service.send("進めて")
            sent = await asyncio.wait_for(results.get(), 5)
            assert sent.message == MessageResult("accepted", "turn-1")
            service.disconnect_runtime()
            disconnected = await asyncio.wait_for(results.get(), 5)
            assert (disconnected.operation, disconnected.error) == ("disconnect", "")
            await service.close()
            assert peer.closed
            assert inputs.connected
            started = inputs.start((InputStep("press", 1, InputState(buttons=("A",))),))
            assert isinstance(started, tuple)
            assert (await started[1]).code == "OK"
            assert device.calls[-2:] == [InputState(buttons=("A",)), InputState()]
        finally:
            await service.close()
            await inputs.close()

    asyncio.run(scenario())
