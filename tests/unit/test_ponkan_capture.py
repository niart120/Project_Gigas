import sys
from importlib import import_module
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import Mock

import numpy as np
import pytest

from gigas.game_io.capture.backend import acquire_frames
from gigas.game_io.capture.config import PONKAN_SOURCE, CaptureOptions
from gigas.game_io.capture.models import CaptureError

if TYPE_CHECKING:
    from gigas.game_io.capture.models import Frame

if sys.platform != "win32":
    pytest.skip("Windows capture source", allow_module_level=True)
ponkan = import_module("ponkan")


@pytest.mark.parametrize(
    ("status", "count", "reason"),
    [
        ("available", 0, "見つかりません"),
        ("available", 2, "複数"),
        ("unavailable", 0, "ドライバー"),
    ],
)
def test_reject_before_open(
    monkeypatch: pytest.MonkeyPatch, status: str, count: int, reason: str
) -> None:
    discovery = Mock(
        return_value=SimpleNamespace(backend_status=status, devices=[0] * count, reason=status)
    )
    opener = Mock()
    monkeypatch.setattr(ponkan, "list_capture_devices", discovery)
    monkeypatch.setattr(ponkan, "open_capture", opener)
    with pytest.raises(CaptureError, match=reason):
        acquire_frames(CaptureOptions(PONKAN_SOURCE), 1, Mock(), lambda: False, Mock())
    discovery.assert_called_once_with(profile="n3dsxl", backend="auto")
    opener.assert_not_called()


@pytest.mark.parametrize("cancel_at_open", [False, True])
def test_ponkan_frame_timeout_and_close(
    monkeypatch: pytest.MonkeyPatch, cancel_at_open: bool
) -> None:
    monkeypatch.setattr(
        ponkan,
        "list_capture_devices",
        Mock(return_value=SimpleNamespace(backend_status="available", devices=[object()])),
    )
    pixels = np.full((480, 400, 3), (17, 43, 95), dtype=np.uint8)
    reader = Mock()
    reader.read.side_effect = [None, pixels]
    opener = Mock(return_value=reader)
    monkeypatch.setattr(ponkan, "open_capture", opener)
    released = Mock()
    frames: list[Frame] = []
    camera = Mock()
    acquire_frames(
        CaptureOptions(PONKAN_SOURCE),
        7,
        camera,
        lambda: bool(frames) or (cancel_at_open and opener.called),
        frames.append,
        released,
    )
    camera.enumerate.assert_not_called()
    opener.assert_called_once_with(
        backend="auto",
        output=ponkan.CaptureOutput.BOTH_VERTICAL,
        colorspace="BGR",
        read_timeout=0.1,
    )
    released.assert_called_once()
    reader.close.assert_called_once()
    if cancel_at_open:
        reader.read.assert_not_called()
        assert not frames
    else:
        (frame,) = frames
        assert (frame.width, frame.height, frame.generation, frame.sequence) == (400, 480, 7, 1)
        assert frame.bgr == pixels.tobytes()


def test_reader_failure_preserves_diagnostic_and_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    from ponkan.errors import DeviceOpenError  # noqa: PLC0415 -- Windows dependency

    monkeypatch.setattr(
        ponkan,
        "list_capture_devices",
        Mock(return_value=SimpleNamespace(backend_status="available", devices=[object()])),
    )
    reader = Mock()
    reader.read.side_effect = DeviceOpenError("USB transfer failed")
    monkeypatch.setattr(ponkan, "open_capture", Mock(return_value=reader))
    released = Mock()
    with pytest.raises(CaptureError, match="USB transfer failed"):
        acquire_frames(CaptureOptions(PONKAN_SOURCE), 1, Mock(), lambda: False, Mock(), released)
    reader.close.assert_called_once()
    released.assert_called_once()


def test_dll_load_exit_is_reported_as_discovery_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ponkan, "list_capture_devices", Mock(side_effect=SystemExit))
    opener = Mock()
    monkeypatch.setattr(ponkan, "open_capture", opener)
    with pytest.raises(CaptureError, match="D3XX"):
        acquire_frames(CaptureOptions(PONKAN_SOURCE), 1, Mock(), lambda: False, Mock())
    opener.assert_not_called()
