"""Single-owner settings I/O on a Qt worker thread."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from gigas.storage.settings import Settings, SettingsError, load_settings, save_settings


@dataclass(frozen=True)
class SettingsIO:
    """Synchronous operations injected at the worker boundary.

    Attributes:
        load: Return settings for a path or raise an error.
        save: Persist a snapshot or raise an error.
    """

    load: Callable[[Path], Settings] = load_settings
    save: Callable[[Path, Settings], None] = save_settings


@dataclass(frozen=True)
class OperationResult:
    """Worker completion delivered to the GUI thread.

    Attributes:
        settings: Loaded or saved snapshot on success, otherwise None.
        error: Displayable diagnostic on failure, otherwise empty.
    """

    settings: Settings | None = None
    error: str = ""


class SettingsWorker(QObject):
    """Execute synchronous settings operations without accessing widgets.

    Args:
        path: File owned by this application instance.
        io: Settings operations; defaults use the M1 implementation.
    """

    completed = Signal(object)

    def __init__(self, path: Path, io: SettingsIO) -> None:
        """Store the settings I/O boundary."""
        super().__init__()
        self._path = path
        self._io = io
        self._owner_thread = self.thread()

    @Slot()
    def retire(self) -> None:
        """Return to the creating thread before deferred QObject destruction.

        Call from this worker's affinity thread after its final I/O operation.
        Keeping destruction on the GUI thread avoids concurrent Python QObject
        destruction and GUI signal registration competing for Qt locks and the GIL.
        """
        if self.thread() != self._owner_thread and not self.moveToThread(self._owner_thread):
            return
        self.deleteLater()

    @Slot(str, object)
    def perform(self, operation: str, snapshot: object) -> None:
        """Execute one request and emit exactly one success or failure result."""
        try:
            if operation == "load":
                values = self._io.load(self._path)
            elif operation == "save" and isinstance(snapshot, Settings):
                self._io.save(self._path, snapshot)
                values = snapshot
            else:
                self.completed.emit(OperationResult(error="Invalid settings request"))
                return
        except SettingsError as error:
            details = "\n".join((str(error), *getattr(error, "__notes__", ())))
            self.completed.emit(OperationResult(error=details))
        except Exception as error:  # noqa: BLE001 -- every worker failure must reach the GUI
            self.completed.emit(OperationResult(error=f"{type(error).__name__}: {self._path}"))
        else:
            self.completed.emit(OperationResult(settings=values))
