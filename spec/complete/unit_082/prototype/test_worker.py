"""Validate asynchronous parsing boundaries with the actual Qt worker."""

import pytest
from PySide6.QtCore import QPoint
from worker import AsyncMessageView


@pytest.fixture
def view(qtbot):
    widget = AsyncMessageView()
    qtbot.addWidget(widget)
    widget.resize(640, 400)
    widget.show()
    yield widget
    widget.shutdown()


def settled(qtbot, view):
    qtbot.waitUntil(
        lambda: not view.busy and not view.pending and not view.timer.isActive(), timeout=5000
    )


def test_latest_source_coalesces_during_inflight_parse(qtbot, view):
    text = "## 日本語\n\n長い本文 😀\n\n" * 3000
    view.enqueue("live", text)
    view.flush()
    assert view.busy
    for _ in range(200):
        view.enqueue("live", "追記")
    assert len(view.pending) == 1
    settled(qtbot, view)
    assert view.messages["live"].source == text + "追記" * 200
    assert view.applied_count == 2


def test_completion_supersedes_inflight_parse(qtbot, view):
    view.enqueue("live", "**未確定**\n\n" * 3000)
    view.flush()
    view.finish("live", "# 確定した回答")
    view.enqueue("live", "遅着")
    settled(qtbot, view)
    assert view.messages["live"].source == "# 確定した回答"
    assert view.applied_count == 1
    assert view.messages["live"].blocks is None


def test_switch_discards_old_worker_result_and_selection_survives(qtbot, view):
    view.enqueue("old", "旧会話\n\n" * 3000)
    view.flush()
    old = view.generation
    view.reset()
    view.put("history", "選択を保つ 😀\n\n" * 70)
    view.setTextCursor(view.document().find("選択を保つ 😀"))
    view.verticalScrollBar().setValue(view.verticalScrollBar().maximum() // 2)
    cursor = view.cursorForPosition(QPoint(30, 4))
    y = view.cursorRect(cursor).top()
    view.enqueue("late", "旧会話の遅着", generation=old)
    view.enqueue("new", "新しい本文 **強調**")
    settled(qtbot, view)
    assert set(view.messages) == {"history", "new"}
    assert view.textCursor().selectedText() == "選択を保つ 😀"
    assert view.cursorRect(cursor).top() == y
    assert not view.thread.isInterruptionRequested()
