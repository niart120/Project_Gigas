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

    def open(self, device: IndexedDevice) -> Camera:
        """Record the index and return the camera."""
        self.selected = device.index
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


@pytest.mark.parametrize("automatic", [True, False])
def test_resolution_request_preserves_actual_dimensions_and_unmodified_pixels(
    automatic: bool,
) -> None:
    # Non-contiguous input also has to retain the row-major values, without resizing.
    pixels = np.arange(24 * 64 * 3, dtype=np.uint8).reshape((24, 64, 3))[:, ::2]
    camera = Camera([pixels, pixels])
    frames: list[Frame] = []
    options = CaptureOptions("path-a", None, None, 30) if automatic else CaptureOptions("path-a")
    acquire_frames(
        options, 9, Backend(camera), lambda: len(frames) == 2, frames.append, lambda _: None
    )

    assert [(frame.width, frame.height) for frame in frames] == [(32, 24), (32, 24)]
    assert all(frame.bgr == pixels.tobytes() for frame in frames)
    assert [(frame.generation, frame.sequence) for frame in frames] == [(9, 1), (9, 2)]
    assert frames[1].monotonic_ns >= frames[0].monotonic_ns > 0
    assert camera.properties[cv2.CAP_PROP_FPS] == options.fps
    if automatic:
        assert cv2.CAP_PROP_FRAME_WIDTH not in camera.properties
        assert cv2.CAP_PROP_FRAME_HEIGHT not in camera.properties
    else:
        assert camera.properties[cv2.CAP_PROP_FRAME_WIDTH] == 1920
        assert camera.properties[cv2.CAP_PROP_FRAME_HEIGHT] == 1080
    assert camera.released


def test_maximum_actual_resolution_is_accepted_without_requiring_the_requested_size() -> None:
    pixels = np.broadcast_to(np.array([2, 4, 8], dtype=np.uint8), (2160, 3840, 3))
    camera = Camera([pixels])
    frames: list[Frame] = []
    acquire_frames(
        CaptureOptions("path-a"),
        1,
        Backend(camera),
        lambda: bool(frames),
        frames.append,
        lambda _: None,
    )
    assert (frames[0].width, frames[0].height) == (3840, 2160)
    assert frames[0].bgr == bytes([2, 4, 8]) * (3840 * 2160)
    assert camera.released


def test_resolution_change_after_first_frame_fails_without_publishing_new_dimensions() -> None:
    original = np.full((24, 32, 3), 7, dtype=np.uint8)
    camera = Camera([original, np.zeros((32, 32, 3), dtype=np.uint8)])
    frames: list[Frame] = []
    with pytest.raises(CaptureError):
        acquire_frames(
            CaptureOptions("path-a"),
            1,
            Backend(camera),
            lambda: False,
            frames.append,
            lambda _: None,
        )
    assert len(frames) == 1
    assert (frames[0].width, frames[0].height, frames[0].bgr) == (32, 24, original.tobytes())
    assert camera.released


@pytest.mark.parametrize(
    "outcome",
    [
        None,
        np.zeros((8, 16, 3), dtype=np.uint8),
        np.zeros((16, 16, 3), dtype=np.float32),
        np.zeros((16, 16), dtype=np.uint8),
        np.zeros((16, 16, 4), dtype=np.uint8),
        np.zeros((16, 16, 3, 1), dtype=np.uint8),
        np.broadcast_to(np.uint8(0), (16, 3841, 3)),
        np.broadcast_to(np.uint8(0), (2161, 16, 3)),
        np.broadcast_to(np.uint8(0), (1080, 7680, 3)),
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
    monkeypatch.setattr(
        capture_backend.cv2.videoio_registry, "getCameraBackends", lambda: (cv2.CAP_DSHOW,)
    )
    monkeypatch.setattr(capture_backend.cv2.videoio_registry, "hasBackend", lambda _api: True)
    backend = OpenCVBackend()
    device = IndexedDevice(DeviceInfo("A", "path-a"), 3, cv2.CAP_DSHOW)
    assert backend.enumerate() == (device,)
    assert backend.open(device) is camera
    assert calls == [(cv2.CAP_DSHOW,), (3, cv2.CAP_DSHOW)]
