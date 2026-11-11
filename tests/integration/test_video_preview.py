from datetime import UTC, datetime

from PySide6.QtGui import QColor
from pytestqt.qtbot import QtBot

from gigas.game_io.models import Frame
from gigas.ui.video_panel import VideoView


def test_preview_preserves_pixels_aspect_and_clears_stopped_image(qtbot: QtBot) -> None:
    view = VideoView()
    qtbot.addWidget(view)
    view.resize(200, 200)
    view.show()
    pixels = bytes([0, 0, 255]) * 8
    frame = Frame(pixels, 4, 2, 1, 1, datetime.now(UTC), 1)

    view.set_frame(frame)

    rendered = view.grab().toImage()
    assert rendered.pixelColor(100, 10) == QColor("black")
    assert rendered.pixelColor(100, 50) == QColor("red")
    assert rendered.pixelColor(100, 149) == QColor("red")
    assert rendered.pixelColor(100, 160) == QColor("black")
    assert frame.bgr == pixels
    view.resize(100, 200)
    resized = view.grab().toImage()
    assert resized.pixelColor(50, 74) == QColor("black")
    assert resized.pixelColor(50, 75) == QColor("red")
    assert resized.pixelColor(50, 124) == QColor("red")
    assert resized.pixelColor(50, 125) == QColor("black")

    newest = Frame(bytes([255, 0, 0]) * 8, 4, 2, 1, 20, datetime.now(UTC), 20)
    view.set_frame(newest)
    assert view.grab().toImage().pixelColor(50, 100) == QColor("blue")
    assert frame.bgr == pixels
    view.set_frame(None)
    assert view.grab().toImage().pixelColor(50, 100) == QColor("black")
    reconnected = Frame(bytes([0, 255, 0]) * 8, 4, 2, 2, 1, datetime.now(UTC), 30)
    view.set_frame(reconnected)
    assert view.grab().toImage().pixelColor(50, 100) == QColor("lime")
