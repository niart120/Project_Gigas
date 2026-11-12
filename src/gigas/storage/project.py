"""Single-owner project storage for recorded observations."""

import json
import os
import re
import sys
from contextlib import ExitStack, contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import TYPE_CHECKING, Self
from uuid import UUID, uuid4

if sys.platform == "win32":
    import msvcrt

if TYPE_CHECKING:
    from collections.abc import Generator
    from typing import BinaryIO

    from gigas.game_io.observation import Observation


class ProjectError(ValueError):
    """A project could not be created, read, or written without losing data."""


class ProjectBusyError(ProjectError):
    """A recording operation still owns the project."""


class ProjectWriter:
    """An explicitly closed Windows owner of one project's observation records.

    Construct through create() or open(), not by calling the initializer.
    The application owns one writer; observation tools borrow it. An OS lock on
    .gigas.lock excludes cooperating writers until close or process exit, without
    blocking archive reads or edits to memory/. Only this owner writes project.json,
    events.jsonl and observations/. Run synchronous operations outside the GUI/event
    loop, and do not change this owner's path or project_id attributes.

    Attributes:
        path: Absolute project directory, fixed for this owner's lifetime.
        project_id: Validated project identity, fixed for this owner's lifetime.
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
            ProjectError: Creation or OS locking failed, or the OS is unsupported.
                Existing and partial data are preserved.
        """
        path = path.resolve()
        project_id = f"project-{uuid4()}"
        try:
            with ExitStack() as resources:
                path.mkdir()
                lock_file = resources.enter_context(_acquire_lock(path))
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
                owner = cls(path, project_id, lock_file)
                resources.pop_all()
                return owner
        except (OSError, ProjectError) as error:
            message = f"{path}: プロジェクト作成失敗。作成途中の資材は保持します: {error}"
            raise ProjectError(message) from error

    @classmethod
    def open(cls, path: Path) -> Self:
        """Open a supported project without rewriting its existing data.

        Args:
            path: Existing project directory.

        Returns:
            An owner ready to append after the last valid sequence.

        Raises:
            ProjectError: Metadata or a record is unreadable or invalid, another
                writer owns the project, or the OS is unsupported.
        """
        path = path.resolve()
        with ExitStack() as resources:
            lock_file = resources.enter_context(_acquire_lock(path))
            project_id, next_sequence = _read_project(path)
            owner = cls(path, project_id, lock_file)
            owner._next_sequence = next_sequence
            resources.pop_all()
            return owner

    def reload(self) -> None:
        """Revalidate the held project before explicitly resuming writes.

        Raises:
            ProjectBusyError: A recording or reload is already running.
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

    def record_thread(self, thread_id: str) -> None:
        """Record a real Codex thread before its first turn is submitted.

        Args:
            thread_id: Opaque runtime ID, not empty or whitespace-only.

        Raises:
            ProjectBusyError: Another synchronous operation owns the writer.
            ProjectError: The ID is invalid, the writer is unavailable, or appending
                fails. Uncertain appends require an explicit successful reload.
        """
        with self._operation():
            sequence = self._require_writable()
            try:
                _thread_id(thread_id)
            except (ValueError, TypeError) as error:
                message = f"会話IDが不正です。記録していません: {error}"
                raise ProjectError(message) from error
            self._append_event(
                "thread.started", {"runtime": "codex", "thread_id": thread_id}, sequence
            )

    def record(self, observation: "Observation", *, thread_id: str | None = None) -> None:
        """Persist the final observation image and its corresponding metadata.

        Args:
            observation: Immutable PNG and source metadata from Observation.from_frame.
                The bytes are saved unchanged; this method does not encode an image.
            thread_id: Real runtime conversation associated with this observation.
                None means a standalone observation without a chat context.

        Raises:
            ProjectBusyError: A recording or reload is already running.
            ProjectError: The owner is closed, needs reload, metadata is invalid,
                or persistence failed. A JSONL failure disables further append
                until a successful reload.
        """
        with self._operation():
            sequence = self._require_writable()
            frame = observation.source_frame
            payload: dict[str, object] = {
                "observation_id": observation.observation_id,
                "captured_at": observation.captured_at.isoformat().replace("+00:00", "Z"),
                "image_path": observation.image_path,
                "image": {
                    "mime_type": observation.mime_type,
                    "width": observation.width,
                    "height": observation.height,
                },
                "source_frame": {
                    "generation": frame.generation,
                    "sequence": frame.sequence,
                    "width": frame.width,
                    "height": frame.height,
                },
            }
            if thread_id is not None:
                payload["thread_id"] = thread_id
            try:
                _validate_observation(payload)
            except (ValueError, TypeError) as error:
                message = f"観測項目が不正です。記録していません: {error}"
                raise ProjectError(message) from error
            try:
                _publish_image(self.path / observation.image_path, observation.png_bytes)
            except OSError as error:
                message = f"{self.path}: 観測画像の保存失敗: {error}"
                raise ProjectError(message) from error
            self._append_event("observation.recorded", payload, sequence)

    def _require_writable(self) -> int:
        self._require_open()
        if self._next_sequence is None:
            message = "追記結果が未確定です。プロジェクトを明示的に再読込してください"
            raise ProjectError(message)
        return self._next_sequence

    def _append_event(self, kind: str, payload: dict[str, object], sequence: int) -> None:
        # Caller owns _operation and has validated this record before any writes.
        event = {
            "schema_version": 1,
            "event_id": f"evt-{uuid4()}",
            "sequence": sequence,
            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "type": kind,
            "project_id": self.project_id,
            "payload": payload,
        }
        row = (json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        try:
            _append_row(self.path / "events.jsonl", row)
        except OSError as error:
            self._next_sequence = None
            message = f"{self.path}: JSONL追記結果が未確定です。原本を保持します: {error}"
            raise ProjectError(message) from error
        self._next_sequence = sequence + 1

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


def _publish_image(path: Path, data: bytes) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.rename(path)  # Windows rename refuses an existing destination.
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def _append_row(path: Path, row: bytes) -> None:
    with path.open("ab", buffering=0) as stream:
        if stream.write(row) != len(row):
            message = "JSONLへの書込が途中で終了しました"
            raise OSError(message)
        stream.flush()
        os.fsync(stream.fileno())


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
        or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z", value)
        is None
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
    except (OSError, ValueError, TypeError, RecursionError) as error:
        message = f"{path}/project.json: 読込失敗: {error}"
        raise ProjectError(message) from error
    previous = 0
    try:
        with (path / "events.jsonl").open("rb") as stream:
            for line, raw in enumerate(stream, 1):
                try:
                    previous = _read_record(raw, project_id, previous)
                except (ValueError, TypeError, RecursionError) as error:
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
    elif kind == "thread.started":
        payload = _object(record["payload"])
        _thread_id(payload.get("thread_id"))
        if payload.get("runtime") != "codex":
            message = "会話記録のruntimeが不正です"
            raise ValueError(message)
    return sequence


def _validate_observation(value: object) -> None:
    payload = _object(value)
    if "thread_id" in payload:
        _thread_id(payload["thread_id"])
    observation_id = _identifier(payload.get("observation_id"), "obs-")
    _timestamp(payload.get("captured_at"))
    if payload.get("image_path") != f"observations/{observation_id}.png":
        message = "観測画像パスが不正です"
        raise ValueError(message)
    frame = _object(payload.get("source_frame"))
    _positive(frame.get("generation"))
    _positive(frame.get("sequence"))
    width, height = _positive(frame.get("width")), _positive(frame.get("height"))
    if not (16 <= width <= 3840 and 16 <= height <= 2160):
        message = "観測画像寸法が範囲外です"
        raise ValueError(message)
    image = _object(payload.get("image"))
    image_width = _positive(image.get("width"))
    image_height = _positive(image.get("height"))
    if image.get("mime_type") != "image/png" or image_width > width or image_height > height:
        message = "観測出力画像の形式または寸法が不正です"
        raise ValueError(message)


def _thread_id(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        message = "空でないthread_idが必要です"
        raise ValueError(message)
