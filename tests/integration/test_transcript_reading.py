import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QTextCursor
from pytestqt.qtbot import QtBot

from gigas.runtime.codex.models import TextEvent
from gigas.services.codex import ServiceEvent
from gigas.ui.connection_panel import ConnectionPanel
from gigas.ui.transcript import Transcript


@pytest.fixture
def panel(qtbot: QtBot) -> ConnectionPanel:
    """Show a bounded conversation with enough history to scroll."""
    widget = ConnectionPanel()
    qtbot.addWidget(widget)
    widget.resize(680, 500)
    widget.show()
    widget.activate(1)
    widget.handle_event(ServiceEvent(1, TextEvent("intro", "冒頭 🐉", role="user", replace=True)))
    widget.handle_event(
        ServiceEvent(
            1,
            TextEvent("history", "\n".join(f"行{i:03d} 🐉 本文" for i in range(100))),
        )
    )
    widget.handle_event(ServiceEvent(1, TextEvent("tail", "生成中")))
    qtbot.waitUntil(lambda: widget.transcript.verticalScrollBar().maximum() > 50)
    return widget


def _select_history(panel: ConnectionPanel) -> tuple[str, str]:
    transcript = panel.transcript
    cursor = transcript.document().find("行030 🐉 本文")
    assert not cursor.isNull()
    transcript.setTextCursor(cursor)
    transcript.verticalScrollBar().setValue(20)
    visible = transcript.cursorForPosition(QPoint(0, 0)).block().text()
    return cursor.selectedText(), visible


@pytest.mark.parametrize("change", ["append", "new", "replace_earlier", "replace_current"])
def test_updates_preserve_selected_text_and_visible_history(
    panel: ConnectionPanel, change: str
) -> None:
    selected, visible = _select_history(panel)
    events = {
        "append": TextEvent("tail", "\n次の断片"),
        "new": TextEvent("new", "別の応答 🐉"),
        "replace_earlier": TextEvent(
            "intro", "\n".join(["置換 🐉"] * 15), role="user", replace=True
        ),
        "replace_current": TextEvent(
            "history",
            "追加 🐉\n" * 12 + "\n".join(f"行{i:03d} 🐉 本文" for i in range(100)),
            replace=True,
        ),
    }
    panel.handle_event(ServiceEvent(1, events[change]))
    assert panel.transcript.textCursor().selectedText() == selected
    assert panel.transcript.cursorForPosition(QPoint(0, 0)).block().text() == visible
    assert (
        panel.transcript.verticalScrollBar().value()
        < panel.transcript.verticalScrollBar().maximum()
    )


def test_tail_follows_new_text_only_when_reading_without_selection(panel: ConnectionPanel) -> None:
    transcript = panel.transcript
    cursor = transcript.textCursor()
    cursor.clearSelection()
    transcript.setTextCursor(cursor)
    scrollbar = transcript.verticalScrollBar()
    scrollbar.setValue(scrollbar.maximum())
    panel.handle_event(ServiceEvent(1, TextEvent("tail", "\n新しい行" * 15)))
    assert scrollbar.value() == scrollbar.maximum()


def test_selection_at_tail_suspends_following(panel: ConnectionPanel) -> None:
    transcript = panel.transcript
    cursor = transcript.document().find("生成中")
    transcript.setTextCursor(cursor)
    scrollbar = transcript.verticalScrollBar()
    scrollbar.setValue(scrollbar.maximum())
    before = scrollbar.value()
    panel.handle_event(ServiceEvent(1, TextEvent("tail", "\n新しい行" * 15)))
    assert transcript.textCursor().selectedText() == "生成中"
    assert scrollbar.value() == before
    assert scrollbar.value() < scrollbar.maximum()


def test_replacement_preserves_other_items_and_unicode_boundaries(panel: ConnectionPanel) -> None:
    panel.handle_event(
        ServiceEvent(1, TextEvent("intro", "冒頭 🐉 終端", role="user", replace=True))
    )
    panel.handle_event(
        ServiceEvent(1, TextEvent("intro", "冒頭 🐈 終端", role="user", replace=True))
    )
    panel.handle_event(ServiceEvent(1, TextEvent("tail", "<b>🐉 完了</b>", replace=True)))
    text = panel.transcript.toPlainText()
    assert text.startswith("あなた\n冒頭 🐈 終端\n\nCodex\n行000 🐉 本文")
    assert "行099 🐉 本文" in text
    assert text.endswith("\n\nCodex\n<b>🐉 完了</b>")
    panel.activate(2)
    assert panel.transcript.toPlainText() == ""
    panel.handle_event(ServiceEvent(1, TextEvent("old", "古い世代")))
    panel.handle_event(ServiceEvent(2, TextEvent("fresh", "新しい世代 🐉")))
    assert panel.transcript.toPlainText() == "Codex\n新しい世代 🐉"


def test_update_keeps_backward_selection_direction(panel: ConnectionPanel) -> None:
    cursor = panel.transcript.document().find("行030 🐉 本文")
    start, end = cursor.selectionStart(), cursor.selectionEnd()
    cursor.setPosition(end)
    cursor.setPosition(start, QTextCursor.MoveMode.KeepAnchor)
    panel.transcript.setTextCursor(cursor)
    panel.transcript.verticalScrollBar().setValue(20)
    panel.handle_event(ServiceEvent(1, TextEvent("tail", "追加")))
    updated = panel.transcript.textCursor()
    assert updated.position() == start
    assert updated.anchor() == end


@pytest.mark.parametrize(
    ("separator", "backward"),
    [("\r\n", False), ("\r", True), ("\r\r\n", True), ("\u2029", False)],
)
def test_paragraph_updates_preserve_selection_and_visible_history(
    panel: ConnectionPanel, separator: str, backward: bool
) -> None:
    selected, visible = _select_history(panel)
    if backward:
        cursor = panel.transcript.textCursor()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        cursor.setPosition(end)
        cursor.setPosition(start, QTextCursor.MoveMode.KeepAnchor)
        panel.transcript.setTextCursor(cursor)
        panel.transcript.verticalScrollBar().setValue(20)
    panel.handle_event(
        ServiceEvent(1, TextEvent("intro", f"追加{separator}冒頭 🐉", role="user", replace=True))
    )
    cursor = panel.transcript.textCursor()
    assert cursor.selectedText() == selected
    assert (cursor.position() < cursor.anchor()) == backward
    assert panel.transcript.cursorForPosition(QPoint(0, 0)).block().text() == visible
    panel.handle_event(ServiceEvent(1, TextEvent("tail", "追記")))
    assert panel.transcript.textCursor().selectedText() == selected


@pytest.mark.parametrize("spacing", ["\u2028", "\u00a0"])
def test_updates_keep_soft_breaks_and_nonbreaking_spaces(qtbot: QtBot, spacing: str) -> None:
    transcript = Transcript()
    qtbot.addWidget(transcript)
    original = f"A{spacing}KEEP{spacing}B\n\t&nbsp;"
    transcript.set_text(original)
    transcript.setTextCursor(transcript.document().find("KEEP"))

    transcript.set_text("new\n" + original)
    assert transcript.textCursor().selectedText() == "KEEP"
    transcript.set_text("new\n" + original + "\nappend")
    assert transcript.textCursor().selectedText() == "KEEP"
    assert transcript.document().toRawText().count(spacing) == 2
    assert "\t&nbsp;" in transcript.toPlainText()


def test_replacement_above_a_wrapped_view_keeps_its_visible_passage(
    panel: ConnectionPanel, qtbot: QtBot
) -> None:
    panel.activate(2)
    paragraph = " ".join(f"word{i:04d}" for i in range(500))
    panel.handle_event(ServiceEvent(2, TextEvent("wrapped", paragraph)))
    transcript = panel.transcript
    qtbot.waitUntil(lambda: transcript.verticalScrollBar().maximum() > 20)
    transcript.verticalScrollBar().setValue(12)
    visible = transcript.cursorForPosition(QPoint(0, 0))
    visible.select(QTextCursor.SelectionType.WordUnderCursor)
    passage = visible.selectedText()
    assert passage.startswith("word")
    panel.handle_event(
        ServiceEvent(2, TextEvent("wrapped", "prefix " * 300 + paragraph, replace=True))
    )
    retained = transcript.document().find(passage)
    assert not retained.isNull()
    top = transcript.cursorRect(retained).top()
    assert 0 <= top <= transcript.fontMetrics().lineSpacing()


@pytest.mark.parametrize(
    ("text", "anchor", "position"),
    [("🐉XY", 0, 1), ("🐉XY", 1, 0), ("X🐉Y", 1, 2), ("XY🐉", 2, 3)],
)
def test_replacement_clamps_selection_to_complete_unicode_characters(
    qtbot: QtBot, text: str, anchor: int, position: int
) -> None:
    transcript = Transcript()
    qtbot.addWidget(transcript)
    transcript.set_text("abcd")
    cursor = transcript.textCursor()
    cursor.setPosition(anchor)
    cursor.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
    transcript.setTextCursor(cursor)

    transcript.set_text(text)

    cursor = transcript.textCursor()
    assert cursor.selectedText() == "🐉"
    assert (cursor.anchor() > cursor.position()) == (anchor > position)
