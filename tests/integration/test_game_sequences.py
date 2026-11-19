import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from gigas.game_io.input.models import InputState, InputStep
from gigas.game_io.observation import Observation
from gigas.services._game_io_tools import GameOperations
from gigas.storage.records import GameIoRecordWriter
from tests.integration.test_conversation_execution import Conversation
from tests.integration.test_game_io_mcp import client_for, initialize, server_for
from tests.integration.test_input_service import Device, Harness, press

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def game(tmp_path: Path) -> "Iterator[Harness]":
    writer = GameIoRecordWriter.create(tmp_path / "game")
    try:
        yield Harness(writer)
    finally:
        writer.close()


async def call(
    client: httpx.AsyncClient, url: str, name: str, **arguments: object
) -> tuple[dict, list]:
    response = await client.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    response.raise_for_status()
    blocks = response.json()["result"]["content"]
    return json.loads(blocks[-1]["text"]), blocks[:-1]


def test_http_dispatch_poll_cancel_and_retention(game: Harness) -> None:
    async def scenario() -> None:
        await game.service.connect(game.device)
        server = server_for(game)
        await server.start()
        try:
            async with client_for(server) as client:
                await initialize(client, server.url)

                async def dispatch(**step: object) -> str:
                    data, images = await call(client, server.url, "dispatch", sequence=[step])
                    assert not images
                    return data["dispatch_id"]

                first = await dispatch(kind="press", buttons=["RIGHT"], duration_ms=60000)
                await game.device.pressed.wait()
                progress, images = await call(client, server.url, "poll", dispatch_id=first)
                assert (progress["step"], progress["completed_steps"], progress["total_steps"]) == (
                    1,
                    0,
                    1,
                )
                assert len(images) == 1
                for name in ("dispatch", "act_and_observe"):
                    busy, _ = await call(client, server.url, name, sequence=[press().to_json()])
                    assert busy["code"] == "BUSY"
                unknown, _ = await call(client, server.url, "cancel", dispatch_id="wrong")
                assert unknown["code"] == "UNKNOWN_DISPATCH"
                stopped = await call(client, server.url, "cancel", dispatch_id=first)
                assert stopped[0]["status"] == "interrupted"
                assert stopped[0]["neutral"] == "sent"
                assert len(stopped[1]) == 1
                second = await dispatch(kind="wait", duration_ms=200)
                assert await call(client, server.url, "poll", dispatch_id=first) == stopped
                async with asyncio.timeout(3):
                    while True:
                        final, _ = await call(client, server.url, "poll", dispatch_id=second)
                        if final["status"] == "completed":
                            break
                        await asyncio.sleep(0.02)
                assert final["completed_steps"] == 1
                assert (await call(client, server.url, "cancel", dispatch_id=second))[0] == final
                assert (await call(client, server.url, "poll", dispatch_id=first))[0][
                    "code"
                ] == "UNKNOWN_DISPATCH"
        finally:
            await server.close()
            await game.close()

    asyncio.run(scenario())


def test_sync_periodic_images_and_preflight(game: Harness) -> None:
    async def scenario() -> None:
        await game.service.connect(game.device)
        server = server_for(game)
        await server.start()
        try:
            async with client_for(server) as client:
                await initialize(client, server.url)
                invalid, _ = await call(
                    client,
                    server.url,
                    "act_and_observe",
                    sequence=[press(duration=20000).to_json()],
                    capture_interval_ms=500,
                )
                assert invalid["min_interval_ms"] == 625
                assert game.device.calls == ["open"]
                data, images = await call(
                    client,
                    server.url,
                    "act_and_observe",
                    sequence=[press(duration=300).to_json(), press("B", 450).to_json()],
                    capture_interval_ms=200,
                )
                assert data["status"] == "completed"
                slots = data["samples"]
                assert [slot["offset_ms"] for slot in slots] == [200, 400, 600]
                assert [slot["step"] for slot in slots] == [1, 2, 2]
                assert len(images) == 4
                assert all(image["type"] == "image" for image in images)
                assert game.device.calls == [
                    "open",
                    InputState(buttons=("A",)),
                    InputState(buttons=("B",)),
                    InputState(),
                ]
        finally:
            await server.close()
            await game.close()
        end = next(e["payload"] for e in game.records() if e["type"] == "input.operation_finished")
        assert isinstance(end, dict)
        assert [s["observation_id"] for s in end["samples"]] == [s["observation_id"] for s in slots]
        for image_id in [data["observation_id"], *(s["observation_id"] for s in slots)]:
            assert (game.writer.path / "observations" / f"{image_id}.png").is_file()

    asyncio.run(scenario())


def test_partial_samples_survive_failed_transition_during_slow_capture(
    game: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        capture_started = asyncio.Event()
        finish_capture = asyncio.Event()
        observe = game.observations.observe

        async def slow_capture() -> Observation:
            capture_started.set()
            await finish_capture.wait()
            return await observe()

        monkeypatch.setattr(game.observations, "observe", slow_capture)
        send = game.device.send

        async def fail_second_state(state: InputState) -> None:
            await send(state)
            if "B" in state.buttons:
                message = "transition failed"
                raise OSError(message)

        monkeypatch.setattr(game.device, "send", fail_second_state)
        operations = GameOperations(game.service.start, game.observations, game.recording)
        await game.service.connect(game.device)
        task = asyncio.create_task(operations.execute((press(duration=350), press("B", 500)), 80))
        try:
            async with asyncio.timeout(3):
                await capture_started.wait()
                await game.service.drain_input()
            assert not task.done()
            assert game.device.calls[-2:] == [InputState(), "close"]
            finish_capture.set()
            outcome = await task
            assert outcome.error
            assert outcome.metadata["completed_steps"] == 1
            assert outcome.metadata["neutral"] == "sent"
            assert outcome.metadata["status"] == "failed"
            assert outcome.samples[0].image is not None
            assert any(slot.image is None for slot in outcome.samples[1:])
            assert all(slot.offset_ms < 500 for slot in outcome.samples)
        finally:
            finish_capture.set()
            await task
            await game.close()

    asyncio.run(scenario())


def test_state_transitions_and_repress_use_only_explicit_release(game: Harness) -> None:
    async def scenario() -> None:
        right = InputState(buttons=("RIGHT",))
        chord = InputState(buttons=("RIGHT", "A"))
        neutral = InputState()
        await game.service.connect(game.device)
        sequence = tuple(InputStep("press", 1, state) for state in (right, chord, right, right))
        accepted = game.service.start(
            (*sequence, InputStep("wait", 1), InputStep("press", 1, right), InputStep("wait", 1))
        )
        assert isinstance(accepted, tuple)
        result = await accepted[1]
        assert result.completed_steps == 7
        assert result.neutral == "sent"
        assert game.device.calls == ["open", right, chord, right, neutral, right, neutral]
        await game.close()

    asyncio.run(scenario())


def test_turn_completion_releases_dispatch_and_keeps_progress_on_observation_failure(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        chat = Conversation(tmp_path)
        device = Device()
        await chat.connection.open()
        await chat.inputs.connect(device)
        try:
            await chat.execution.send("移動")
            config = next(
                p["config"] for method, p in chat.peer.calls if method == "thread/start" and p
            )
            assert isinstance(config, dict)
            url = next(v for k, v in config.items() if k.endswith(".url"))
            assert isinstance(url, str)
            headers = next(v for k, v in config.items() if k.endswith(".http_headers"))
            assert isinstance(headers, dict)
            auth = {k: str(v) for k, v in headers.items()}
            async with httpx.AsyncClient(
                headers={**auth, "accept": "application/json, text/event-stream"},
                trust_env=False,
            ) as client:
                await initialize(client, url)
                data, _ = await call(
                    client, url, "dispatch", sequence=[press(duration=60000).to_json()]
                )
                identifier = data["dispatch_id"]
                await device.pressed.wait()
                progress, images = await call(client, url, "poll", dispatch_id=identifier)
                assert not images
                assert progress["status"] == "running"
                assert progress["observation_error"]
                chat.turn("turn/completed")
                async with asyncio.timeout(3):
                    await chat.inputs.drain_input()
                stopped, _ = await call(client, url, "poll", dispatch_id=identifier)
                assert stopped["status"] == "interrupted"
                assert stopped["neutral"] == "sent"
                refused, _ = await call(client, url, "dispatch", sequence=[press().to_json()])
                assert refused["code"] == "INPUT_DISABLED"
        finally:
            await chat.close()
            await chat.inputs.close()

    asyncio.run(scenario())
