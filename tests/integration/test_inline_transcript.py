from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from gigas.ui.transcript import Transcript

if TYPE_CHECKING:
    from pytestqt.logging import _QtMessageCapture
    from pytestqt.qtbot import QtBot


def _history(qtbot: "QtBot") -> Transcript:
    view = Transcript()
    qtbot.addWidget(view)
    view.resize(440, 300)
    view.show()
    view.setFocus()
    qtbot.wait(1)
    view.update_item("question", "経路 😀\n北側か南側か。", kind="request")
    for index in range(45):
        view.update_item(str(index), f"応答 {index}\n本文")
    return view


def _form() -> tuple[QWidget, QLineEdit]:
    control = QWidget()
    layout = QVBoxLayout(control)
    label = QLabel("選択肢の補足 " * 18, control)
    label.setWordWrap(True)
    layout.addWidget(label)
    editor = QLineEdit(control)
    layout.addWidget(editor)
    layout.addWidget(QPushButton("回答", control))
    return control, editor


def test_inline_add_remove_keeps_selected_passage_and_lifetime(qtbot: "QtBot") -> None:
    view = _history(qtbot)
    selected = view.document().find("応答 20")
    selected.setPosition(selected.selectionEnd())
    selected.movePosition(QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveMode.KeepAnchor)
    view.setTextCursor(selected)
    view.ensureCursorVisible()
    visible = view.cursorForPosition(QPoint(0, 1))
    visible_y = view.cursorRect(visible).top()
    control, editor = _form()
    editor.setText("下書き")
    for attached in (control, None, control):
        view.set_item_control("question", attached)
        qtbot.wait(1)
        assert view.textCursor().selectedText() == "応答 20"
        assert view.textCursor().anchor() > view.textCursor().position()
        assert abs(view.cursorRect(visible).top() - visible_y) <= 1
        assert control.isVisible() == (attached is control)
        assert editor.text() == "下書き"
    destroyed: list[bool] = []
    control.destroyed.connect(lambda: destroyed.append(True))
    view.set_item_control("question", None)
    view.clear()
    qtbot.waitUntil(lambda: bool(destroyed))


def test_inline_body_copy_and_geometry_follow_updates(qtbot: "QtBot") -> None:
    view = _history(qtbot)
    control, _ = _form()
    view.set_item_control("question", control)
    start = view.document().find("経路 😀").selectionStart()
    end = view.document().find("応答 1").selectionEnd()
    selection = QTextCursor(view.document())
    selection.setPosition(end)
    selection.setPosition(start, QTextCursor.MoveMode.KeepAnchor)
    view.setTextCursor(selection)
    view.copy()
    copied = QApplication.clipboard().text()
    assert "経路 😀" in copied
    assert "北側か南側か。" in copied
    assert "応答 1" in copied
    view.update_item("44", "末尾の追記", append=True)
    assert view.textCursor().selectedText().replace("\u2029", "\n") == copied
    view.verticalScrollBar().setValue(0)
    question = view.document().find("北側か南側か。")
    following = view.document().find("応答 0")
    assert control.y() > view.cursorRect(question).bottom()
    assert control.geometry().bottom() < view.cursorRect(following).top()
    old_height = control.height()
    view.resize(260, 300)
    qtbot.waitUntil(lambda: control.height() > old_height)
    assert control.geometry().bottom() < view.cursorRect(following).top()
    view.update_item("question", "経路 😀\n改行の追加\n北側か南側か。", kind="request")
    assert control.geometry().bottom() < view.cursorRect(view.document().find("応答 0")).top()


@pytest.mark.parametrize("selected", [False, True])
def test_inline_tail_follows_only_without_selection(qtbot: "QtBot", *, selected: bool) -> None:
    view = _history(qtbot)
    view.update_item("last-question", "最後の質問", kind="request")
    bar = view.verticalScrollBar()
    bar.setValue(bar.maximum())
    if selected:
        view.setTextCursor(view.document().find("最後の質問"))
    position = bar.value()
    control, _ = _form()
    view.set_item_control("last-question", control)
    qtbot.wait(1)
    if selected:
        assert view.textCursor().selectedText() == "最後の質問"
        assert bar.value() == position
        assert bar.value() < bar.maximum()
    else:
        assert bar.value() == bar.maximum()


def test_inline_layout_change_keeps_reading_position_and_focus_scrolls(qtbot: "QtBot") -> None:
    view = _history(qtbot)
    control, editor = _form()
    view.set_item_control("question", control)
    view.setTextCursor(view.document().find("応答 20"))
    view.ensureCursorVisible()
    visible = view.cursorForPosition(QPoint(0, 1))
    visible_y = view.cursorRect(visible).top()
    old_height = control.height()
    layout = control.layout()
    assert layout is not None
    layout.addWidget(QLabel("追加された判断対象\n" * 4, control))
    qtbot.waitUntil(lambda: control.height() > old_height)
    assert abs(view.cursorRect(visible).top() - visible_y) <= 1
    assert view.textCursor().selectedText() == "応答 20"
    editor.setFocus(Qt.FocusReason.TabFocusReason)
    qtbot.waitUntil(editor.hasFocus)
    y = editor.mapTo(view.viewport(), QPoint()).y()
    assert y >= 0
    assert y + editor.height() <= view.viewport().height()
    assert view.textCursor().selectedText() == "応答 20"


def test_inline_control_fits_when_its_height_introduces_scrollbar(qtbot: "QtBot") -> None:
    view = Transcript()
    qtbot.addWidget(view)
    view.resize(300, 160)
    view.show()
    qtbot.wait(1)
    view.update_item("question", "質問本文", kind="request")
    assert view.verticalScrollBar().maximum() == 0
    control, _ = _form()
    view.set_item_control("question", control)
    qtbot.waitUntil(lambda: view.verticalScrollBar().maximum() > 0)
    assert control.geometry().right() < view.viewport().width()
    view.update_item("question", "", kind="request")
    qtbot.waitUntil(lambda: view.verticalScrollBar().maximum() == 0)
    assert control.isHidden()


def test_inline_collapsing_reserved_question_items_never_uses_stale_positions(
    qtbot: "QtBot", qtlog: "_QtMessageCapture"
) -> None:
    view = Transcript()
    qtbot.addWidget(view)
    view.resize(440, 300)
    view.show()
    for item_id in ("summary", "question-a", "question-b", "status"):
        view.update_item(item_id, "", kind="request")
    for item_id in ("question-a", "question-b"):
        view.update_item(item_id, f"{item_id}\n質問の本文 😀", kind="request")
        control, _ = _form()
        view.set_item_control(item_id, control)
    view.update_item("status", "送信中", kind="request")
    view.update_item("summary", "北側", kind="request")
    for item_id in ("question-a", "question-b"):
        view.update_item(item_id, "", kind="request")
        view.set_item_control(item_id, None)
    view.update_item("status", "", kind="request")
    qtbot.wait(1)
    assert view.toPlainText().strip() == "北側"
    assert not [record.message for record in qtlog.records if "out of range" in record.message]
