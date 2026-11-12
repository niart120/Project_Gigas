import asyncio
import json
from pathlib import Path

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.game_io.input.config import resolve_serial_config
from gigas.game_io.input.models import parse_sequence
from gigas.game_io.input.serial import SerialInput
from gigas.services.input import InputService
from gigas.services.observation import ObservationService
from gigas.services.recording import RecordingService
from gigas.storage.project import ProjectWriter
from tests.integration.test_observation_storage import sample_frame


class Port:
    """Capture actual encoded writes without contacting hardware."""

    def __init__(self) -> None:
        """Prepare write history and close evidence."""
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> int:
        """Accept a full local write."""
        self.writes.append(data)
        return len(data)

    def close(self) -> None:
        """Mark the transport as closed."""
        self.closed = True


def test_serial_sequence_and_latest_observation_share_recorded_outcome(tmp_path: Path) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    port = Port()
    original = sample_frame()

    def snapshot() -> CaptureSnapshot:
        assert len(port.writes) == 3  # Initial neutral, press, final neutral.
        return CaptureSnapshot(phase="streaming", generation=original.generation, frame=original)

    async def scenario() -> None:
        reader = CaptureReader(snapshot)
        recording = RecordingService(writer)
        observations = ObservationService(reader, recording)
        service = InputService()
        config = resolve_serial_config("COM3", "ch552", None)
        assert config is not None
        try:
            await service.connect(SerialInput(config, open_port=lambda _config: port))
            grant = await service.begin_run(
                "Aを短く押して待つ", "real-thread-id", observations, recording
            )
            sequence = parse_sequence(
                [
                    {"kind": "press", "buttons": ["A"], "duration_ms": 1},
                    {"kind": "wait", "duration_ms": 1},
                ],
                config.protocol,
            )
            result = await service.act(grant, sequence)
            assert result.code == "OK"
            assert result.completed_steps == 2
            assert result.neutral == "sent"
            assert result.observation is not None
            assert result.observation.source_frame.sequence == original.sequence
            assert result.observation.source_frame.generation == original.generation
            await service.finish_run("completed")
        finally:
            await service.close()
            await observations.drain()
            await recording.drain()
        rows = [json.loads(row) for row in (writer.path / "events.jsonl").read_bytes().splitlines()]
        assert [row["type"] for row in rows] == [
            "input.run_started",
            "input.operation_started",
            "observation.recorded",
            "input.operation_finished",
            "input.run_finished",
        ]
        recorded = rows[2]["payload"]
        finished = rows[3]["payload"]
        assert finished["operation_id"] == result.operation_id
        assert (
            finished["observation_id"]
            == recorded["observation_id"]
            == result.observation.observation_id
        )
        assert (writer.path / recorded["image_path"]).read_bytes() == result.observation.png_bytes
        assert rows[1]["payload"]["sequence"] == [step.to_json() for step in sequence]
        assert all(
            "port" not in row["payload"] and "baudrate" not in row["payload"] for row in rows
        )

    try:
        asyncio.run(scenario())
    finally:
        writer.close()
    neutral = bytes.fromhex("AB 00 00 08 80 80 80 80 00 00 00")
    assert port.writes == [neutral, bytes.fromhex("AB 04 00 08 80 80 80 80 00 00 00"), neutral]
    assert port.closed
