"""Behavioral probes for the independent M1 renderer (not product tests)."""

import pytest
from document import MessageView, blocks, rich_document, units
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QTextCursor, QTextFormat
from PySide6.QtWidgets import QApplication, QPushButton
from subtitle import Subtitle


def visible_text(view):
    cursor = view.cursorForPosition(QPoint(30, 4))
    cursor.select(QTextCursor.SelectionType.WordUnderCursor)
    return cursor.selectedText(), view.cursorRect(cursor).top()


def test_selection_survives_stream_update(qtbot):
    view = MessageView()
    qtbot.addWidget(view)
    view.resize(640, 400)
    view.show()
    view.put("old", "コピーする過去の文 😀")
    view.put("live", "## 生成中\n\n本文")
    cursor = view.document().find("過去の文 😀")
    cursor.setPosition(cursor.selectionEnd())
    cursor.setPosition(cursor.position() - 8, QTextCursor.MoveMode.KeepAnchor)
    view.setTextCursor(cursor)
    before = view.textCursor().selectedText()
    direction = view.textCursor().position() < view.textCursor().anchor()
    view.put("live", "## 生成中\n\n本文を追記 **強調**")
    assert view.textCursor().selectedText() == before
    assert (view.textCursor().position() < view.textCursor().anchor()) == direction


def test_reading_position_survives_update_above_viewport(qtbot):
    view = MessageView()
    qtbot.addWidget(view)
    view.resize(640, 400)
    view.show()
    view.put("changing", "短い冒頭")
    view.put("history", "\n\n".join(f"記録{n}の本文です。" for n in range(80)))
    view.verticalScrollBar().setValue(view.verticalScrollBar().maximum() // 2)
    qtbot.wait(20)
    before = visible_text(view)
    view.put("changing", "追記された行\n\n" * 20)
    qtbot.wait(20)
    assert visible_text(view) == before


def test_heading_scale_and_semantics():
    doc = rich_document(
        "# 大見出し\n\n## 中見出し\n\n### 小見出し\n\n本文 **太字** *斜体*",
        "final_answer",
        QFont("Meiryo", 10),
    )
    sizes = {}
    block = doc.begin()
    while block.isValid():
        if level := block.blockFormat().headingLevel():
            sizes[level] = block.begin().fragment().charFormat().fontPointSize()
        block = block.next()
    assert sizes == {1: 11.5, 2: 11.5, 3: 10.0}
    assert doc.begin().blockFormat().headingLevel() == 0
    cursor = doc.find("太字")
    assert cursor.charFormat().fontWeight() == QFont.Weight.Bold
    assert doc.find("斜体").charFormat().fontItalic()


@pytest.mark.parametrize(
    ("before", "after", "selected"),
    [
        ("途中の **太字", "途中の **太字** を追記", "太字"),
        ("保持する 😀 本文", "保持する 😀 本文\n\n追加", "保持する 😀 本文"),
        (
            "前の文\n\n真ん中の選択\n\n後の文",
            "修正された前\n\n真ん中の選択\n\n修正された後",
            "真ん中の選択",
        ),
        (
            "[参照][ref]\n\n残す本文",
            "[参照][ref]\n\n残す本文\n\n[ref]: https://example.com",
            "残す本文",
        ),
        ("```python\nprint('hello')", "```python\nprint('hello')\n```", "print('hello')"),
    ],
)
def test_selection_inside_reparsed_message(qtbot, before, after, selected):
    view = MessageView()
    qtbot.addWidget(view)
    view.put("live", before, "unknown")
    cursor = view.document().find(selected)
    assert not cursor.isNull()
    view.setTextCursor(cursor)
    view.put("live", after, "final_answer")
    assert view.textCursor().selectedText() == selected


def test_tail_follow_only_without_selection(qtbot):
    view = MessageView()
    qtbot.addWidget(view)
    view.resize(640, 300)
    view.show()
    text = "長い本文\n\n" * 70
    view.put("live", text)
    assert view.verticalScrollBar().value() == view.verticalScrollBar().maximum()
    cursor = view.document().find("長い本文")
    view.setTextCursor(cursor)
    view.verticalScrollBar().setValue(view.verticalScrollBar().maximum())
    before = visible_text(view)
    view.put("live", text + "新しい末尾\n\n" * 10)
    assert view.textCursor().selectedText() == "長い本文"
    assert visible_text(view) == before


def test_cross_message_selection_survives_other_update(qtbot):
    view = MessageView()
    qtbot.addWidget(view)
    view.put("first", "一つ目の選択")
    view.put("second", "二つ目の選択")
    view.put("live", "生成中")
    cursor = view.document().find("一つ目")
    cursor.setPosition(
        view.document().find("二つ目").selectionEnd(), QTextCursor.MoveMode.KeepAnchor
    )
    view.setTextCursor(cursor)
    before = view.textCursor().selectedText()
    view.put("live", "生成中の追記 **Markdown**")
    assert view.textCursor().selectedText() == before
    view.copy()
    assert "一つ目" in QApplication.clipboard().text()
    assert "二つ目" in QApplication.clipboard().text()
    assert "\ufdd0" not in QApplication.clipboard().text()
    assert "\ufdd1" not in QApplication.clipboard().text()


def test_table_and_nested_lists_remain_structural(qtbot):
    view = MessageView()
    qtbot.addWidget(view)
    source = "| 項目 | 状態 |\n|---|---|\n| 入力 | 完了 |\n\n- 親\n  - 子\n\n**末尾**"
    view.put("table", source)
    cursor = view.document().find("入力")
    assert cursor.currentTable() is not None
    assert view.document().find("子").block().textList().format().indent() == 2
    frame = view.messages["table"].frame
    assert frame.lastPosition() - frame.firstPosition() == units(view.messages["table"].rendered)
    view.setTextCursor(view.document().find("末尾"))
    view.put("table", source + " 追記")
    assert view.textCursor().selectedText() == "末尾"


def test_left_rule_tracks_scroll_and_resize(qtbot):
    view = MessageView()
    qtbot.addWidget(view)
    view.resize(640, 300)
    view.show()
    view.put("live", "## 見出し\n\n" + "本文の折り返しを確認する。" * 200)
    view.verticalScrollBar().setValue(0)
    before = view.line_rects()[0][1]
    view.verticalScrollBar().setValue(100)
    moved = view.line_rects()[0][1]
    assert moved.top() == before.top() - 100
    height = moved.height()
    view.resize(400, 300)
    qtbot.wait(10)
    assert view.line_rects()[0][1].height() > height
    view.grab()
    assert view.painted_lines


def test_coalesced_completion_and_stale_generation(qtbot):
    view = MessageView()
    qtbot.addWidget(view)
    old_generation = view.generation
    view.enqueue("live", "未確定")
    view.finish("live", "確定 **本文**")
    qtbot.wait(80)
    assert "未確定" not in view.toPlainText()
    assert "確定 本文" in view.toPlainText()
    view.enqueue("late", "切替前")
    view.reset()
    view.enqueue("late", "遅着", generation=old_generation)
    qtbot.wait(80)
    assert not view.toPlainText()
    assert not view.pending


def test_completed_item_drops_late_delta_without_losing_correction(qtbot):
    view = MessageView()
    qtbot.addWidget(view)
    view.enqueue("live", "差分")
    view.finish("live", "確定本文")
    view.enqueue("live", "遅着")
    qtbot.wait(70)
    assert view.messages["live"].source == "確定本文"
    assert view.messages["live"].blocks is None


def test_tool_plain_text_and_unknown_phase_do_not_invent_semantics(qtbot):
    view = MessageView()
    qtbot.addWidget(view)
    view.put("tool", "# cmd\n**output**", "activity", markdown=False)
    assert "# cmd" in view.toPlainText()
    assert "**output**" in view.toPlainText()
    view.put("unknown", "phase未提供", "unknown")
    assert not view.line_rects()
    assert view.loadResource(2, None) is None


@pytest.mark.parametrize(
    "source",
    [
        "# 見出し\n\n本文 **太字** と *斜体*\n\n### 小見出し\n続き 😀",
        "- 親\n  - 子\n  - 二つ目\n- 次の親\n\n最後",
        "[参照][ref]\n\n> 引用\n\n[ref]: https://example.com",
        "```python\nprint('hello')\n```\n\n普通の本文",
        "| 項目 | 状態 |\n|---|---|\n| 入力 | 完了 |\n\n本文",
    ],
)
def test_streamed_final_matches_fresh_native_parse(qtbot, source):
    view, fresh = MessageView(), MessageView()
    qtbot.addWidget(view)
    qtbot.addWidget(fresh)
    for end in range(1, len(source), 3):
        view.put("live", source[:end], "commentary")
    view.finish("live", source)
    fresh.put("live", source)
    assert view.toPlainText() == fresh.toPlainText()

    # Compare the actual display document, not the parsed cache.
    def semantic_blocks(doc):
        result = []
        for position, (text, fmt, listing, fragments) in blocks(doc):
            properties = fmt.properties()
            # Qt assigns a new object id when importing a native list. Compare
            # list formatting and item number, not a document-local allocation.
            properties.pop(QTextFormat.Property.ObjectIndex, None)
            block = doc.findBlock(position)
            number = block.textList().itemNumber(block) if block.textList() else None
            result.append((text, properties, listing, number, fragments))
        return result

    assert semantic_blocks(view.document()) == semantic_blocks(fresh.document())


def test_history_prepend_and_fold_preserve_reading_and_selection(qtbot):
    view = MessageView()
    qtbot.addWidget(view)
    view.resize(640, 320)
    view.show()
    view.put("tool", "シェル · 完了", "activity", markdown=False)
    view.put("history", "\n\n".join(f"記録{n}を確認する。" for n in range(60)))
    view.setTextCursor(view.document().find("記録30"))
    view.verticalScrollBar().setValue(view.verticalScrollBar().maximum() // 2)
    before = visible_text(view)
    for expanded in (True, False):
        view.put(
            "tool",
            "シェル · 完了" + ("\n出力の詳細" * 30 if expanded else ""),
            "activity",
            markdown=False,
        )
        assert view.textCursor().selectedText() == "記録30"
        assert visible_text(view) == before
    view.put("older", "以前の履歴\n\n" * 20, prepend=True)
    assert view.textCursor().selectedText() == "記録30"
    assert visible_text(view) == before


def test_question_control_can_follow_frame_without_interrupting_selection(qtbot):
    view = MessageView()
    qtbot.addWidget(view)
    view.resize(640, 400)
    view.show()
    view.put("live", "生成中")
    view.put("question", "どちらへ進みますか？\n\n\n", "unknown", markdown=False)
    button = QPushButton("北へ進む", view.viewport())
    accepted = []
    button.clicked.connect(lambda: accepted.append(True))

    def place():
        frame = view.messages["question"].frame
        rect = view.document().documentLayout().frameBoundingRect(frame)
        button.move(20, int(rect.bottom() - view.verticalScrollBar().value() - 45))
        button.show()

    place()
    old_y = button.y()
    view.put("live", "生成中\n\n追加の段落")
    place()
    assert button.y() > old_y
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert accepted == [True]


def test_subtitle_lifecycle_and_latest_part(qtbot):
    subtitle = Subtitle()
    qtbot.addWidget(subtitle)
    subtitle.resize(420, subtitle.height())
    subtitle.show()
    height = subtitle.height()
    subtitle.start("turn-1")
    assert subtitle.text.toPlainText() == ""
    subtitle.delta("turn-1", "r1", 0, "前の要約")
    subtitle.boundary("turn-1", "r1", 1)
    assert subtitle.text.toPlainText() == "前の要約"
    subtitle.delta("turn-1", "r1", 1, "最新の段落。" * 50)
    subtitle.delta("turn-1", "r1", 0, "遅着")
    assert subtitle.text.toPlainText() == "最新の段落。" * 50
    assert subtitle.height() == height
    assert subtitle.text.verticalScrollBar().value() == subtitle.text.verticalScrollBar().maximum()
    subtitle.complete_item("turn-1", "r1", ["訂正した要約"])
    assert subtitle.text.toPlainText() == "訂正した要約"
    subtitle.finish("turn-1", "中断")
    subtitle.delta("turn-1", "r2", 0, "終了後の遅着")
    assert subtitle.text.toPlainText() == "訂正した要約"
    assert not subtitle.active
    subtitle.start("turn-2")
    subtitle.delta("turn-1", "r2", 0, "別turn")
    assert subtitle.text.toPlainText() == ""
