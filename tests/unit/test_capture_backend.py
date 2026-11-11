from collections.abc import Sequence
from types import SimpleNamespace
from typing import TYPE_CHECKING

import cv2
import numpy as np
import pytest

from gigas.game_io.capture import backend as capture_backend
from gigas.game_io.capture.backend import IndexedDevice, OpenCVBackend, acquire_frames
from gigas.game_io.capture.models import CaptureError, CaptureOptions, DeviceInfo

if TYPE_CHECKING:
    from gigas.game_io.capture.models import Frame


class Camera:
    """Deterministic acquisition boundary without a physical camera."""

    def __init__(self, outcomes: Sequence[object], *, opened: bool = True) -> None:
        """Prepare fixed read results."""
        self.outcomes = list(outcomes)
        self.opened = opened
        self.released = False
        self.properties: dict[int, float] = {}

    def isOpened(self) -> bool:  # noqa: N802 -- OpenCV interface
        """Return the selected open result."""
        return self.opened

    def set(self, property_id: int, value: float) -> bool:
        """Record requested values."""
        self.properties[property_id] = value
        return True

    def get(self, _property_id: int) -> float:
        """Report a fractional nominal rate."""
        return 59.94

    def read(self) -> tuple[bool, object]:
        """Return or raise the next fixed outcome."""
        value = self.outcomes.pop(0)
        if isinstance(value, Exception):
            raise value
        return value is not None, value

    def release(self) -> None:
        """Record resource release."""
        self.released = True


class Backend:
    """Record selection while providing one test camera."""

    def __init__(self, camera: Camera) -> None:
        """Own the test's camera."""
        self.camera = camera
        self.selected: int | None = None
        self.enumerations = 0

    def enumerate(self) -> tuple[IndexedDevice, ...]:
        """Return a selectable device and an interface with no usable path."""
        self.enumerations += 1
        return (
            IndexedDevice(DeviceInfo("Capture", "path-a"), 9),
            IndexedDevice(DeviceInfo("Pathless", ""), 12),
        )

    def open(self, index: int) -> Camera:
        """Record the index and return the camera."""
        self.selected = index
        return self.camera


def test_requests_mode_and_publishes_original_pixels_with_acquisition_metadata() -> None:
    pixels = np.arange(16 * 16 * 3, dtype=np.uint8).reshape((16, 16, 3))
    camera = Camera([pixels])
    backend = Backend(camera)
    frames: list[Frame] = []
    rates: list[float | None] = []
    listed: list[tuple[DeviceInfo, ...]] = []
    acquire_frames(
        CaptureOptions("path-a", 16, 16, 60),
        7,
        backend,
        lambda: bool(frames),
        frames.append,
        rates.append,
        enumerated=listed.append,
    )
    assert listed == [(DeviceInfo("Capture", "path-a"),)]
    assert backend.enumerations == 1
    assert backend.selected == 9
    assert camera.properties == {
        cv2.CAP_PROP_FRAME_WIDTH: 16,
        cv2.CAP_PROP_FRAME_HEIGHT: 16,
        cv2.CAP_PROP_FPS: 60,
    }
    assert frames[0].bgr == pixels.tobytes()
    pixels[:] = 0
    assert any(frames[0].bgr)
    assert (frames[0].generation, frames[0].sequence) == (7, 1)
    assert frames[0].monotonic_ns > 0
    assert rates == [59.94]
    assert camera.released


@pytest.mark.parametrize(
    "outcome",
    [
        None,
        np.zeros((8, 16, 3), dtype=np.uint8),
        np.zeros((16, 16, 3), dtype=np.float32),
        b"not-an-array",
    ],
)
def test_bad_frames_fail_without_publishing_and_release_the_camera(outcome: object) -> None:
    camera = Camera([outcome])
    frames: list[Frame] = []
    with pytest.raises(CaptureError):
        acquire_frames(
            CaptureOptions("path-a", 16, 16),
            1,
            Backend(camera),
            lambda: False,
            frames.append,
            lambda _rate: None,
        )
    assert frames == []
    assert camera.released


@pytest.mark.parametrize("opened", [False, True])
def test_open_failure_or_read_exception_still_releases_the_camera(opened: bool) -> None:
    camera = Camera([RuntimeError("driver failure")], opened=opened)
    with pytest.raises(CaptureError if not opened else RuntimeError):
        acquire_frames(
            CaptureOptions("path-a", 16, 16),
            1,
            Backend(camera),
            lambda: False,
            lambda _frame: None,
            lambda _rate: None,
        )
    assert camera.released


def test_stop_during_read_does_not_publish_the_late_result() -> None:
    stopped = False

    class StopsDuringRead(Camera):
        def read(self) -> tuple[bool, object]:
            """Cancel the connection while the native read is in progress."""
            nonlocal stopped
            stopped = True
            return super().read()

    camera = StopsDuringRead([np.zeros((16, 16, 3), dtype=np.uint8)])
    frames: list[Frame] = []
    acquire_frames(
        CaptureOptions("path-a", 16, 16),
        1,
        Backend(camera),
        lambda: stopped,
        frames.append,
        lambda _rate: None,
    )
    assert frames == []
    assert camera.released


def test_opencv_adapter_uses_directshow_and_excludes_pathless_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, ...]] = []
    camera = Camera([])

    def enumerate_devices(api: int) -> list[SimpleNamespace]:
        calls.append((api,))
        return [
            SimpleNamespace(name="A", path="path-a", index=3),
            SimpleNamespace(name="Virtual", path="", index=4),
        ]

    def open_device(index: int, api: int) -> Camera:
        calls.append((index, api))
        return camera

    monkeypatch.setattr(capture_backend, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(capture_backend, "enumerate_cameras", enumerate_devices)
    monkeypatch.setattr(capture_backend.cv2, "VideoCapture", open_device)
    backend = OpenCVBackend()
    assert backend.enumerate() == (IndexedDevice(DeviceInfo("A", "path-a"), 3),)
    assert backend.open(3) is camera
    assert calls == [(cv2.CAP_DSHOW,), (3, cv2.CAP_DSHOW)]
