from pathlib import Path
from types import SimpleNamespace

import cv2
import pytest
from tests.unit.test_capture_backend import Camera

from gigas.game_io.capture import backend as module
from gigas.game_io.capture.backend import IndexedDevice, OpenCVBackend
from gigas.game_io.capture.models import CaptureError, CaptureOptions, DeviceInfo


@pytest.mark.parametrize(
    ("platform", "apis"),
    [
        ("win32", (cv2.CAP_DSHOW, cv2.CAP_MSMF)),
        ("linux", (cv2.CAP_V4L2, cv2.CAP_GSTREAMER)),
        ("darwin", (cv2.CAP_AVFOUNDATION,)),
    ],
)
def test_platform_candidates_pair_each_current_index_with_its_api(
    monkeypatch: pytest.MonkeyPatch, platform: str, apis: tuple[int, ...]
) -> None:
    calls: list[tuple[int, ...]] = []
    camera = Camera([])

    def enumerate_devices(api: int) -> list[SimpleNamespace]:
        calls.append((api,))
        return [
            SimpleNamespace(name="Same model", path="identity", index=apis.index(api) + 3),
            SimpleNamespace(name="Same model", path="other", index=8),
            SimpleNamespace(name="Virtual", path="", index=9),
        ]

    def open_device(index: int, api: int) -> Camera:
        calls.append((index, api))
        return camera

    monkeypatch.setattr(module, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(module, "enumerate_cameras", enumerate_devices)
    monkeypatch.setattr(module.cv2.videoio_registry, "getCameraBackends", lambda: apis)
    monkeypatch.setattr(module.cv2.videoio_registry, "hasBackend", lambda _api: True)
    monkeypatch.setattr(module.cv2, "VideoCapture", open_device)
    if platform == "linux":
        monkeypatch.setattr(module, "_linux_identity", lambda path: "stable:" + path, raising=False)
    backend = OpenCVBackend()
    candidates = backend.enumerate()
    path = "stable:identity" if platform == "linux" else "identity"
    selected = module.select_candidates(candidates, path)
    assert [(item.backend, item.index) for item in selected] == [
        (api, position + 3) for position, api in enumerate(apis)
    ]
    assert len(module.device_catalog(candidates)) == 2
    assert backend.open(selected[-1]) is camera
    assert calls == [(api,) for api in apis] + [(len(apis) + 2, apis[-1])]


def test_unavailable_api_is_neither_enumerated_nor_used(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def enumerate_devices(api: int) -> list[SimpleNamespace]:
        calls.append(api)
        return []

    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(module, "enumerate_cameras", enumerate_devices)
    monkeypatch.setattr(
        module.cv2.videoio_registry, "getCameraBackends", lambda: (cv2.CAP_DSHOW, cv2.CAP_MSMF)
    )
    monkeypatch.setattr(module.cv2.videoio_registry, "hasBackend", lambda api: api == cv2.CAP_MSMF)
    assert OpenCVBackend().enumerate() == ()
    assert calls == [cv2.CAP_MSMF]


@pytest.mark.parametrize("platform", ["freebsd", "win32"])
def test_unsupported_platform_or_build_never_probes_numeric_devices(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(module.cv2.videoio_registry, "getCameraBackends", tuple)
    monkeypatch.setattr(module, "enumerate_cameras", calls.append)
    with pytest.raises(CaptureError):
        OpenCVBackend().enumerate()
    assert calls == []


def test_linux_identity_prefers_stable_alias_and_never_saves_video_number(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    by_id, by_path = tmp_path / "by-id", tmp_path / "by-path"
    by_id.mkdir()
    by_path.mkdir()
    node, other = tmp_path / "video3", tmp_path / "video4"
    node.touch()
    other.touch()
    serial, port = by_id / "camera-serial", by_path / "usb-port"
    serial.touch()
    port.touch()
    resolve = Path.resolve

    def resolved(path: Path, *, strict: bool = False) -> Path:
        return node if path in (serial, port) else resolve(path, strict=strict)

    monkeypatch.setattr(module, "_LINUX_ID_ROOTS", (by_id, by_path), raising=False)
    monkeypatch.setattr(Path, "resolve", resolved)
    assert module._linux_identity(str(node)) == str(serial)
    serial.unlink()
    assert module._linux_identity(str(node)) == str(port)
    assert module._linux_identity(str(other)) is None


@pytest.mark.parametrize("path", ["", "missing", "Same model", "0"])
def test_candidate_selection_does_not_fall_back_to_names_or_indices(path: str) -> None:
    with pytest.raises(CaptureError):
        module.select_candidates((IndexedDevice(DeviceInfo("Same model", "identity"), 0),), path)


def test_duplicate_identity_within_one_api_is_rejected() -> None:
    candidates = (
        IndexedDevice(DeviceInfo("A", "identity"), 1),
        IndexedDevice(DeviceInfo("B", "identity"), 2),
    )
    with pytest.raises(CaptureError):
        module.select_candidates(candidates, "identity")


def test_more_than_two_matching_apis_are_rejected_before_open() -> None:
    opened: list[IndexedDevice] = []

    class TooManyCandidates:
        def enumerate(self) -> tuple[IndexedDevice, ...]:
            return tuple(IndexedDevice(DeviceInfo("Same", "identity"), 0, api) for api in (1, 2, 3))

        def open(self, device: IndexedDevice) -> Camera:
            opened.append(device)
            return Camera([])

    with pytest.raises(CaptureError):
        module.acquire_frames(
            CaptureOptions("identity"),
            1,
            TooManyCandidates(),
            lambda: False,
            lambda _frame: None,
        )
    assert not opened
