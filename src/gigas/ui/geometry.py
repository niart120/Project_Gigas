"""Keep visible application windows within the current screen."""

from PySide6.QtWidgets import QWidget


def fit_to_screen(window: QWidget) -> None:
    """Clamp a shown window's frame to the available screen geometry.

    Args:
        window: Top-level window after native decorations have been created.
    """
    screen = window.screen()
    if screen is None:
        return
    available = screen.availableGeometry()
    frame = window.frameGeometry()
    extra_width = frame.width() - window.width()
    extra_height = frame.height() - window.height()
    window.resize(
        min(window.width(), available.width() - extra_width),
        min(window.height(), available.height() - extra_height),
    )
    frame = window.frameGeometry()
    window.move(
        max(available.left(), min(frame.left(), available.right() - frame.width() + 1)),
        max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1)),
    )
