from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from gigas.ui.message_composer import MessageComposer


def test_composer_keeps_draft_and_switches_actions_in_the_same_position(qtbot: QtBot) -> None:
    composer = MessageComposer()
    qtbot.addWidget(composer)
    composer.resize(320, composer.sizeHint().height())
    composer.show()
    assert composer.prompt.placeholderText() == "Message"
    assert composer.prompt.toPlainText() == ""
    composer.prompt.setPlainText("途中の指示🙂")
    composer.prompt.selectAll()
    composer.render_actions(
        send_enabled=True, interrupt_enabled=False, running=False, has_draft=True
    )
    qtbot.waitUntil(composer.send_button.isVisible)
    edge = composer.send_button.geometry().right()
    assert not composer.interrupt_button.isVisible()
    composer.render_actions(
        send_enabled=False, interrupt_enabled=True, running=True, has_draft=False
    )
    qtbot.waitUntil(composer.interrupt_button.isVisible)
    assert not composer.send_button.isVisible()
    assert composer.interrupt_button.geometry().right() == edge
    composer.render_actions(send_enabled=True, interrupt_enabled=True, running=True, has_draft=True)
    qtbot.waitUntil(composer.send_button.isVisible)
    assert composer.interrupt_button.isVisible()
    assert composer.interrupt_button.geometry().right() < composer.send_button.geometry().left()
    assert composer.prompt.textCursor().selectedText() == "途中の指示🙂"
    composer.prompt.copy()
    assert QApplication.clipboard().text() == "途中の指示🙂"
    for child in (composer.prompt, composer.model_button, composer.send_button):
        assert composer.rect().contains(QRect(child.mapTo(composer, QPoint()), child.size()))


def test_composer_long_model_and_keyboard_gesture_do_not_change_input(qtbot: QtBot) -> None:
    composer = MessageComposer()
    qtbot.addWidget(composer)
    composer.resize(320, composer.sizeHint().height())
    composer.set_model("model-" * 35, "high")
    composer.show()
    composer.render_actions(
        send_enabled=False, interrupt_enabled=False, running=True, has_draft=False
    )
    assert composer.interrupt_button.isVisible()
    assert not composer.interrupt_button.isEnabled()
    assert composer.width() == 320
    assert "model-" * 35 in composer.model_button.text()
    assert composer.model_button.width() < composer.width()
    gestures: list[bool] = []
    composer.prompt.submit_requested.connect(lambda: gestures.append(True))
    composer.prompt.setPlainText("東へ")
    qtbot.keyClick(composer.prompt, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert gestures == [True]
    assert composer.prompt.toPlainText() == "東へ"


def test_composer_paints_one_surface_and_fits_a_normal_model_label(qtbot: QtBot) -> None:
    composer = MessageComposer()
    composer.setFont(QFont("Meiryo UI", 11))
    qtbot.addWidget(composer)
    palette = composer.palette()
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#292929"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#444444"))
    composer.setPalette(palette)
    composer.set_model("gpt-6.1-sol", "low")
    composer.resize(500, composer.sizeHint().height())
    composer.show()
    image = composer.grab().toImage()
    middle = composer.prompt.viewport().mapTo(composer, composer.prompt.viewport().rect().center())
    assert image.pixelColor(middle) == image.pixelColor(6, middle.y())
    button = composer.model_button
    assert (
        button.fontMetrics().elidedText(
            button.text(), Qt.TextElideMode.ElideRight, button.width() - 20
        )
        == button.text()
    )
