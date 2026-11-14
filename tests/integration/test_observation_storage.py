import json
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID

import cv2
import numpy as np
import pytest

from gigas.game_io import observation as observation_module
from gigas.game_io.capture.models import Frame
from gigas.game_io.input.config import InputProtocol, get_protocol
from gigas.game_io.input.models import InputState, InputStep, InvalidSequenceError, parse_sequence
from gigas.game_io.observation import Observation
from gigas.storage.input_events import OperationFinished, OperationStarted
from gigas.storage.project import ProjectError, ProjectWriter

if TYPE_CHECKING:
    from collections.abc import Generator
    from typing import BinaryIO


def test_input_records_keep_sequence_results_and_observation_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    observation = Observation.from_frame(sample_frame())
    sequence = (
        InputStep("press", 100, InputState(buttons=("A",), touch=(30, 20))),
        InputStep("wait", 50),
    )
    try:
        writer.record_thread("runtime-thread")
        writer.record_input(OperationStarted("operation-1", sequence, "runtime-thread"))
        writer.record(observation, thread_id="runtime-thread")
        writer.record_input(
            OperationFinished(
                "operation-1",
                "completed",
                2,
                observation_id=observation.observation_id,
                input_sent="sent",
                neutral="sent",
            )
        )
    finally:
        writer.close()
    # A later capability change must affect new input, not existing archive rows.
    with monkeypatch.context() as patch:
        patch.setattr(InputProtocol, "touch", False)
        with pytest.raises(InvalidSequenceError):
            parse_sequence([step.to_json() for step in sequence], get_protocol("3ds"))
        reopened = ProjectWriter.open(writer.path)
        reopened.close()
    records = [json.loads(row) for row in (writer.path / "events.jsonl").read_bytes().splitlines()]
    assert [row["sequence"] for row in records] == list(range(1, 5))
    assert [row["type"] for row in records] == [
        "thread.started",
        "input.operation_started",
        "observation.recorded",
        "input.operation_finished",
    ]
    assert records[1]["payload"]["sequence"] == [step.to_json() for step in sequence]
    assert records[1]["payload"]["thread_id"] == "runtime-thread"
    assert records[1]["payload"]["operation_id"] == records[3]["payload"]["operation_id"]
    assert "turn_id" not in records[1]["payload"]
    assert records[3]["payload"]["observation_id"] == observation.observation_id
    assert records[3]["payload"]["completed_steps"] == 2
    assert all(
        not {"port", "baud", "baudrate", "protocol", "run_id", "request_id"} & row["payload"].keys()
        for row in records
    )


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        (
            "input.run_started",
            {"request_id": "r", "run_id": "run", "thread_id": "", "message": "x"},
        ),
        (
            "input.operation_started",
            {"request_id": "r", "run_id": "run", "operation_id": "op", "sequence": []},
        ),
        (
            "input.operation_finished",
            {
                "run_id": "run",
                "operation_id": "op",
                "result": "completed",
                "completed_steps": -1,
                "input_sent": "none",
                "neutral": "not_needed",
            },
        ),
        ("input.run_finished", {"request_id": "r", "run_id": "run", "reason": ""}),
    ],
)
def test_old_input_payload_is_preserved_without_current_semantic_validation(
    tmp_path: Path, kind: str, payload: dict[str, object]
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    writer.close()
    row = event(writer.project_id)
    row["type"], row["payload"] = kind, payload
    log = writer.path / "events.jsonl"
    original = (json.dumps(row) + "\n").encode()
    log.write_bytes(original)
    reopened = ProjectWriter.open(writer.path)
    reopened.close()
    assert log.read_bytes() == original


def test_create_project_and_preserve_existing_directory(tmp_path: Path) -> None:
    path = tmp_path / "game"
    writer = ProjectWriter.create(path)
    try:
        metadata = json.loads((path / "project.json").read_text(encoding="utf-8"))
        assert metadata["schema_version"] == 1
        assert UUID(metadata["project_id"].removeprefix("project-")).version == 4
        assert datetime.fromisoformat(metadata["created_at"]).utcoffset() == timedelta(0)
        assert writer.project_id == metadata["project_id"]
        assert writer.path == path.resolve()
        assert (path / "events.jsonl").read_bytes() == b""
        assert (path / "observations").is_dir()
        with pytest.raises(ProjectError):
            ProjectWriter.create(path)
        assert json.loads((path / "project.json").read_text(encoding="utf-8")) == metadata
    finally:
        writer.close()


def test_creation_failure_leaves_partial_data_uninitialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "partial"
    mkdir = Path.mkdir

    def failing_mkdir(
        directory: Path, mode: int = 0o777, *, parents: bool = False, exist_ok: bool = False
    ) -> None:
        if directory.name == "observations":
            message = "disk failure"
            raise OSError(message)
        mkdir(directory, mode, parents=parents, exist_ok=exist_ok)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "mkdir", failing_mkdir)
        with pytest.raises(ProjectError, match="disk failure"):
            ProjectWriter.create(path)
    assert path.is_dir()
    assert not (path / "project.json").exists()
    with pytest.raises(ProjectError):
        ProjectWriter.create(path)


def event(project_id: str, sequence: int = 3) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": "evt-0df99c55-52ee-4e0f-89f7-3aba321437db",
        "sequence": sequence,
        "occurred_at": "2026-11-12T00:00:00Z",
        "type": "future.event",
        "project_id": project_id,
        "payload": {"future": [1, "value"]},
        "extra": "preserved",
    }


@pytest.mark.parametrize("metadata_extra", [{}, {"created_at": {"legacy": "time"}}])
def test_open_preserves_unknown_events_fields_and_sequence_gaps(
    tmp_path: Path, metadata_extra: dict[str, object]
) -> None:
    path = tmp_path / "existing"
    writer = ProjectWriter.create(path)
    writer.close()
    project_id = "legacy-game"
    metadata = json.dumps({"schema_version": 1, "project_id": project_id, **metadata_extra})
    (path / "project.json").write_text(metadata, encoding="utf-8")
    first, second = event(project_id, 3), event(project_id, 9)
    first.pop("event_id")
    first.pop("occurred_at")
    second.update(event_id=["legacy", "id"], occurred_at=False)
    original = "".join(json.dumps(row) + "\n" for row in (first, second))
    (path / "events.jsonl").write_text(original, encoding="utf-8")
    opened = ProjectWriter.open(path)
    try:
        assert opened.project_id == project_id
        opened.reload()
        assert (path / "events.jsonl").read_text(encoding="utf-8") == original
        opened.record(Observation.from_frame(sample_frame()))
        lines = (path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert json.loads(lines[-1])["sequence"] == 10
        assert "\n".join(lines[:-1]) + "\n" == original
        assert (path / "project.json").read_text(encoding="utf-8") == metadata
    finally:
        opened.close()


@pytest.mark.parametrize(
    "bad_line",
    [b"{", b"{}\n", b"\xff\n", b"\n", b'{"number":NaN}\n'],
)
def test_bad_log_reports_line_and_preserves_prefix_and_tail(
    tmp_path: Path, bad_line: bytes
) -> None:
    path = tmp_path / "broken"
    writer = ProjectWriter.create(path)
    valid = (json.dumps(event(writer.project_id)) + "\n").encode()
    writer.close()
    original = valid + bad_line
    (path / "events.jsonl").write_bytes(original)
    with pytest.raises(ProjectError, match=r"行2.*正常範囲.*1"):
        ProjectWriter.open(path)
    assert (path / "events.jsonl").read_bytes() == original


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("sequence", True),
        ("sequence", 0),
        ("sequence", 3),
        ("type", ""),
        ("type", None),
        ("project_id", "project-wrong"),
    ],
)
def test_invalid_event_envelope_is_rejected(tmp_path: Path, key: str, value: object) -> None:
    path = tmp_path / "broken"
    writer = ProjectWriter.create(path)
    first, second = event(writer.project_id), event(writer.project_id, 4)
    second[key] = value
    writer.close()
    original = (json.dumps(first) + "\n" + json.dumps(second) + "\n").encode()
    (path / "events.jsonl").write_bytes(original)
    with pytest.raises(ProjectError, match="行2"):
        ProjectWriter.open(path)
    assert (path / "events.jsonl").read_bytes() == original


@pytest.mark.parametrize("metadata", ["{}", '{"schema_version":2}', "NaN"])
def test_unknown_or_invalid_project_is_not_initialized(tmp_path: Path, metadata: str) -> None:
    path = tmp_path / "broken"
    writer = ProjectWriter.create(path)
    writer.close()
    (path / "project.json").write_text(metadata, encoding="utf-8")
    with pytest.raises(ProjectError):
        ProjectWriter.open(path)
    assert (path / "project.json").read_text(encoding="utf-8") == metadata


@pytest.mark.parametrize("project_id", [None, "", 1, True])
def test_open_requires_nonempty_project_identity(tmp_path: Path, project_id: object) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    writer.close()
    path = writer.path / "project.json"
    metadata = json.loads(path.read_bytes())
    metadata["project_id"] = project_id
    original = json.dumps(metadata).encode()
    path.write_bytes(original)
    with pytest.raises(ProjectError):
        ProjectWriter.open(writer.path)
    assert path.read_bytes() == original


@pytest.mark.parametrize("name", ["project.json", "events.jsonl"])
def test_json_depth_failure_releases_owner_even_while_error_is_retained(
    tmp_path: Path, name: str
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    writer.close()
    path = writer.path / name
    original = path.read_bytes()
    deep = b"[" * 5000 + b"0" + b"]" * 5000 + b"\n"
    path.write_bytes(deep)
    with pytest.raises(ProjectError) as retained:
        ProjectWriter.open(writer.path)
    assert retained.value.__cause__ is not None
    assert path.read_bytes() == deep
    # External editing is permitted only after the failed owner has released its lock.
    path.write_bytes(original)
    reopened = ProjectWriter.open(writer.path)
    reopened.close()


LOCK_PROBE = """
import sys
from pathlib import Path
from gigas.storage.project import ProjectError, ProjectWriter
try:
    writer = ProjectWriter.open(Path(sys.argv[1]))
except ProjectError:
    sys.exit(2)
writer.close()
"""


def test_archive_owner_allows_readers_and_memory_edits_but_excludes_other_writers(
    tmp_path: Path,
) -> None:
    writer = ProjectWriter.create(tmp_path / "locked")
    observation = Observation.from_frame(sample_frame())
    reader_probe = """
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
metadata = json.loads((root / "project.json").read_bytes())
record = json.loads((root / "events.jsonl").read_bytes())
assert metadata["project_id"] == record["project_id"]
assert (root / record["payload"]["image_path"]).read_bytes().startswith(bytes.fromhex("89504e47"))
(root / "memory").mkdir()
(root / "memory/current.md").write_text("child interpretation", encoding="utf-8")
"""
    try:
        writer.record(observation)
        before = (writer.path / "events.jsonl").read_bytes()
        with pytest.raises(ProjectError):
            ProjectWriter.open(writer.path)
        writer.reload()
        result = subprocess.run(  # noqa: S603 - fixed local Python probe
            [sys.executable, "-c", reader_probe + LOCK_PROBE, str(writer.path)],
            timeout=10,
            check=False,
        )
        assert (
            result.returncode == 2
        )  # Reading and memory editing succeeded; a second writer failed.
        assert (writer.path / "memory/current.md").read_text(
            encoding="utf-8"
        ) == "child interpretation"
        assert (writer.path / "events.jsonl").read_bytes() == before
        assert (writer.path / observation.image_path).read_bytes() == observation.png_bytes
    finally:
        writer.close()
    abrupt_exit = "import os\n" + LOCK_PROBE.replace("writer.close()", "os._exit(0)")
    for code in (abrupt_exit, LOCK_PROBE):
        result = subprocess.run(  # noqa: S603 - fixed local Python probe
            [sys.executable, "-c", code, str(writer.path)], timeout=10, check=False
        )
        assert result.returncode == 0


def sample_frame(width: int = 16, height: int = 16) -> Frame:
    pixels = np.random.default_rng(41).integers(0, 256, (height, width, 3), dtype=np.uint8)
    return Frame(pixels.tobytes(), width, height, 2, 7, datetime(2000, 1, 1, tzinfo=UTC), 123)


@pytest.mark.parametrize(
    ("width", "height", "resolution", "output_width", "output_height"),
    [
        (16, 16, None, 16, 16),
        (3840, 2160, "original", 3840, 2160),
        (3840, 2160, None, 1920, 1080),
        (3840, 2160, "hd", 1280, 720),
        (1920, 1440, "hd", 960, 720),
        (1921, 1081, "hd", 1279, 720),
    ],
)
def test_record_preserves_pixels_bytes_id_and_source_time(
    tmp_path: Path,
    width: int,
    height: int,
    resolution: Literal["original", "full_hd", "hd"] | None,
    output_width: int,
    output_height: int,
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    frame = sample_frame(width, height)
    if (width, height) != (output_width, output_height):
        # Four broad color regions reveal cropping, channel swaps and aspect distortion.
        pixels = np.zeros((height, width, 3), dtype=np.uint8)
        pixels[: height // 2, : width // 2] = [255, 0, 0]
        pixels[: height // 2, width // 2 :] = [0, 255, 0]
        pixels[height // 2 :, : width // 2] = [0, 0, 255]
        pixels[height // 2 :, width // 2 :] = [255, 255, 255]
        frame = replace(frame, bgr=pixels.tobytes())
    try:
        first = (
            Observation.from_frame(frame)
            if resolution is None
            else Observation.from_frame(frame, resolution=resolution)
        )
        second = Observation.from_frame(frame)
        writer.record_thread("thread-1")
        writer.record(first, thread_id="thread-1")
        writer.record(second, thread_id="thread-1")
        assert first.observation_id != second.observation_id
        assert first.captured_at == frame.captured_at
        assert (first.source_frame.generation, first.source_frame.sequence) == (2, 7)
        assert (first.source_frame.width, first.source_frame.height) == (width, height)
        assert (first.width, first.height, first.mime_type) == (
            output_width,
            output_height,
            "image/png",
        )
        assert (writer.path / first.image_path).read_bytes() == first.png_bytes
        decoded = cv2.imdecode(np.frombuffer(first.png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        assert decoded is not None
        assert decoded.shape == (output_height, output_width, 3)
        if (width, height) == (output_width, output_height):
            assert decoded.tobytes() == frame.bgr
        else:
            assert decoded[0, 0].tolist() == [255, 0, 0]
            assert decoded[0, -1].tolist() == [0, 255, 0]
            assert decoded[-1, 0].tolist() == [0, 0, 255]
            assert decoded[-1, -1].tolist() == [255, 255, 255]
            assert decoded[output_height // 4, output_width // 4].tolist() == [255, 0, 0]
            assert decoded[3 * output_height // 4, 3 * output_width // 4].tolist() == [
                255,
                255,
                255,
            ]
        assert len(first.png_bytes) <= 32 * 1024 * 1024
        records = [
            json.loads(line) for line in (writer.path / "events.jsonl").read_bytes().splitlines()
        ]
        assert [record["sequence"] for record in records] == [1, 2, 3]
        assert records[0]["type"] == "thread.started"
        assert records[0]["payload"] == {"runtime": "codex", "thread_id": "thread-1"}
        assert records[0]["event_id"] != records[1]["event_id"]
        assert records[1]["payload"] == {
            "thread_id": "thread-1",
            "observation_id": first.observation_id,
            "captured_at": "2000-01-01T00:00:00Z",
            "image_path": first.image_path,
            "image": {"mime_type": "image/png", "width": output_width, "height": output_height},
            "source_frame": {"generation": 2, "sequence": 7, "width": width, "height": height},
        }
        assert datetime.fromisoformat(records[0]["occurred_at"]) > frame.captured_at
    finally:
        writer.close()
    log = writer.path / "events.jsonl"
    original = log.read_bytes()
    (writer.path / first.image_path).unlink()
    reopened = ProjectWriter.open(writer.path)
    try:
        assert log.read_bytes() == original
        reopened.record_thread("thread-2")
        reopened.record(Observation.from_frame(frame), thread_id="thread-2")
        assert log.read_bytes().startswith(original)
        records = [
            json.loads(line) for line in (writer.path / "events.jsonl").read_bytes().splitlines()
        ]
        assert records[-1]["sequence"] == 5
        assert records[-1]["payload"]["thread_id"] == "thread-2"
    finally:
        reopened.close()


def test_historical_observation_payload_is_not_reinterpreted(tmp_path: Path) -> None:
    writer = ProjectWriter.create(tmp_path / "broken")
    try:
        writer.record(Observation.from_frame(sample_frame()))
    finally:
        writer.close()
    log = writer.path / "events.jsonl"
    record = json.loads(log.read_bytes())
    record["payload"] = {"image_path": "old-format/image.png", "source_frame": {}}
    original = (json.dumps(record) + "\n").encode()
    log.write_bytes(original)
    ProjectWriter.open(writer.path).close()
    assert log.read_bytes() == original


@pytest.mark.parametrize("observation_id", ["../escaped", "obs-../../escaped"])
def test_observation_path_cannot_escape_storage(tmp_path: Path, observation_id: str) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    try:
        observation = Observation.from_frame(sample_frame())
        with pytest.raises(ProjectError):
            writer.record(replace(observation, observation_id=observation_id))
        assert (writer.path / "events.jsonl").read_bytes() == b""
        assert list((writer.path / "observations").iterdir()) == []
        assert not (tmp_path / "escaped.png").exists()
        writer.record(observation)
    finally:
        writer.close()
    reopened = ProjectWriter.open(writer.path)
    reopened.close()


@pytest.mark.parametrize("failure", ["publish", "collision"])
def test_image_failure_preserves_log_and_writer_remains_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    try:
        first = Observation.from_frame(sample_frame())
        writer.record(first)
        log = writer.path / "events.jsonl"
        before = log.read_bytes()
        with monkeypatch.context() as patch:
            if failure == "collision":
                patch.setattr(observation_module, "uuid4", lambda: UUID(first.observation_id[4:]))
            else:

                def broken_publish(_path: Path, _target: Path) -> Path:
                    message = "image publish failure"
                    raise OSError(message)

                patch.setattr(Path, "rename", broken_publish)
            with pytest.raises(ProjectError):
                writer.record(Observation.from_frame(sample_frame()))
        assert log.read_bytes() == before
        assert (writer.path / first.image_path).read_bytes() == first.png_bytes
        assert len(list((writer.path / "observations").iterdir())) == 1
        writer.record(Observation.from_frame(sample_frame()))
        assert json.loads(log.read_bytes().splitlines()[-1])["sequence"] == 2
    finally:
        writer.close()


@pytest.mark.parametrize("failure", ["partial", "close"])
def test_uncertain_log_preserves_data_and_requires_explicit_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    try:
        writer.record(Observation.from_frame(sample_frame()))
        log = writer.path / "events.jsonl"
        before = log.read_bytes()
        open_file = Path.open
        with monkeypatch.context() as patch:

            @contextmanager
            def failing_open(path: Path, mode: str, *, buffering: int) -> "Generator[BinaryIO]":
                assert path == log
                assert mode == "ab"
                with open_file(path, "ab", buffering=buffering) as stream:
                    if failure == "partial":
                        write = stream.write
                        patch.setattr(stream, "write", lambda row: write(row[: len(row) // 2]))
                    yield stream
                if failure == "close":
                    message = "log close failure"
                    raise OSError(message)

            patch.setattr(Path, "open", failing_open)
            with pytest.raises(ProjectError, match="未確定"):
                writer.record(Observation.from_frame(sample_frame()))
        after = log.read_bytes()
        assert after.startswith(before)
        assert len(after) > len(before)
        assert len(list((writer.path / "observations").glob("*.png"))) == 2
        with pytest.raises(ProjectError, match="再読込"):
            writer.record(Observation.from_frame(sample_frame()))
        assert log.read_bytes() == after
        assert len(list((writer.path / "observations").glob("*.png"))) == 2
        if failure == "partial":
            with pytest.raises(ProjectError, match="行2"):
                writer.reload()
            assert log.read_bytes() == after
        else:
            writer.reload()
            writer.record(Observation.from_frame(sample_frame()))
            assert json.loads(log.read_bytes().splitlines()[-1])["sequence"] == 3
    finally:
        writer.close()


@pytest.mark.parametrize("value", [float("nan"), "unpaired-\ud800"])
def test_row_encoding_failure_preserves_log_and_sequence(
    tmp_path: Path, value: float | str
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    try:
        writer.record_thread("thread")
        log = writer.path / "events.jsonl"
        before = log.read_bytes()
        with pytest.raises(ProjectError):
            writer.record_dialogue("dialogue.final", {"text": value}, thread_id="thread")
        assert log.read_bytes() == before
        writer.record_dialogue("dialogue.final", {"text": "次の記録"}, thread_id="thread")
        assert json.loads(log.read_bytes().splitlines()[-1])["sequence"] == 2
        writer.reload()
    finally:
        writer.close()


def test_observation_row_encoding_failure_keeps_published_image(tmp_path: Path) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    observation = Observation.from_frame(sample_frame())
    try:
        with pytest.raises(ProjectError):
            writer.record(observation, thread_id="unpaired-\ud800")
        assert (writer.path / observation.image_path).read_bytes() == observation.png_bytes
        assert (writer.path / "events.jsonl").read_bytes() == b""
        writer.record_thread("thread")
        assert json.loads((writer.path / "events.jsonl").read_bytes())["sequence"] == 1
    finally:
        writer.close()


def test_memory_bootstrap_preserves_existing_memory_and_records(tmp_path: Path) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    try:
        memory = writer.ensure_memory()
        assert memory == writer.path / "memory"
        assert (memory / "current.md").is_file()
        assert (writer.path / "guide.md").is_file()
        (memory / "current.md").write_bytes(b"existing-memory")
        (writer.path / "guide.md").write_bytes(b"custom-guide")
        writer.record_dialogue("dialogue.goal", {"objective": "test"}, thread_id="thread")
        original = (writer.path / "events.jsonl").read_bytes()
        assert writer.ensure_memory() == memory
        assert (memory / "current.md").read_bytes() == b"existing-memory"
        assert (writer.path / "guide.md").read_bytes() == b"custom-guide"
        assert (writer.path / "events.jsonl").read_bytes() == original
    finally:
        writer.close()
    reopened = ProjectWriter.open(writer.path)
    reopened.close()
