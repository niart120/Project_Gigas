import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

import pytest

from gigas.game_io.capture.models import Frame
from gigas.game_io.input.models import InputState, InputStep
from gigas.game_io.observation import Observation
from gigas.storage import records as records_module
from gigas.storage.input_events import OperationFinished, OperationStarted
from gigas.storage.records import GameIoRecordWriter, RecordBusyError, RecordError

if TYPE_CHECKING:
    from collections.abc import Generator
    from typing import BinaryIO


def observation() -> Observation:
    frame = Frame(
        bytes([255, 0, 0]) * (32 * 16),
        32,
        16,
        2,
        7,
        datetime(2000, 1, 1, tzinfo=UTC),
        123,
    )
    return Observation.from_frame(frame)


def test_standalone_writer_records_five_events_without_project_or_conversation(
    tmp_path: Path,
) -> None:
    image = observation()
    sequence = (
        InputStep("press", 100, InputState(buttons=("A",), left_stick=(64, 0), touch=(30, 20))),
        InputStep("wait", 50),
    )
    with closing(GameIoRecordWriter.create(tmp_path / "records")) as writer:
        writer.record_dialogue("dialogue.submitted", "依頼\n次の行")
        writer.record_input(OperationStarted("operation-1", sequence))
        writer.record(image)
        writer.record_input(
            OperationFinished(
                "operation-1",
                "completed",
                2,
                observation_id=image.observation_id,
                input_sent="sent",
                neutral="sent",
            )
        )
        writer.record_dialogue("dialogue.message", "進捗です。")
        rows = (writer.path / "events.jsonl").read_bytes().splitlines()
        assert len(rows) == 5
        records = [json.loads(row) for row in rows]
        assert [(row["type"], row["payload"]) for row in records] == [
            ("dialogue.submitted", {"text": "依頼\n次の行"}),
            (
                "input.operation_started",
                {"operation_id": "operation-1", "sequence": [step.to_json() for step in sequence]},
            ),
            (
                "observation.recorded",
                {
                    "observation_id": image.observation_id,
                    "captured_at": "2000-01-01T00:00:00Z",
                    "image_path": image.image_path,
                    "image": {"width": 32, "height": 16},
                    "source_frame": {"width": 32, "height": 16},
                },
            ),
            (
                "input.operation_finished",
                {
                    "operation_id": "operation-1",
                    "result": "completed",
                    "completed_steps": 2,
                    "observation_id": image.observation_id,
                    "input_sent": "sent",
                    "neutral": "sent",
                },
            ),
            ("dialogue.message", {"text": "進捗です。"}),
        ]
        for row in records:
            assert set(row) == {"schema_version", "recorded_at", "type", "payload"}
            assert row["schema_version"] == 1
            assert row["recorded_at"].endswith("Z")
            assert datetime.fromisoformat(row["recorded_at"]) > image.captured_at
        assert (writer.path / image.image_path).read_bytes() == image.png_bytes
        assert {path.name for path in writer.path.iterdir()} == {
            ".gigas.lock",
            "events.jsonl",
            "observations",
        }


def test_input_outcome_keeps_observation_failure_without_inventing_a_reference(
    tmp_path: Path,
) -> None:
    with closing(GameIoRecordWriter.create(tmp_path / "records")) as writer:
        writer.record_input(
            OperationFinished(
                "operation-1", "completed", 2, phase="observe", reason="observation failed"
            )
        )
        assert json.loads((writer.path / "events.jsonl").read_bytes())["payload"] == {
            "operation_id": "operation-1",
            "result": "completed",
            "completed_steps": 2,
            "input_sent": "none",
            "neutral": "not_needed",
            "phase": "observe",
            "reason": "observation failed",
        }


def test_create_requires_a_new_destination_and_does_not_create_ancestors(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "records"
    with pytest.raises(RecordError):
        GameIoRecordWriter.create(target)
    assert not target.parent.exists()
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "notes.md"
    marker.write_bytes(b"user notes")
    with pytest.raises(RecordError):
        GameIoRecordWriter.create(existing)
    assert list(existing.iterdir()) == [marker]
    assert marker.read_bytes() == b"user notes"


def test_open_creates_missing_record_files_without_changing_existing_files(tmp_path: Path) -> None:
    target = tmp_path / "existing"
    target.mkdir()
    marker = target / "notes.md"
    marker.write_bytes(b"user notes")
    with closing(GameIoRecordWriter.open(target)) as writer:
        assert writer.path == target.resolve()
        assert (target / "events.jsonl").read_bytes() == b""
        assert (target / "observations").is_dir()
        writer.record_dialogue("dialogue.message", "new record")
    before = (target / "events.jsonl").read_bytes()
    with closing(GameIoRecordWriter.open(target)):
        assert (target / "events.jsonl").read_bytes() == before
    assert marker.read_bytes() == b"user notes"


def test_open_does_not_create_a_missing_destination(tmp_path: Path) -> None:
    target = tmp_path / "missing"
    with pytest.raises(RecordError):
        GameIoRecordWriter.open(target)
    assert not target.exists()


@pytest.mark.parametrize("prefix", [b"{", b"\xff\n", b"{}\n", b"\n", b'{"n":NaN}\n'])
def test_open_appends_after_any_old_bytes_without_inspection_or_repair(
    tmp_path: Path, prefix: bytes
) -> None:
    target = tmp_path / "records"
    target.mkdir()
    log = target / "events.jsonl"
    log.write_bytes(prefix)
    with closing(GameIoRecordWriter.open(target)) as writer:
        writer.record_dialogue("dialogue.message", "next")
    saved = log.read_bytes()
    assert saved.startswith(prefix)
    appended = saved[len(prefix) :]
    assert appended.startswith(b"{")  # No newline is inserted to repair the old tail.
    assert appended.endswith(b"\n")
    assert json.loads(appended)["payload"] == {"text": "next"}


@pytest.mark.parametrize("failure", ["open", "partial", "close"])
def test_log_failure_keeps_original_bytes_and_allows_the_next_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    with closing(GameIoRecordWriter.create(tmp_path / "records")) as writer:
        writer.record_dialogue("dialogue.submitted", "first")
        log = writer.path / "events.jsonl"
        before = log.read_bytes()
        open_file = Path.open
        with monkeypatch.context() as patch:

            @contextmanager
            def failing_open(path: Path, mode: str, *, buffering: int) -> "Generator[BinaryIO]":
                assert path == log
                assert mode == "ab"
                if failure == "open":
                    message = "injected log open failure"
                    raise OSError(message)
                with open_file(path, mode, buffering=buffering) as stream:
                    if failure == "partial":
                        write = stream.write
                        patch.setattr(stream, "write", lambda row: write(row[: len(row) // 2]))
                    yield stream
                if failure == "close":
                    message = "injected log close failure"
                    raise OSError(message)

            patch.setattr(Path, "open", failing_open)
            with pytest.raises(RecordError):
                writer.record_dialogue("dialogue.message", "failed")
        after = log.read_bytes()
        assert after.startswith(before)
        assert (after == before) == (failure == "open")
        writer.record_dialogue("dialogue.message", "next")
        assert log.read_bytes().startswith(after)
        assert json.loads(log.read_bytes()[len(after) :])["payload"] == {"text": "next"}


def test_encoding_failure_does_not_disable_the_writer(tmp_path: Path) -> None:
    with closing(GameIoRecordWriter.create(tmp_path / "records")) as writer:
        log = writer.path / "events.jsonl"
        with pytest.raises(RecordError):
            writer.record_dialogue("dialogue.message", "invalid-\ud800")
        assert log.read_bytes() == b""
        writer.record_dialogue("dialogue.message", "next")
        assert json.loads(log.read_bytes())["payload"] == {"text": "next"}


@pytest.mark.parametrize("failure", ["collision", "publish"])
def test_image_failure_preserves_previous_image_and_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    with closing(GameIoRecordWriter.create(tmp_path / "records")) as writer:
        image = observation()
        writer.record(image)
        log = writer.path / "events.jsonl"
        before = log.read_bytes()
        with monkeypatch.context() as patch:
            if failure == "publish":

                def fail_publish(_path: Path, _target: Path) -> Path:
                    message = "injected image publish failure"
                    raise OSError(message)

                patch.setattr(Path, "rename", fail_publish)
            with pytest.raises(RecordError):
                writer.record(image if failure == "collision" else observation())
        assert log.read_bytes() == before
        assert (writer.path / image.image_path).read_bytes() == image.png_bytes
        assert len(list((writer.path / "observations").iterdir())) == 1
        writer.record(observation())
        assert len(log.read_bytes().splitlines()) == 2


def test_jsonl_failure_after_image_publication_keeps_the_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with closing(GameIoRecordWriter.create(tmp_path / "records")) as writer:
        image = observation()

        def fail_append(_path: Path, _row: bytes) -> None:
            message = "injected log write failure"
            raise OSError(message)

        with monkeypatch.context() as patch:
            patch.setattr(records_module, "_append_row", fail_append)
            with pytest.raises(RecordError):
                writer.record(image)
        assert (writer.path / image.image_path).read_bytes() == image.png_bytes
        assert (writer.path / "events.jsonl").read_bytes() == b""
        writer.record_dialogue("dialogue.message", "next")


@pytest.mark.parametrize(
    "identifier",
    [
        "../escaped",
        "obs-../../escaped",
        "obs-019aa000-0000-7000-8000-000000000000",
        "019AA000-0000-7000-8000-000000000000",
        "019aa000-0000-4000-8000-000000000000",
    ],
)
def test_image_id_cannot_escape_the_record_destination(tmp_path: Path, identifier: str) -> None:
    with closing(GameIoRecordWriter.create(tmp_path / "records")) as writer:
        with pytest.raises(RecordError):
            writer.record(replace(observation(), observation_id=identifier))
        assert (writer.path / "events.jsonl").read_bytes() == b""
        assert list((writer.path / "observations").iterdir()) == []
        assert not (tmp_path / "escaped.png").exists()


LOCK_PROBE = """
import sys
from pathlib import Path
from gigas.storage.records import GameIoRecordWriter, RecordError
path = Path(sys.argv[1])
assert (path / "events.jsonl").read_bytes()
try:
    writer = GameIoRecordWriter.open(path)
except RecordError:
    sys.exit(2)
writer.close()
"""


def test_writer_excludes_other_processes_but_allows_readers_and_reopen(tmp_path: Path) -> None:
    with closing(GameIoRecordWriter.create(tmp_path / "records")) as writer:
        writer.record_dialogue("dialogue.message", "record")
        with pytest.raises(RecordError):
            GameIoRecordWriter.open(writer.path)
        locked = subprocess.run(  # noqa: S603 -- fixed local ownership probe.
            [sys.executable, "-c", LOCK_PROBE, str(writer.path)], timeout=10, check=False
        )
        assert locked.returncode == 2
    writer.close()
    abrupt_exit = "import os\n" + LOCK_PROBE.replace("writer.close()", "os._exit(0)")
    for code in (abrupt_exit, LOCK_PROBE):
        result = subprocess.run(  # noqa: S603 -- fixed local ownership probe.
            [sys.executable, "-c", code, str(writer.path)], timeout=10, check=False
        )
        assert result.returncode == 0
    with pytest.raises(RecordError):
        writer.record_dialogue("dialogue.message", "closed")


def test_failed_open_releases_ownership_and_preserves_partial_data(tmp_path: Path) -> None:
    target = tmp_path / "records"
    target.mkdir()
    (target / "observations").write_bytes(b"existing file")
    with pytest.raises(RecordError):
        GameIoRecordWriter.open(target)
    assert (target / "observations").read_bytes() == b"existing file"
    (target / "observations").unlink()
    with closing(GameIoRecordWriter.open(target)):
        assert (target / "observations").is_dir()


def test_synchronous_writer_rejects_concurrent_write_and_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    append = records_module._append_row

    def delayed_append(path: Path, row: bytes) -> None:
        entered.set()
        if not release.wait(10):
            message = "test writer release"
            raise TimeoutError(message)
        append(path, row)

    monkeypatch.setattr(records_module, "_append_row", delayed_append)
    with closing(GameIoRecordWriter.create(tmp_path / "records")) as writer:
        with ThreadPoolExecutor(max_workers=1) as executor:
            writing = executor.submit(writer.record_dialogue, "dialogue.message", "first")
            try:
                assert entered.wait(3)
                with pytest.raises(RecordBusyError):
                    writer.record_dialogue("dialogue.message", "concurrent")
                with pytest.raises(RecordBusyError):
                    writer.close()
            finally:
                release.set()
            writing.result(timeout=3)
        assert len((writer.path / "events.jsonl").read_bytes().splitlines()) == 1
