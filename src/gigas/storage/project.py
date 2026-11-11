"""Single-owner project storage for recorded observations."""

import json
import os
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Self
from uuid import UUID, uuid4

if sys.platform == "win32":
    import msvcrt

if TYPE_CHECKING:
    from collections.abc import Generator
    from typing import BinaryIO


class ProjectError(ValueError):
    """A project could not be created, read, or written without losing data."""


class ProjectBusyError(ProjectError):
    """A recording operation still owns the project."""


class ProjectWriter:
    """An explicitly closed owner of one project's observation records.

    Construct through create() or open(), not by calling the initializer.
    """

    def __init__(self, path: Path, project_id: str, lock_file: "BinaryIO") -> None:
        """Initialize the owner after its project has been validated."""
        self.path = path
        self.project_id = project_id
        self._lock_file: BinaryIO | None = lock_file
        self._mutex = Lock()
        self._next_sequence: int | None = 1

    @classmethod
    def create(cls, path: Path) -> Self:
        """Create a project in a directory that does not yet exist.

        Args:
            path: Dedicated project directory; its parent must exist.

        Returns:
            An owner that must be explicitly closed after use.

        Raises:
            ProjectError: Creation failed. Existing and partial data are preserved.
        """
        path = path.resolve()
        project_id = f"project-{uuid4()}"
        lock_file = None
        try:
            path.mkdir()
            lock_file = _acquire_lock(path)
            (path / "observations").mkdir()
            with (path / "events.jsonl").open("xb"):
                pass
            metadata = {
                "schema_version": 1,
                "project_id": project_id,
                "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
            with (path / "project.json").open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(metadata, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, ProjectError) as error:
            if lock_file is not None:
                lock_file.close()
            message = f"{path}: プロジェクト作成失敗。作成途中の資材は保持します: {error}"
            raise ProjectError(message) from error
        return cls(path, project_id, lock_file)

    @classmethod
    def open(cls, path: Path) -> Self:
        """Open a supported project without rewriting its existing data.

        Args:
            path: Existing project directory.

        Returns:
            An owner ready to append after the last valid sequence.

        Raises:
            ProjectError: Metadata or a record is unreadable or invalid.
        """
        path = path.resolve()
        lock_file = _acquire_lock(path)
        try:
            project_id, next_sequence = _read_project(path)
        except ProjectError:
            lock_file.close()
            raise
        owner = cls(path, project_id, lock_file)
        owner._next_sequence = next_sequence
        return owner

    def reload(self) -> None:
        """Revalidate the held project before explicitly resuming writes.

        Raises:
            ProjectError: This owner is closed, identity changed, or data is invalid.
                Append remains disabled after a failed revalidation.
        """
        with self._operation():
            self._require_open()
            self._next_sequence = None
            project_id, next_sequence = _read_project(self.path)
            if project_id != self.project_id:
                message = f"{self.path}: プロジェクトIDが変更されています"
                raise ProjectError(message)
            self._next_sequence = next_sequence

    def close(self) -> None:
        """Release ownership; repeated close is harmless.

        Raises:
            ProjectBusyError: A synchronous recording or reload is still running.
        """
        with self._operation():
            if self._lock_file is not None:
                self._lock_file.close()
                self._lock_file = None

    @contextmanager
    def _operation(self) -> "Generator[None]":
        if not self._mutex.acquire(blocking=False):
            message = "プロジェクトの書込処理中です"
            raise ProjectBusyError(message)
        try:
            yield
        finally:
            self._mutex.release()

    def _require_open(self) -> None:
        if self._lock_file is None:
            message = "プロジェクトは終了済みです"
            raise ProjectError(message)


def _acquire_lock(path: Path) -> "BinaryIO":
    if sys.platform != "win32":
        message = "プロジェクト書込はWindowsだけに対応しています"
        raise ProjectError(message)
    try:
        stream = (path / ".gigas.lock").open("a+b")
        try:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            stream.close()
            raise
    except OSError as error:
        message = f"{path}: 書込所有権を取得できません: {error}"
        raise ProjectError(message) from error
    return stream


def _reject_constant(value: str) -> None:
    message = f"JSONに未対応の数値: {value}"
    raise ValueError(message)


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        message = "JSON objectが必要です"
        raise TypeError(message)
    return value


def _identifier(value: object, prefix: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        message = f"{prefix}で始まるIDが必要です"
        raise ValueError(message)
    identifier = UUID(value[len(prefix) :])
    if identifier.version != 4 or value != prefix + str(identifier):
        message = "IDは小文字・ハイフン付きUUID4が必要です"
        raise ValueError(message)
    return value


def _timestamp(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value.endswith("Z")
        or "T" not in value
        or datetime.fromisoformat(value).tzinfo != UTC
    ):
        message = "UTCのRFC 3339時刻が必要です"
        raise ValueError(message)


def _positive(value: object) -> int:
    if type(value) is not int or value <= 0:
        message = "正整数が必要です"
        raise ValueError(message)
    return value


def _version(value: object) -> None:
    if type(value) is not int or value != 1:
        message = "未対応のschema_versionです"
        raise ValueError(message)


def _read_metadata(path: Path) -> str:
    metadata = _object(
        json.loads(
            (path / "project.json").read_text(encoding="utf-8"), parse_constant=_reject_constant
        )
    )
    _version(metadata.get("schema_version"))
    project_id = _identifier(metadata.get("project_id"), "project-")
    _timestamp(metadata.get("created_at"))
    if not (path / "observations").is_dir():
        message = "observationsディレクトリがありません"
        raise ValueError(message)
    return project_id


def _read_project(path: Path) -> tuple[str, int]:
    try:
        project_id = _read_metadata(path)
    except (OSError, ValueError, TypeError) as error:
        message = f"{path}/project.json: 読込失敗: {error}"
        raise ProjectError(message) from error
    previous = 0
    try:
        with (path / "events.jsonl").open("rb") as stream:
            for line, raw in enumerate(stream, 1):
                try:
                    previous = _read_record(raw, project_id, previous)
                except (ValueError, TypeError) as error:
                    message = (
                        f"{path}/events.jsonl 行{line}: {error}。正常範囲: 先頭から{line - 1}行"
                    )
                    raise ProjectError(message) from error
    except OSError as error:
        message = f"{path}/events.jsonl: 読込失敗: {error}"
        raise ProjectError(message) from error
    return project_id, previous + 1


def _read_record(raw: bytes, project_id: str, previous: int) -> int:
    if not raw.endswith(b"\n"):
        message = "末尾の不完全行"
        raise ValueError(message)
    record = _object(json.loads(raw.decode("utf-8"), parse_constant=_reject_constant))
    _version(record.get("schema_version"))
    _identifier(record.get("event_id"), "evt-")
    _timestamp(record.get("occurred_at"))
    sequence = _positive(record.get("sequence"))
    kind = record.get("type")
    if (
        sequence <= previous
        or record.get("project_id") != project_id
        or not isinstance(kind, str)
        or not kind
        or "payload" not in record
    ):
        message = "イベント共通項目または追記順が不正です"
        raise ValueError(message)
    if kind == "observation.recorded":
        _validate_observation(record["payload"])
    return sequence


def _validate_observation(value: object) -> None:
    payload = _object(value)
    observation_id = _identifier(payload.get("observation_id"), "obs-")
    _timestamp(payload.get("captured_at"))
    if (
        payload.get("image_path") != f"observations/{observation_id}.png"
        or payload.get("analysis") != {}
    ):
        message = "観測画像パスまたはanalysisが不正です"
        raise ValueError(message)
    frame = _object(payload.get("source_frame"))
    _positive(frame.get("generation"))
    _positive(frame.get("sequence"))
    width, height = _positive(frame.get("width")), _positive(frame.get("height"))
    if not (16 <= width <= 3840 and 16 <= height <= 2160):
        message = "観測画像寸法が範囲外です"
        raise ValueError(message)
