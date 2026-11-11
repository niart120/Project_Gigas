"""Controllable capture boundary without native device access."""

from dataclasses import replace
from datetime import UTC, datetime

from gigas.game_io.capture import CaptureReader, CaptureSnapshot
from gigas.game_io.capture.models import CaptureOptions, DeviceInfo, Frame


class ControlledCapture:
    """Expose controllable service outcomes without opening a capture device."""

    def __init__(self, *, auto_enumerate: bool = False) -> None:
        """Prepare request history and an idle state."""
        self.auto_enumerate = auto_enumerate
        self.state = CaptureSnapshot()
        self.alive = False
        self.connections: list[CaptureOptions] = []
        self.refreshes = 0
        self.disconnects = 0
        self.shutdowns = 0
        self.finish_shutdown = True
        self.finish_disconnect = True
        self.accept_connection = True
        self.reader_requests = 0

    def start(self) -> None:
        """Start the simulated service without discovering devices."""
        self.alive = True

    def refresh_devices(self) -> bool:
        """Accept only an idle device enumeration request."""
        if self.state.phase not in {"idle", "error"} or self.state.cleanup == "unconfirmed":
            return False
        self.refreshes += 1
        self.state = replace(self.state, phase="enumerating")
        if self.auto_enumerate:
            self.complete_enumeration()
        return True

    def complete_enumeration(
        self, devices: tuple[DeviceInfo, ...] = (), *, error: str = ""
    ) -> None:
        """Publish an explicit list result, preserving prior candidates on failure."""
        self.state = replace(
            self.state,
            phase="error" if error else "idle",
            devices=self.state.devices if error else devices,
            devices_revision=self.state.devices_revision + (not error),
            error=error,
            cleanup="graceful",
        )

    def connect(self, options: CaptureOptions) -> bool:
        """Record an immutable connection request."""
        if (
            not self.accept_connection
            or self.state.phase not in {"idle", "error"}
            or self.state.cleanup == "unconfirmed"
        ):
            return False
        self.connections.append(options)
        self.state = replace(
            self.state,
            phase="connecting",
            generation=len(self.connections),
            error="",
            cleanup="none",
        )
        return True

    def disconnect(self) -> None:
        """Optionally hold cleanup so reconnect ordering can be observed."""
        self.disconnects += 1
        self.state = replace(self.state, phase="stopping", frame=None)
        if self.finish_disconnect:
            self.complete_disconnect()

    def complete_disconnect(self) -> None:
        """Confirm release of the old worker before another connection is accepted."""
        self.state = replace(self.state, phase="idle", frame=None, cleanup="graceful", error="")

    def shutdown(self) -> None:
        """Optionally keep shutdown pending for lifetime tests."""
        self.shutdowns += 1
        self.state = replace(self.state, phase="stopping", frame=None)
        if self.finish_shutdown:
            self.state = replace(self.state, phase="closed", cleanup="graceful")
            self.alive = False

    def is_alive(self) -> bool:
        """Return the controlled thread lifetime."""
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        """Reject any GUI attempt to wait for a running thread."""
        assert timeout == 0

    def snapshot(self) -> CaptureSnapshot:
        """Return the immutable current state."""
        return self.state

    def reader(self) -> CaptureReader:
        """Return a separate read-only port for the application preview."""
        self.reader_requests += 1
        return CaptureReader(self.snapshot)

    def stream(
        self,
        sequence: int = 1,
        bgr: bytes = bytes([0, 0, 255]),
        *,
        actual_size: tuple[int, int] | None = None,
    ) -> None:
        """Publish actual dimensions, defaulting to the request or 16x16 for auto."""
        options = self.connections[-1]
        width, height = actual_size or (options.width or 16, options.height or 16)
        frame = Frame(
            bgr * (width * height),
            width,
            height,
            len(self.connections),
            sequence,
            datetime.now(UTC),
            sequence,
        )
        self.state = replace(self.state, phase="streaming", frame=frame)
