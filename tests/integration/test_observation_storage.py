import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import cv2
import numpy as np
import pytest

from gigas.game_io.capture.models import Frame
from gigas.storage.project import ProjectError, ProjectWriter


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
        opened.record(sample_frame())
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


def test_writer_ownership_is_exclusive_across_processes(tmp_path: Path) -> None:
    writer = ProjectWriter.create(tmp_path / "locked")
    try:
        with pytest.raises(ProjectError):
            ProjectWriter.open(writer.path)
        writer.reload()
        result = subprocess.run(  # noqa: S603 - fixed local Python probe
            [sys.executable, "-c", LOCK_PROBE, str(writer.path)], timeout=10, check=False
        )
        assert result.returncode == 2
    finally:
        writer.close()
    result = subprocess.run(  # noqa: S603 - fixed local Python probe
        [sys.executable, "-c", LOCK_PROBE, str(writer.path)], timeout=10, check=False
    )
    assert result.returncode == 0


def test_process_exit_releases_lock_without_deleting_lock_file(tmp_path: Path) -> None:
    writer = ProjectWriter.create(tmp_path / "locked")
    writer.close()
    code = "import os\n" + LOCK_PROBE.replace("writer.close()", "os._exit(0)")
    result = subprocess.run(  # noqa: S603 - fixed local Python probe
        [sys.executable, "-c", code, str(writer.path)], timeout=10, check=False
    )
    assert result.returncode == 0
    assert (writer.path / ".gigas.lock").exists()
    reopened = ProjectWriter.open(writer.path)
    reopened.close()


def sample_frame(width: int = 16, height: int = 16) -> Frame:
    pixels = np.random.default_rng(41).integers(0, 256, (height, width, 3), dtype=np.uint8)
    return Frame(pixels.tobytes(), width, height, 2, 7, datetime(2000, 1, 1, tzinfo=UTC), 123)


@pytest.mark.parametrize(("width", "height"), [(16, 16), (3840, 2160)])
def test_record_preserves_pixels_bytes_id_and_source_time(
    tmp_path: Path, width: int, height: int
) -> None:
    writer = ProjectWriter.create(tmp_path / "game")
    frame = sample_frame(width, height)
    try:
        first = writer.record(frame)
        second = writer.record(frame)
        assert first.observation_id != second.observation_id
        assert first.frame is frame
        assert (writer.path / first.image_path).read_bytes() == first.png_bytes
        decoded = cv2.imdecode(np.frombuffer(first.png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        assert decoded is not None
        assert decoded.shape == (height, width, 3)
        assert decoded.tobytes() == frame.bgr
        assert len(first.png_bytes) <= 32 * 1024 * 1024
        records = [
            json.loads(line) for line in (writer.path / "events.jsonl").read_bytes().splitlines()
        ]
        assert [record["sequence"] for record in records] == [1, 2]
        assert records[0]["event_id"] != records[1]["event_id"]
        assert records[0]["payload"] == {
            "observation_id": first.observation_id,
            "captured_at": "2000-01-01T00:00:00Z",
            "image_path": first.image_path,
            "source_frame": {"generation": 2, "sequence": 7, "width": width, "height": height},
            "analysis": {},
        }
        assert datetime.fromisoformat(records[0]["occurred_at"]) > frame.captured_at
    finally:
        writer.close()
    (writer.path / first.image_path).unlink()
    reopened = ProjectWriter.open(writer.path)
    try:
        reopened.record(frame)
        records = [
            json.loads(line) for line in (writer.path / "events.jsonl").read_bytes().splitlines()
        ]
        assert records[-1]["sequence"] == 3
    finally:
        reopened.close()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("image_path", "../other.png"),
        ("observation_id", "obs-invalid"),
        ("source_frame", {}),
        ("analysis", []),
        ("captured_at", "yesterday"),
    ],
)
def test_invalid_recorded_observation_is_rejected_without_rewriting(
    tmp_path: Path, key: str, value: object
) -> None:
    writer = ProjectWriter.create(tmp_path / "broken")
    try:
        writer.record(sample_frame())
    finally:
        writer.close()
    log = writer.path / "events.jsonl"
    record = json.loads(log.read_bytes())
    record["payload"][key] = value
    original = (json.dumps(record) + "\n").encode()
    log.write_bytes(original)
    with pytest.raises(ProjectError, match="行1"):
        ProjectWriter.open(writer.path)
    assert log.read_bytes() == original
