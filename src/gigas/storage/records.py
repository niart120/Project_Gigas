"""Single-owner game I/O records without project or conversation management."""

import json
import sys
from contextlib import ExitStack, contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import TYPE_CHECKING, Literal, Self
from uuid import UUID

from gigas.storage.input_events import input_record

if sys.platform == "win32":
    import msvcrt

if TYPE_CHECKING:
    from collections.abc import Generator
    from typing import BinaryIO

    from gigas.game_io.observation import Observation
    from gigas.storage.input_events import InputEvent

type DialogueRecordType = Literal["dialogue.submitted", "dialogue.message"]


class RecordError(ValueError):
    """A record destination could not be acquired or written."""


class RecordBusyError(RecordError):
    """A synchronous write still owns the record destination."""


class GameIoRecordWriter:
    """An explicitly closed Windows owner of one game I/O record destination.

    Construct through create() or open(). The caller supplies the destination and
    owns its lifetime; no Project, conversation, or model connection is required.
    The OS lock on .gigas.lock excludes cooperating writers until close or process
    exit without blocking reads. Run synchronous operations outside the GUI/event
    loop. Records and images are not repaired or retried after a failure.

    Attributes:
        path: Absolute record directory, fixed for this owner's lifetime.
    """

    def __init__(self, path: Path, lock_file: "BinaryIO") -> None:
        """Retain an acquired destination; use create() or open() to acquire one."""
        self.path = path
        self._lock_file: BinaryIO | None = lock_file
        self._mutex = Lock()

    @classmethod
    def create(cls, path: Path) -> Self:
        """Create and acquire a new record destination without creating ancestors.

        Args:
            path: New directory whose parent already exists.

        Returns:
            A writer that the caller must close after use.

        Raises:
            RecordError: The destination exists, creation or locking fails, or the
                OS is unsupported. Partial data remains at the requested path.
        """
        path = path.resolve()
        try:
            path.mkdir()
            return cls.open(path)
        except (OSError, RecordError) as error:
            message = f"{path}: 記録先の作成に失敗しました。作成途中の資材は保持します: {error}"
            raise RecordError(message) from error

    @classmethod
    def open(cls, path: Path) -> Self:
        """Acquire an existing directory and prepare only missing record files.

        Existing JSONL bytes and images are not read, validated, repaired, or
        replaced. A missing observations directory or events.jsonl is created.

        Args:
            path: Existing record destination; missing ancestors are not created.

        Returns:
            A writer ready to append after the existing bytes.

        Raises:
            RecordError: The directory is missing, preparation or locking fails,
                another writer owns it, or the OS is unsupported.
        """
        path = path.resolve()
        try:
            with ExitStack() as resources:
                lock_file = resources.enter_context(_acquire_lock(path))
                (path / "observations").mkdir(exist_ok=True)
                with (path / "events.jsonl").open("ab"):
                    pass
                owner = cls(path, lock_file)
                resources.pop_all()
                return owner
        except (OSError, RecordError) as error:
            message = f"{path}: 記録先を開けません: {error}"
            raise RecordError(message) from error

    def close(self) -> None:
        """Release ownership; repeated close is harmless.

        Raises:
            RecordBusyError: A synchronous write is still running.
            RecordError: The OS cannot close the ownership file.
        """
        with self._operation():
            if self._lock_file is not None:
                try:
                    self._lock_file.close()
                except OSError as error:
                    message = f"{self.path}: 記録先の書込所有権を解放できません: {error}"
                    raise RecordError(message) from error
                self._lock_file = None

    def record_dialogue(self, kind: DialogueRecordType, text: str) -> None:
        """Append a submitted user body or a confirmed Codex body.

        Args:
            kind: dialogue.submitted or dialogue.message, selected by the caller.
            text: Complete body without request, role, or conversation metadata.
                The caller owns selection; acceptance and finality are not inferred.

        Raises:
            RecordBusyError: Another synchronous operation owns the writer.
            RecordError: The writer is closed, encoding fails, or appending fails.
                A failure does not disable subsequent records.
        """
        with self._operation():
            self._require_open()
            self._append_event(kind, {"text": text})

    def record_input(self, event: "InputEvent") -> None:
        """Append one operation start or final outcome.

        Args:
            event: Immutable input facts, including an observation ID when available.
                The caller owns semantic validity and physical-delivery interpretation.

        Raises:
            RecordBusyError: Another synchronous operation owns the writer.
            RecordError: The writer is closed, encoding fails, or appending fails.
                A failure does not disable subsequent records.
        """
        with self._operation():
            self._require_open()
            kind, payload = input_record(event)
            self._append_event(kind, payload)

    def record_token_usage(self, turn_id: str, total: dict[str, int]) -> None:
        """Append cumulative token counts for one received notification.

        Args:
            turn_id: Turn associated with the usage update.
            total: Cumulative token counts reported for the thread.

        Raises:
            RecordBusyError: Another synchronous operation owns the writer.
            RecordError: The writer is closed or the append fails.
        """
        with self._operation():
            self._require_open()
            self._append_event("codex.token_usage_updated", {"turn_id": turn_id, "total": total})

    def record(self, observation: "Observation") -> None:
        """Publish the original PNG bytes and append their observation metadata.

        Args:
            observation: Final image and source metadata from Observation.from_frame.
                Only its canonical UUID7 identifier is checked to fix the destination.
                The image is not encoded or decoded again.

        Raises:
            RecordBusyError: Another synchronous operation owns the writer.
            RecordError: The writer is closed, the image ID is invalid, the image
                already exists, or saving fails. An already published image remains
                when JSONL saving fails; subsequent new records are still allowed.
        """
        with self._operation():
            self._require_open()
            observation_id = _observation_id(observation.observation_id)
            image_path = f"observations/{observation_id}.png"
            frame = observation.source_frame
            payload: dict[str, object] = {
                "observation_id": observation_id,
                "captured_at": observation.captured_at.isoformat().replace("+00:00", "Z"),
                "image_path": image_path,
                "image": {"width": observation.width, "height": observation.height},
                "source_frame": {"width": frame.width, "height": frame.height},
            }
            try:
                _publish_image(self.path / image_path, observation.png_bytes)
            except OSError as error:
                message = f"{self.path}: 観測画像の保存に失敗しました: {error}"
                raise RecordError(message) from error
            self._append_event("observation.recorded", payload)

    def _append_event(self, kind: str, payload: dict[str, object]) -> None:
        event = {
            "schema_version": 1,
            "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "type": kind,
            "payload": payload,
        }
        try:
            row = (json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        except (TypeError, ValueError) as error:
            message = f"{self.path}: 記録をUTF-8のJSON行へ変換できません: {error}"
            raise RecordError(message) from error
        try:
            _append_row(self.path / "events.jsonl", row)
        except OSError as error:
            message = f"{self.path}: JSONL追記に失敗しました。原本は保持します: {error}"
            raise RecordError(message) from error

    @contextmanager
    def _operation(self) -> "Generator[None]":
        if not self._mutex.acquire(blocking=False):
            raise RecordBusyError("記録先の書込処理中です")
        try:
            yield
        finally:
            self._mutex.release()

    def _require_open(self) -> None:
        if self._lock_file is None:
            raise RecordError("記録先は終了済みです")


def _acquire_lock(path: Path) -> "BinaryIO":
    if sys.platform != "win32":
        raise RecordError("記録の書込はWindowsだけに対応しています")
    stream = (path / ".gigas.lock").open("a+b")
    try:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        stream.close()
        raise
    return stream


def _publish_image(path: Path, data: bytes) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        temporary.rename(path)  # Windows rename refuses an existing destination.
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def _append_row(path: Path, row: bytes) -> None:
    with path.open("ab", buffering=0) as stream:
        if stream.write(row) != len(row):
            raise OSError("JSONLへの書込が途中で終了しました")


def _observation_id(value: str) -> str:
    try:
        identifier = UUID(value)
    except ValueError as error:
        message = f"観測画像の保存先IDが不正です: {error}"
        raise RecordError(message) from error
    if identifier.version != 7 or value != str(identifier):
        raise RecordError("観測画像の保存先IDは小文字・ハイフン付きUUID7が必要です")
    return value
