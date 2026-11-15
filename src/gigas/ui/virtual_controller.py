"""Nonmodal mouse controller, independent of input devices and Codex."""

from math import ceil
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QWidget

from gigas.ui.controller_model import ManualInputModel

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent, QPaintEvent, QResizeEvent

    from gigas.game_io.input.config import InputProtocol
    from gigas.game_io.input.models import InputState


class _Control(QWidget):
    """Paint one control and report mouse gestures without keyboard activation."""

    pressed = Signal(float, float)
    moved = Signal(float, float)
    released = Signal()

    def __init__(self, name: str, label: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.name = name
        self.label = label
        self.dragging = False
        self.axes = (0, 0)
        self.directions: tuple[str, ...] = ()
        self.setAccessibleName(label or name)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def reset(self) -> None:
        self.dragging = False
        self.axes = (0, 0)
        self.directions = ()
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()
        self.update()

    def _position(self, event: "QMouseEvent") -> tuple[float, float]:
        radius = min(self.width(), self.height()) * (0.28 if "stick" in self.name else 0.5)
        return (
            (event.position().x() - self.width() / 2) / radius,
            (event.position().y() - self.height() / 2) / radius,
        )

    def mousePressEvent(self, event: "QMouseEvent") -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.dragging = True
            self.pressed.emit(*self._position(event))
            self.update()
            event.accept()
        else:
            event.ignore()

    def mouseMoveEvent(self, event: "QMouseEvent") -> None:  # noqa: N802
        if self.dragging and self.isEnabled():
            self.moved.emit(*self._position(event))
            event.accept()

    def mouseReleaseEvent(self, event: "QMouseEvent") -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.dragging:
            self.reset()
            self.released.emit()
            event.accept()

    def paintEvent(self, _event: "QPaintEvent") -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        palette = self.palette()
        fill = palette.color(group, QPalette.ColorRole.Button)
        ink = palette.color(group, QPalette.ColorRole.ButtonText)
        border = QColor(ink)
        border.setAlpha(105 if self.isEnabled() else 45)
        accent = palette.color(group, QPalette.ColorRole.Highlight)
        painter.setPen(QPen(border, 2))
        painter.setBrush(accent if self.dragging and self.name != "pad" else fill)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if "stick" in self.name:
            painter.setBrush(fill)
            painter.drawEllipse(rect)
            color = QColor("#00bedf" if self.name == "left_stick" else "#f34b52")
            if not self.isEnabled():
                color.setAlpha(75)
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            radius = min(self.width(), self.height()) * 0.28
            center = rect.center() + QPointF(
                self.axes[0] * radius / 100, self.axes[1] * radius / 100
            )
            knob = min(self.width(), self.height()) * 0.2
            painter.drawEllipse(center, knob, knob)
        elif self.name == "pad":
            size = rect.width()
            arm = size * 0.32
            middle = (size - arm) / 2
            path = QPainterPath()
            path.addRoundedRect(QRectF(rect.left() + middle, rect.top(), arm, size), 10, 10)
            cross = QPainterPath()
            cross.addRoundedRect(QRectF(rect.left(), rect.top() + middle, size, arm), 10, 10)
            path = path.united(cross)
            painter.drawPath(path)
            painter.drawRoundedRect(
                QRectF(rect.left() + middle, rect.top() + middle, arm, arm), 7, 7
            )
            painter.setClipPath(path)
            for name, region in (
                ("UP", QRectF(rect.left() + middle, rect.top(), arm, middle)),
                ("DOWN", QRectF(rect.left() + middle, rect.top() + middle + arm, arm, middle)),
                ("LEFT", QRectF(rect.left(), rect.top() + middle, middle, arm)),
                ("RIGHT", QRectF(rect.left() + middle + arm, rect.top() + middle, middle, arm)),
            ):
                if name in self.directions:
                    painter.fillRect(region, accent)
        else:
            if self.name in {"A", "B", "X", "Y"}:
                painter.drawEllipse(rect)
            else:
                painter.drawRoundedRect(rect, 9, 9)
            painter.setPen(QPen(ink, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if self.name in {"HOME", "CAPTURE"}:
                painter.save()
                painter.translate(rect.center())
                scale = min(rect.width(), rect.height()) / 40
                painter.scale(scale, scale)
                path = QPainterPath()
                if self.name == "HOME":
                    path.moveTo(-11, 0)
                    path.lineTo(0, -10)
                    path.lineTo(11, 0)
                    path.moveTo(-9, -1)
                    path.lineTo(-9, 10)
                    path.lineTo(9, 10)
                    path.lineTo(9, -1)
                else:
                    path.moveTo(-11, -7)
                    for x, y in (
                        (-6, -7),
                        (-3, -11),
                        (4, -11),
                        (7, -7),
                        (11, -7),
                        (11, 10),
                        (-11, 10),
                        (-11, -7),
                    ):
                        path.lineTo(x, y)
                    painter.drawEllipse(QPointF(0, 1), 5, 5)
                painter.drawPath(path)
                painter.restore()
            elif self.name in {"MINUS", "PLUS"} and len(self.label) == 1:
                center = rect.center()
                half = min(rect.width(), rect.height()) * 0.23
                painter.setPen(QPen(ink, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawLine(center - QPointF(half, 0), center + QPointF(half, 0))
                if self.name == "PLUS":
                    painter.drawLine(center - QPointF(0, half), center + QPointF(0, half))
            else:
                font = QFont(self.font())
                pixels = max(self.fontMetrics().height() - 3, round(rect.height() * 0.58))
                font.setPixelSize(pixels)
                metrics = QFontMetricsF(font)
                ratio = min(
                    1.0,
                    (rect.width() - 12) / metrics.horizontalAdvance(self.label),
                    (rect.height() - 8) / metrics.height(),
                )
                font.setPixelSize(max(1, int(pixels * ratio)))
                painter.setFont(font)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.label)


class VirtualController(QWidget):
    """Display one mouse controller and emit complete states or manual release.

    The caller supplies availability and capabilities. Disabling clears local
    gestures silently because the service owns execution and connection release.
    """

    input_changed = Signal(object)
    release_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """Create a disconnected native window without acquiring any input."""
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("コントローラ")
        font = QFont(self.font())
        font.setPointSizeF(max(12.0, font.pointSizeF()))
        self.setFont(font)
        self.model = ManualInputModel()
        self.controls: dict[str, _Control] = {}
        self._protocol: InputProtocol | None = None
        self._pending: InputState | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush_axes)
        for name, label in (
            ("ZL", "ZL"),
            ("L", "L"),
            ("LS", "LS"),
            ("RS", "RS"),
            ("R", "R"),
            ("ZR", "ZR"),
            ("X", "X"),
            ("Y", "Y"),
            ("A", "A"),
            ("B", "B"),
            ("MINUS", "−"),  # noqa: RUF001 -- controller minus symbol
            ("PLUS", "+"),
            ("CAPTURE", "撮影"),
            ("HOME", "HOME"),
            ("left_stick", "左スティック"),
            ("right_stick", "右スティック"),
            ("pad", "十字キー"),
        ):
            control = _Control(name, label, self)
            self.controls[name] = control
            control.pressed.connect(lambda x, y, key=name: self._change(key, x, y, immediate=True))
            control.moved.connect(lambda x, y, key=name: self._change(key, x, y, immediate=False))
            control.released.connect(self._release)
        self.resize(640, 420)
        self.set_input_available(False, None)

    def set_input_available(self, available: bool, protocol: "InputProtocol | None") -> None:
        """Apply service admission and device capabilities, never restoring holds."""
        if not available or protocol != self._protocol:
            self._reset()
        self._protocol = protocol
        is_3ds = protocol is not None and protocol.key == "3ds"
        for name, control in self.controls.items():
            button = {"MINUS": "SELECT", "PLUS": "START"}.get(name, name) if is_3ds else name
            supported = protocol is not None and (
                name in {"left_stick", "right_stick", "pad"} or button in protocol.buttons
            )
            control.setEnabled(available and supported)
        self.controls["MINUS"].label = "SELECT" if is_3ds else "−"  # noqa: RUF001
        self.controls["PLUS"].label = "START" if is_3ds else "+"
        for name in ("MINUS", "PLUS"):
            self.controls[name].setAccessibleName(self.controls[name].label)
            self.controls[name].update()
        self._layout_controls()

    def _reset(self) -> bool:
        self._timer.stop()
        self._pending = None
        held = self.model.release()
        for control in self.controls.values():
            control.reset()
        return held

    def _release(self) -> None:
        if self._reset():
            self.release_requested.emit()

    def _change(self, name: str, x: float, y: float, *, immediate: bool) -> None:
        if "stick" in name:
            self.model.move_stick("left" if name == "left_stick" else "right", x, y)
            self.controls[name].axes = (
                self.model.state.left_stick
                if name == "left_stick"
                else self.model.state.right_stick
            )
        elif name == "pad":
            self.model.move_pad(x, y)
            self.controls[name].directions = self.model.state.buttons
        elif immediate:
            is_3ds = self._protocol is not None and self._protocol.key == "3ds"
            self.model.press(
                {"MINUS": "SELECT", "PLUS": "START"}.get(name, name) if is_3ds else name
            )
        else:
            return
        self.controls[name].update()
        if immediate or name == "pad":
            self.input_changed.emit(self.model.state)
        else:
            self._pending = self.model.state
            if not self._timer.isActive():
                self._timer.start()

    def _flush_axes(self) -> None:
        if self._pending is not None:
            self.input_changed.emit(self._pending)
            self._pending = None

    def event(self, event: QEvent) -> bool:
        """Release a held gesture when the window closes, hides or deactivates."""
        if hasattr(self, "model"):
            if event.type() in {
                QEvent.Type.WindowDeactivate,
                QEvent.Type.Hide,
                QEvent.Type.Close,
            } or (event.type() == QEvent.Type.WindowStateChange and self.isMinimized()):
                self._release()
            elif event.type() == QEvent.Type.FontChange:
                self._layout_controls()
        return super().event(event)

    def resizeEvent(self, event: "QResizeEvent") -> None:  # noqa: N802
        """Scale the adopted layout within the native window's client area."""
        super().resizeEvent(event)
        self._layout_controls()

    def _layout_controls(self) -> None:
        label_width = (
            max(
                self.fontMetrics().horizontalAdvance(self.controls[name].label)
                for name in ("MINUS", "PLUS")
            )
            + 16
        )
        self.setMinimumSize(
            max(520, ceil(2 * label_width / 0.27)),
            max(350, ceil((self.fontMetrics().height() + 12) * 420 / 34)),
        )
        sx, sy = self.width() / 640, self.height() / 420
        for name, x, y, w, h in (
            ("ZL", 24, 16, 64, 34),
            ("L", 100, 16, 64, 34),
            ("LS", 176, 16, 64, 34),
            ("RS", 400, 16, 64, 34),
            ("R", 476, 16, 64, 34),
            ("ZR", 552, 16, 64, 34),
            ("left_stick", 82, 92, 112, 112),
            ("right_stick", 400, 270, 112, 112),
            ("pad", 88, 244, 146, 146),
            ("X", 468, 82, 50, 50),
            ("Y", 416, 134, 50, 50),
            ("A", 520, 134, 50, 50),
            ("B", 468, 186, 50, 50),
        ):
            # Circular controls keep their aspect ratio when the window is resized.
            width, height = w * sx, h * sy
            if w == h:
                width = height = min(width, height)
            self.controls[name].setGeometry(
                round(x * sx), round(y * sy), round(width), round(height)
            )
        width = max(48 * sx, label_width)
        gap = 14 * sx
        for name, right, y in (
            ("MINUS", False, 116),
            ("PLUS", True, 116),
            ("CAPTURE", False, 168),
            ("HOME", True, 168),
        ):
            x = self.width() / 2 + gap / 2 if right else self.width() / 2 - gap / 2 - width
            self.controls[name].setGeometry(round(x), round(y * sy), round(width), round(40 * sy))
