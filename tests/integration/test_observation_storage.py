import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Literal
from uuid import UUID

import cv2
import numpy as np
import pytest

from gigas.game_io import observation as observation_module
from gigas.game_io.capture.models import Frame
from gigas.game_io.input.models import InputState, InputStep
from gigas.game_io.observation import Observation
from gigas.storage import project
from gigas.storage.input_events import OperationFinished, OperationStarted, RunFinished, RunStarted
from gigas.storage.project import ProjectError, ProjectWriter


def test_input_records_keep_sequence_results_and_observation_reference(tmp_path: Path) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    observation = Observation.from_frame(sample_frame())
    sequence = (InputStep("press", 100, InputState(buttons=("A",))), InputStep("wait", 50))
    try:
        writer.record_thread("runtime-thread")
        writer.record_input(RunStarted("request-1", "run-1", "runtime-thread", "Aを押す"))
        writer.record_input(OperationStarted("request-1", "run-1", "operation-1", sequence))
        writer.record(observation, thread_id="runtime-thread")
        writer.record_input(
            OperationFinished(
                "run-1",
                "operation-1",
                "completed",
                2,
                observation_id=observation.observation_id,
                input_sent="sent",
                neutral="sent",
            )
        )
        writer.record_input(RunFinished("request-1", "run-1", "completed", turn_id="turn-1"))
    finally:
        writer.close()
    reopened = ProjectWriter.open(writer.path)
    reopened.close()
    records = [json.loads(row) for row in (writer.path / "events.jsonl").read_bytes().splitlines()]
    assert [row["sequence"] for row in records] == list(range(1, 7))
    assert [row["type"] for row in records] == [
        "thread.started",
        "input.run_started",
        "input.operation_started",
        "observation.recorded",
        "input.operation_finished",
        "input.run_finished",
    ]
    assert records[2]["payload"]["sequence"] == [step.to_json() for step in sequence]
    assert "turn_id" not in records[2]["payload"]
    assert records[4]["payload"]["observation_id"] == observation.observation_id
    assert records[4]["payload"]["completed_steps"] == 2
    assert records[5]["payload"]["turn_id"] == "turn-1"
    assert all(
        not {"port", "baud", "baudrate", "protocol"} & row["payload"].keys() for row in records
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
def test_input_record_read_rejects_invalid_known_payload(
    tmp_path: Path, kind: str, payload: dict[str, object]
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    writer.close()
    row = event(writer.project_id)
    row["type"], row["payload"] = kind, payload
    log = writer.path / "events.jsonl"
    original = (json.dumps(row) + "\n").encode()
    log.write_bytes(original)
    with pytest.raises(ProjectError, match="行1"):
        ProjectWriter.open(writer.path)
    assert log.read_bytes() == original


def test_invalid_input_record_is_rejected_before_append(tmp_path: Path) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    try:
        with pytest.raises(ProjectError):
            writer.record_input(OperationFinished("run", "op", "failed", -1))
        assert (writer.path / "events.jsonl").read_bytes() == b""
        writer.record_input(RunStarted("request", "run", "thread", "message"))
    finally:
        writer.close()


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


def test_open_preserves_unknown_events_fields_and_sequence_gaps(tmp_path: Path) -> None:
    path = tmp_path / "existing"
    writer = ProjectWriter.create(path)
    project_id = writer.project_id
    writer.close()
    original = "".join(json.dumps(event(project_id, number)) + "\n" for number in (3, 9))
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
        ("sequence", 3),
        ("event_id", "evt-invalid"),
        ("type", ""),
        ("project_id", "project-wrong"),
        ("occurred_at", "2026-11-12T00:00:00"),
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


@pytest.mark.parametrize(
    "timestamp",
    ["20261112T00Z", "2026-W46-4T00:00:00Z", "2026-11-12T00:00Z", "2026-11-12T00:00:00,1Z"],
)
def test_non_rfc3339_time_is_rejected(tmp_path: Path, timestamp: str) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    writer.close()
    path = writer.path / "project.json"
    metadata = json.loads(path.read_bytes())
    metadata["created_at"] = timestamp
    original = json.dumps(metadata).encode()
    path.write_bytes(original)
    with pytest.raises(ProjectError, match="RFC 3339"):
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
    return Frame(pixels.tobytes(), width, height, 2, 7, datetime(2000, 1, 1, tzinfo=UTC), 123, 100)


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


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("image_path", "../other.png"),
        ("observation_id", "obs-invalid"),
        ("source_frame", {}),
        ("captured_at", "yesterday"),
        ("thread_id", ""),
        ("thread_id", " "),
        ("thread_id", 42),
        ("image", {"mime_type": "image/jpeg", "width": 16, "height": 16}),
        ("image", {"mime_type": "image/png", "width": 0, "height": 16}),
        ("image", {"mime_type": "image/png", "width": 32, "height": 16}),
    ],
)
def test_invalid_recorded_observation_is_rejected_without_rewriting(
    tmp_path: Path, key: str, value: object
) -> None:
    writer = ProjectWriter.create(tmp_path / "broken")
    try:
        writer.record(Observation.from_frame(sample_frame()))
    finally:
        writer.close()
    log = writer.path / "events.jsonl"
    record = json.loads(log.read_bytes())
    record["payload"][key] = value
    original = (json.dumps(record) + "\n").encode()
    log.write_bytes(original)
    with pytest.raises(ProjectError, match="行1"):
        ProjectWriter.open(writer.path).close()
    assert log.read_bytes() == original


@pytest.mark.parametrize("field", ["generation", "sequence"])
def test_invalid_source_identifiers_cannot_create_unreadable_records(
    tmp_path: Path, field: str
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    try:
        invalid = replace(sample_frame(), **{field: True})
        with pytest.raises(ProjectError):
            writer.record(Observation.from_frame(invalid))
        assert (writer.path / "events.jsonl").read_bytes() == b""
        assert list((writer.path / "observations").iterdir()) == []
        writer.record(Observation.from_frame(sample_frame()))
    finally:
        writer.close()
    reopened = ProjectWriter.open(writer.path)
    reopened.close()


@pytest.mark.parametrize("failure", ["png_sync", "collision"])
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

                def broken_sync(_descriptor: int) -> None:
                    message = "image sync failure"
                    raise OSError(message)

                patch.setattr(project.os, "fsync", broken_sync)
            with pytest.raises(ProjectError):
                writer.record(Observation.from_frame(sample_frame()))
        assert log.read_bytes() == before
        assert (writer.path / first.image_path).read_bytes() == first.png_bytes
        assert len(list((writer.path / "observations").iterdir())) == 1
        writer.record(Observation.from_frame(sample_frame()))
        assert json.loads(log.read_bytes().splitlines()[-1])["sequence"] == 2
    finally:
        writer.close()


@pytest.mark.parametrize("failure", ["partial", "sync"])
@pytest.mark.parametrize("kind", ["observation", "thread", "input"])
def test_uncertain_log_preserves_data_and_requires_explicit_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, kind: str
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    try:
        writer.record(Observation.from_frame(sample_frame()))
        log = writer.path / "events.jsonl"
        before = log.read_bytes()
        fsync = os.fsync
        with monkeypatch.context() as patch:
            if failure == "partial":

                def partial_append(path: Path, row: bytes) -> None:
                    with path.open("ab", buffering=0) as stream:
                        stream.write(row[: len(row) // 2])
                    message = "partial append failure"
                    raise OSError(message)

                patch.setattr(project, "_append_row", partial_append)
            else:
                inode = log.stat().st_ino

                def broken_log_sync(descriptor: int) -> None:
                    if os.fstat(descriptor).st_ino == inode:
                        message = "log sync failure"
                        raise OSError(message)
                    fsync(descriptor)

                patch.setattr(project.os, "fsync", broken_log_sync)
            if kind == "observation":
                append = partial(writer.record, Observation.from_frame(sample_frame()))
            elif kind == "thread":
                append = partial(writer.record_thread, "thread-1")
            else:
                append = partial(writer.record_input, RunStarted("request", "run", "thread", "A"))
            with pytest.raises(ProjectError, match="未確定"):
                append()
        after = log.read_bytes()
        assert after.startswith(before)
        assert len(after) > len(before)
        assert len(list((writer.path / "observations").glob("*.png"))) == (
            2 if kind == "observation" else 1
        )
        with pytest.raises(ProjectError, match="再読込"):
            writer.record(Observation.from_frame(sample_frame()))
        assert log.read_bytes() == after
        assert len(list((writer.path / "observations").glob("*.png"))) == (
            2 if kind == "observation" else 1
        )
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


@pytest.mark.parametrize("invalid", ["", "  ", "\n"])
def test_thread_record_rejects_empty_id_before_appending(tmp_path: Path, invalid: str) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    try:
        with pytest.raises(ProjectError):
            writer.record_thread(invalid)
        assert (writer.path / "events.jsonl").read_bytes() == b""
        writer.record_thread("opaque-runtime-id")
    finally:
        writer.close()
    reopened = ProjectWriter.open(writer.path)
    reopened.close()
    log = writer.path / "events.jsonl"
    record = json.loads(log.read_bytes())
    record["payload"]["thread_id"] = invalid
    malformed = (json.dumps(record) + "\n").encode()
    log.write_bytes(malformed)
    with pytest.raises(ProjectError, match="行1"):
        ProjectWriter.open(writer.path).close()
    assert log.read_bytes() == malformed
