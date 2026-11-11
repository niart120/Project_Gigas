import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

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
