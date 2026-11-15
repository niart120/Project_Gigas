import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QFont, QMouseEvent
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from gigas.game_io.input.config import get_protocol
from gigas.game_io.input.models import InputState
from gigas.ui.virtual_controller import VirtualController


@pytest.fixture
def controller(qtbot: QtBot) -> VirtualController:
    window = VirtualController()
    qtbot.addWidget(window)
    window.set_input_available(True, get_protocol("ch552"))
    with qtbot.waitActive(window):
        window.show()
    return window


def test_mouse_button_release_outside_and_keys_do_not_latch(
    controller: VirtualController, qtbot: QtBot
) -> None:
    states: list[InputState] = []
    ends: list[bool] = []
    controller.input_changed.connect(states.append)
    controller.release_requested.connect(lambda: ends.append(True))
    button = controller.controls["A"]
    qtbot.keyClick(button, Qt.Key.Key_Space)
    qtbot.keyClick(button, Qt.Key.Key_Return)
    assert states == []
    qtbot.mousePress(button, Qt.MouseButton.LeftButton)
    assert states == [InputState(buttons=("A",))]
    qtbot.mouseRelease(button, Qt.MouseButton.LeftButton, pos=QPoint(-10, -10))
    assert ends == [True]
    assert not controller.model.held
    assert not button.dragging


@pytest.mark.parametrize("event", [QEvent.Type.WindowDeactivate, QEvent.Type.Hide])
def test_window_events_release_only_a_held_gesture_once(
    controller: VirtualController, qtbot: QtBot, event: QEvent.Type
) -> None:
    ends: list[bool] = []
    controller.release_requested.connect(lambda: ends.append(True))
    button = controller.controls["B"]
    QApplication.sendEvent(controller, QEvent(event))
    assert ends == []
    qtbot.mousePress(button, Qt.MouseButton.LeftButton)
    QApplication.sendEvent(controller, QEvent(event))
    QApplication.sendEvent(controller, QEvent(event))
    assert ends == [True]
    assert not button.dragging
    qtbot.mouseRelease(button, Qt.MouseButton.LeftButton)
    assert ends == [True]


def test_disable_discards_drag_and_reenable_requires_a_new_press(
    controller: VirtualController, qtbot: QtBot
) -> None:
    states: list[InputState] = []
    ends: list[bool] = []
    controller.input_changed.connect(states.append)
    controller.release_requested.connect(lambda: ends.append(True))
    stick = controller.controls["left_stick"]
    qtbot.mousePress(
        stick, Qt.MouseButton.LeftButton, pos=QPoint(stick.width() - 1, stick.height() // 2)
    )
    assert states[-1].left_stick[0] > 0
    controller.set_input_available(False, get_protocol("ch552"))
    count = len(states)
    controller.set_input_available(True, get_protocol("ch552"))
    qtbot.mouseMove(stick, QPoint(0, stick.height() // 2))
    qtbot.mouseRelease(stick, Qt.MouseButton.LeftButton)
    assert len(states) == count
    assert ends == []
    assert not controller.model.held


def test_3ds_relabels_center_and_disables_unsupported_inputs(
    controller: VirtualController, qtbot: QtBot
) -> None:
    states: list[InputState] = []
    controller.input_changed.connect(states.append)
    controller.set_input_available(True, get_protocol("3ds"))
    for name in ("LS", "RS", "CAPTURE"):
        assert not controller.controls[name].isEnabled()
        qtbot.mouseClick(controller.controls[name], Qt.MouseButton.LeftButton)
    assert states == []
    qtbot.mouseClick(controller.controls["MINUS"], Qt.MouseButton.LeftButton)
    assert states == [InputState(buttons=("SELECT",))]
    qtbot.mouseClick(controller.controls["PLUS"], Qt.MouseButton.LeftButton)
    assert states[-1] == InputState(buttons=("START",))


def test_controls_fit_minimum_size_and_enlarged_font(controller: VirtualController) -> None:
    font = QFont(controller.font())
    font.setPointSize(18)
    controller.setFont(font)
    controller.set_input_available(True, get_protocol("3ds"))
    controller.resize(controller.minimumSize())
    widgets = list(controller.controls.values())
    for index, control in enumerate(widgets):
        assert controller.rect().contains(control.geometry())
        for other in widgets[index + 1 :]:
            assert not control.geometry().intersects(other.geometry())
    for name in ("MINUS", "PLUS"):
        control = controller.controls[name]
        assert control.fontMetrics().horizontalAdvance(control.label) + 12 <= control.width()


@pytest.mark.parametrize("end", ["release", "close", "minimize", "disable"])
def test_pending_axes_are_discarded_on_gesture_end(
    controller: VirtualController, qtbot: QtBot, end: str
) -> None:
    states: list[InputState] = []
    ends: list[bool] = []
    controller.input_changed.connect(states.append)
    controller.release_requested.connect(lambda: ends.append(True))
    stick = controller.controls["left_stick"]
    center = QPointF(stick.rect().center())
    qtbot.mousePress(
        stick, Qt.MouseButton.LeftButton, pos=QPoint(stick.width() - 1, stick.height() // 2)
    )
    for point in (center + QPointF(10, 10), center):
        QApplication.sendEvent(
            stick,
            QMouseEvent(
                QEvent.Type.MouseMove,
                point,
                point,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
    assert controller.model.held
    if end == "release":
        qtbot.mouseRelease(stick, Qt.MouseButton.LeftButton)
    elif end == "close":
        controller.close()
    elif end == "minimize":
        controller.showMinimized()
    else:
        controller.set_input_available(False, get_protocol("ch552"))
    qtbot.wait(40)
    assert len(states) == 1
    assert ends == ([] if end == "disable" else [True])
    assert not controller.model.held
    assert not stick.dragging


def test_axis_updates_keep_latest_position_and_child_focus_does_not_release(
    controller: VirtualController, qtbot: QtBot
) -> None:
    states: list[InputState] = []
    ends: list[bool] = []
    controller.input_changed.connect(states.append)
    controller.release_requested.connect(lambda: ends.append(True))
    stick = controller.controls["right_stick"]
    center = QPointF(stick.width() / 2, stick.height() / 2)
    qtbot.mousePress(stick, Qt.MouseButton.LeftButton)
    for point in (center + QPointF(10, 0), center + QPointF(100, 0), center):
        QApplication.sendEvent(
            stick,
            QMouseEvent(
                QEvent.Type.MouseMove,
                point,
                point,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
    QApplication.sendEvent(stick, QEvent(QEvent.Type.FocusOut))
    qtbot.waitUntil(lambda: len(states) == 2)
    assert states[-1] == InputState()
    assert controller.model.held
    assert ends == []
    qtbot.mouseRelease(stick, Qt.MouseButton.LeftButton)
    assert ends == [True]
