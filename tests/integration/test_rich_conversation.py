from threading import Event
from time import sleep
from typing import TYPE_CHECKING, Literal

import pytest
from PySide6.QtCore import QObject, QPoint, Qt, QUrl, Signal
from PySide6.QtGui import QFont, QTextDocument, QTextFormat
from PySide6.QtWidgets import QApplication

from gigas.runtime.codex.history import HistoryEntry, activity_entry
from gigas.runtime.codex.models import (
    ActivityEvent,
    ConversationStatus,
    ReasoningEvent,
    SummaryPart,
)
from gigas.services.codex import ServiceEvent
from gigas.ui._markdown import Parsed, ParseJob, block_snapshot
from gigas.ui.connection_panel import ConnectionPanel
from gigas.ui.conversation_transcript import ConversationTranscript
from gigas.ui.reasoning_panel import ReasoningPanel

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


class _ConversationSwitch(QObject):
    requested = Signal()


def _activity(
    number: int,
    status: Literal["completed", "failed", "declined", "inProgress"] = "completed",
    turn: str = "turn",
) -> HistoryEntry:
    return activity_entry(
        "thread",
        ActivityEvent(
            turn,
            str(number),
            "mcpToolCall",
            "gigas_test/act_and_observe",
            status,
            details=f"操作結果{number}",
            completed=status != "inProgress",
        ),
    )


def test_completed_activity_group_has_nested_details_and_keeps_failures_visible(
    qtbot: "QtBot",
) -> None:
    view = ConversationTranscript()
    qtbot.addWidget(view)
    first, second = _activity(1), _activity(2)
    view.update_entry(first)
    view.update_entry(second)
    view.update_entry(_activity(3, "failed"))
    view.update_entry(_activity(4, "inProgress"))
    assert "2件実行しました" in view.toPlainText()
    assert "失敗" in view.toPlainText()
    assert "実行中" in view.toPlainText()
    assert "操作結果1" not in view.toPlainText()
    view.anchorClicked.emit(QUrl("gigas-group:" + first.key))
    assert "操作結果1" not in view.toPlainText()
    view.toggle_details(second.key)
    assert "操作結果2" in view.toPlainText()
    view.anchorClicked.emit(QUrl("gigas-group:" + first.key))
    view.selectAll()
    view.copy()
    assert "操作結果2" not in QApplication.clipboard().text()
    view.shutdown()
    qtbot.waitUntil(lambda target=view: target.shutdown_complete)


def test_completed_summary_is_body_and_separates_activity_groups(qtbot: "QtBot") -> None:
    view = ConversationTranscript()
    qtbot.addWidget(view)
    view.update_entry(_activity(1))
    view.update_entry(HistoryEntry("reason", "reasoning", "北へ進む", completed=False))
    assert "北へ進む" not in view.toPlainText()
    view.update_entry(_activity(2))
    view.update_entry(HistoryEntry("reason", "reasoning", "北へ進む", completed=True))
    assert "北へ進む" in view.toPlainText()
    assert "2件実行しました" not in view.toPlainText()
    assert not view.document().find("北へ進む").charFormat().isAnchor()
    view.set_reasoning_visible(False)
    assert "北へ進む" not in view.toPlainText()
    view.set_reasoning_visible(True)
    assert "北へ進む" in view.toPlainText()
    view.shutdown()
    qtbot.waitUntil(lambda target=view: target.shutdown_complete)


def test_history_grouping_matches_live_and_does_not_cross_turns(qtbot: "QtBot") -> None:
    live, history = ConversationTranscript(), ConversationTranscript()
    entries = (_activity(1), _activity(2), _activity(3, turn="next"))
    for view in (live, history):
        qtbot.addWidget(view)
    for entry in entries:
        live.update_entry(entry)
    history.prepend_items(entries[1:])
    history.prepend_items(entries[:1])
    assert live.toPlainText() == history.toPlainText()
    assert "2件実行しました" in live.toPlainText()
    assert "3件実行しました" not in live.toPlainText()
    for view in (live, history):
        view.shutdown()
        qtbot.waitUntil(lambda target=view: target.shutdown_complete)


def test_group_updates_keep_expansion_and_status_corrections_split_it(qtbot: "QtBot") -> None:
    view = ConversationTranscript()
    qtbot.addWidget(view)
    view.resize(500, 400)
    view.show()
    for entry in (_activity(1), _activity(2), _activity(3, "inProgress")):
        view.update_entry(entry)
    view.anchorClicked.emit(QUrl("gigas-group:" + _activity(1).key))
    view.toggle_details(_activity(2).key)
    view.update_entry(_activity(3))
    assert "3件実行しました" in view.toPlainText()
    assert "操作結果2" in view.toPlainText()
    view.resize(300, 400)
    assert view.toPlainText().count("操作結果2") == 1
    view.update_entry(_activity(2, "failed"))
    assert "3件実行しました" not in view.toPlainText()
    assert "失敗" in view.toPlainText()
    assert "操作結果2" in view.toPlainText()
    view.clear()
    view.prepend_items((_activity(1), _activity(2)))
    assert "操作結果2" not in view.toPlainText()
    view.shutdown()
    qtbot.waitUntil(lambda: view.shutdown_complete)


def test_markdown_heading_phase_and_literal_tool_details(qtbot: "QtBot") -> None:
    view = ConversationTranscript()
    qtbot.addWidget(view)
    view.update_entry(
        HistoryEntry("a", "assistant", "# 見出し\n\n**太字** と *斜体*", phase="commentary")
    )
    qtbot.waitUntil(lambda: "見出し" in view.toPlainText())
    cursor = view.document().find("見出し")
    assert cursor.blockFormat().headingLevel() == 1
    assert abs(cursor.charFormat().fontPointSize() - view.font().pointSizeF() * 1.15) < 0.01
    assert view.document().find("太字").charFormat().fontWeight() == QFont.Weight.Bold
    assert view.document().find("斜体").charFormat().fontItalic()
    assert "Codex · 進捗" in view.toPlainText()
    view.update_entry(HistoryEntry("tool", "activity", "実行結果\n# literal **output**"))
    assert "**output**" not in view.toPlainText()
    view.toggle_details("tool")
    assert "# literal **output**" in view.toPlainText()
    view.shutdown()
    qtbot.waitUntil(lambda: view.shutdown_complete)


def test_pending_parse_cannot_restore_cleared_conversation(qtbot: "QtBot") -> None:
    view = ConversationTranscript()
    qtbot.addWidget(view)
    view.update_entry(HistoryEntry("a", "assistant", "旧会話" * 10000))
    view._parser.flush()
    view.clear()
    view.update_entry(
        HistoryEntry("a", "assistant", "新しい **会話**", phase="final_answer", completed=True)
    )
    qtbot.waitUntil(lambda: "新しい 会話" in view.toPlainText())
    assert "旧会話" not in view.toPlainText()
    assert "Codex · 回答" in view.toPlainText()
    view.shutdown()
    qtbot.waitUntil(lambda: view.shutdown_complete)


def test_history_delivery_yields_and_clear_discards_remaining_results(qtbot: "QtBot") -> None:
    view = ConversationTranscript()
    qtbot.addWidget(view)
    observed: list[str] = []
    switch = _ConversationSwitch()

    def switch_conversation() -> None:
        observed.append(view.toPlainText())
        view.clear()
        view.update_entry(HistoryEntry("new", "assistant", "新しい会話"))

    def slow_first_delivery(result: Parsed) -> None:
        assert result.document.thread() == view.thread()
        view._parser.ready.disconnect(slow_first_delivery)
        switch.requested.emit()
        sleep(0.012)

    switch.requested.connect(switch_conversation, Qt.ConnectionType.QueuedConnection)
    view._parser.ready.connect(slow_first_delivery, Qt.ConnectionType.DirectConnection)
    view.prepend_items(tuple(HistoryEntry(str(i), "assistant", f"旧履歴{i}") for i in range(5)))
    qtbot.waitUntil(lambda: bool(observed) and view.rendering_idle)
    assert "旧履歴0" in observed[0]
    assert "旧履歴4" not in observed[0]
    assert "新しい会話" in view.toPlainText()
    assert "旧履歴" not in view.toPlainText()
    view.shutdown()
    qtbot.waitUntil(lambda: view.shutdown_complete)


def test_shutdown_during_parse_returns_without_blocking_gui(
    qtbot: "QtBot", monkeypatch: pytest.MonkeyPatch
) -> None:
    view = ConversationTranscript()
    qtbot.addWidget(view)
    started, release = Event(), Event()
    parse = view._parser._parser.parse

    def held_parse(generation: int, jobs: list[ParseJob]) -> None:
        started.set()
        if release.wait(3):
            parse(generation, jobs)

    monkeypatch.setattr(view._parser._parser, "parse", held_parse)
    view.update_entry(HistoryEntry("a", "assistant", "終了後に表示しない本文"))
    view._parser.flush()
    try:
        qtbot.waitUntil(started.is_set)
        view.shutdown()
        assert not view.shutdown_complete
        qtbot.wait(20)
        assert not view.shutdown_complete
    finally:
        release.set()
    qtbot.waitUntil(lambda: view.shutdown_complete)
    assert "終了後に表示しない本文" not in view.toPlainText()


def test_empty_local_replacement_invalidates_in_flight_parse(qtbot: "QtBot") -> None:
    view = ConversationTranscript()
    qtbot.addWidget(view)
    view.update_item("a", "取り消す応答")
    view._parser.flush()
    view.update_item("a", "")
    qtbot.waitUntil(lambda: view.rendering_idle)
    assert "取り消す応答" not in view.toPlainText()
    view.shutdown()
    qtbot.waitUntil(lambda: view.shutdown_complete)


@pytest.mark.parametrize(
    "source",
    [
        "# 見出し\n\n本文 **強調** と *斜体* 😀",
        "- 親\n  - 子\n  - 次\n- 親2\n\n終わり",
        "[参照][ref]\n\n> 引用\n\n[ref]: https://example.com",
        "```python\nprint('hello')\n```\n\n本文",
        "| 項目 | 状態 |\n|---|---|\n| 入力 | 完了 |\n\n本文",
    ],
)
def test_streamed_markdown_matches_fresh_parse(qtbot: "QtBot", source: str) -> None:
    live, fresh = ConversationTranscript(), ConversationTranscript()
    for view in (live, fresh):
        qtbot.addWidget(view)

    def semantic_blocks(doc: QTextDocument) -> list[tuple[object, ...]]:
        result = []
        for position, (text, fmt, listing, fragments) in block_snapshot(doc):
            properties = fmt.properties()
            properties.pop(QTextFormat.Property.ObjectIndex, None)
            block = doc.findBlock(position)
            text_list = block.textList()
            number = text_list.itemNumber(block) if text_list is not None else None
            result.append((text, properties, listing, number, fragments))
        return result

    for end in range(3, len(source), 11):
        fresh.clear()
        for view in (live, fresh):
            view.update_entry(
                HistoryEntry("a", "assistant", source[:end], phase="commentary", completed=False)
            )
            view._parser.flush()
            qtbot.waitUntil(lambda target=view: target.rendering_idle)
        assert semantic_blocks(live.document()) == semantic_blocks(fresh.document())
    for view in (live, fresh):
        view.update_entry(
            HistoryEntry("a", "assistant", source, phase="final_answer", completed=True)
        )
        qtbot.waitUntil(lambda target=view: target.rendering_idle)
    assert live.toPlainText() == fresh.toPlainText()
    for view in (live, fresh):
        if "入力" in source:
            assert view.document().find("入力").currentTable() is not None
        if "子" in source:
            listing = view.document().find("子").block().textList()
            assert listing is not None
            assert listing.format().indent() == 2
    assert semantic_blocks(live.document()) == semantic_blocks(fresh.document())
    for view in (live, fresh):
        view.shutdown()
        qtbot.waitUntil(lambda target=view: target.shutdown_complete)


def test_selection_and_viewport_survive_correction_fold_and_prepend(qtbot: "QtBot") -> None:
    view = ConversationTranscript()
    qtbot.addWidget(view)
    view.resize(500, 300)
    view.show()
    view.update_entry(HistoryEntry("a", "assistant", "**選択する 😀 本文"))
    view.update_entry(HistoryEntry("tool", "activity", "シェル\n" + "実行結果\n" * 30))
    view.update_entry(HistoryEntry("b", "assistant", "\n\n".join(f"履歴 {i}" for i in range(60))))
    qtbot.waitUntil(lambda: view.rendering_idle)
    view.setTextCursor(view.document().find("選択する 😀 本文"))
    view.update_entry(
        HistoryEntry("a", "assistant", "**選択する 😀 本文**\n\n追記", completed=True)
    )
    qtbot.waitUntil(lambda: view.rendering_idle)
    assert view.textCursor().selectedText() == "選択する 😀 本文"
    view.setTextCursor(view.document().find("履歴 30"))
    view.ensureCursorVisible()
    visible = view.cursorForPosition(QPoint(0, 1))
    y = view.cursorRect(visible).top()
    view.toggle_details("tool")
    view.prepend_items((HistoryEntry("older", "assistant", "以前の記録\n\n" * 20),))
    qtbot.waitUntil(lambda: view.rendering_idle)
    assert abs(view.cursorRect(visible).top() - y) <= 1
    assert view.textCursor().selectedText() == "履歴 30"
    view.copy()
    assert QApplication.clipboard().text() == "履歴 30"
    view.shutdown()
    qtbot.waitUntil(lambda: view.shutdown_complete)


def test_subtitle_lifecycle_visibility_and_late_items(qtbot: "QtBot") -> None:
    panel = ConnectionPanel()
    qtbot.addWidget(panel)
    panel.activate(1)
    subtitle = panel.plan_panel.reasoning
    panel.handle_event(ServiceEvent(1, ConversationStatus("thread", "turn")))

    def send(item: str, parts: tuple[SummaryPart, ...], *, completed: bool = False) -> None:
        panel.handle_event(ServiceEvent(1, ReasoningEvent("turn", item, parts, completed)))

    send("a", (SummaryPart(0, "最初"),))
    qtbot.waitUntil(lambda: subtitle.text.toPlainText() == "最初")
    send("b", ())
    assert subtitle.text.toPlainText() == "最初"
    send("b", (SummaryPart(2, "長い要約。" * 100 + "末尾"),))
    qtbot.waitUntil(lambda: subtitle.text.toPlainText().endswith("末尾"))
    assert subtitle.text.verticalScrollBar().value() == subtitle.text.verticalScrollBar().maximum()
    send("a", (SummaryPart(0, "遅れた以前の区分"),), completed=True)
    assert subtitle.text.toPlainText().endswith("末尾")
    panel.transcript.set_reasoning_visible(False)
    assert subtitle.isHidden()
    panel.transcript.set_reasoning_visible(True)
    assert not subtitle.isHidden()
    panel.handle_event(ServiceEvent(1, ConversationStatus("thread", stopping=True)))
    assert subtitle.label.text() == "推論 · 中断"
    send("b", (SummaryPart(2, "遅着"),))
    assert subtitle.text.toPlainText().endswith("末尾")
    panel.handle_event(ServiceEvent(1, ConversationStatus("thread", "next")))
    assert subtitle.text.toPlainText() == ""


def test_links_are_explicit_and_closed_activity_has_no_copyable_details(qtbot: "QtBot") -> None:
    opened: list[QUrl] = []
    panel = ConnectionPanel(open_url=lambda url: opened.append(url) or True)
    qtbot.addWidget(panel)
    view = panel.transcript
    view.update_entry(HistoryEntry("a", "assistant", "[参照](https://example.com)"))
    view.update_entry(HistoryEntry("tool", "activity", "シェル\n公開された詳細"))
    qtbot.waitUntil(lambda: view.rendering_idle)
    assert not opened
    assert view.document().find("参照").charFormat().isAnchor()
    view.selectAll()
    view.copy()
    assert "公開された詳細" not in QApplication.clipboard().text()
    view.anchorClicked.emit(QUrl("gigas-detail:tool"))
    assert "公開された詳細" in view.toPlainText()
    assert not opened
    view.anchorClicked.emit(QUrl("https://example.com"))
    assert opened == [QUrl("https://example.com")]
    view.shutdown()
    qtbot.waitUntil(lambda: view.shutdown_complete)


def test_subtitle_reflow_keeps_latest_tail(qtbot: "QtBot") -> None:
    panel = ReasoningPanel()
    qtbot.addWidget(panel)
    panel.resize(500, panel.height())
    panel.show()
    panel.start("turn")
    panel.receive(ReasoningEvent("turn", "a", (SummaryPart(0, "進路を確認する。" * 100 + "末尾"),)))
    qtbot.waitUntil(lambda: panel.text.toPlainText().endswith("末尾"))
    height = panel.height()
    panel.resize(200, height)
    qtbot.wait(20)
    bar = panel.text.verticalScrollBar()
    assert bar.value() == bar.maximum()
    assert panel.height() == height


def test_empty_later_reasoning_does_not_erase_subtitle(qtbot: "QtBot") -> None:
    panel = ReasoningPanel()
    qtbot.addWidget(panel)
    panel.start("turn")
    panel.receive(ReasoningEvent("turn", "a", (SummaryPart(0, "公開要約"),), completed=True))
    panel.receive(ReasoningEvent("turn", "b", ()))
    panel.receive(ReasoningEvent("turn", "b", (), completed=True))
    panel.finish(interrupted=True)
    assert panel.text.toPlainText() == "公開要約"

    panel.start("next")
    assert not panel.text.toPlainText()
    panel.receive(ReasoningEvent("next", "c", (SummaryPart(0, "訂正前"),)))
    panel.receive(ReasoningEvent("next", "c", (), completed=True))
    panel.finish()
    assert not panel.text.toPlainText()
