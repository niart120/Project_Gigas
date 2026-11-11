import pytest
from pytestqt.qtbot import QtBot

from gigas.app.video import VideoController
from gigas.storage.settings import Settings
from gigas.ui.video_panel import VideoPanel
from tests.integration.peers.controlled_capture import ControlledCapture


@pytest.mark.parametrize(
    ("resolution", "dimensions"),
    [
        ("auto", (None, None)),
        ("hd", (1280, 720)),
        ("full_hd", (1920, 1080)),
        ("wqhd", (2560, 1440)),
        ("four_k", (3840, 2160)),
    ],
)
def test_saved_resolution_becomes_a_device_request(
    qtbot: QtBot, resolution: str, dimensions: tuple[int | None, int | None]
) -> None:
    panel = VideoPanel()
    qtbot.addWidget(panel)
    capture = ControlledCapture()
    controller = VideoController(panel, capture)
    controller.start()
    controller.set_available(True)
    try:
        controller.apply_capture(
            Settings(capture_device="path-a", capture_resolution=resolution, capture_fps=30)
        )
        assert len(capture.connections) == 1
        request = capture.connections[0]
        assert (request.width, request.height) == dimensions
        assert request.fps == 30
    finally:
        controller.shutdown()
        assert controller.shutdown_complete
