from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QToolButton
from pytestqt.qtbot import QtBot

from gigas.runtime.codex.management_models import ProjectInfo, ProjectPage, ThreadInfo, ThreadPage
from gigas.ui.conversation_sidebar import ConversationHeading, ConversationSidebar, thread_title
from gigas.ui.main_window import MainWindow


def _thread(identifier: str, project_id: str | None, name: str = "会話") -> ThreadInfo:
    return ThreadInfo(
        identifier, name, "公開プレビュー", Path.cwd(), project_id, 1, 2, None, None, {}
    )


def _sidebar(qtbot: QtBot) -> ConversationSidebar:
    sidebar = ConversationSidebar()
    qtbot.addWidget(sidebar)
    sidebar.resize(240, 600)
    sidebar.show()
    sidebar.set_actions_enabled(True)
    sidebar.set_listing_enabled(True)
    return sidebar


def test_group_expansion_is_independent_from_new_and_disabled_conversation_actions(
    qtbot: QtBot,
) -> None:
    sidebar = _sidebar(qtbot)
    sidebar.set_projects(
        ProjectPage((ProjectInfo("p", "<b>Project & 名称</b>", Path.cwd()),), None)
    )
    group = sidebar._groups["p"]
    created: list[str | None] = []
    opened: list[str | None] = []
    sidebar.new_requested.connect(created.append)
    sidebar.group_opened.connect(opened.append)
    group.heading.click()
    qtbot.waitUntil(group.content.isVisible)
    assert group.heading.isChecked()
    assert group.content.isVisible()
    assert opened == ["p"]
    assert not created
    group.new_button.click()
    assert created == ["p"]
    assert opened == ["p"]
    assert group.heading.text() == "<b>Project & 名称</b>"
    assert group.heading.accessibleName() == group.heading.text()
    sidebar.set_actions_enabled(False)
    group.new_button.click()
    group.heading.click()
    assert not group.content.isVisible()
    group.heading.click()
    assert group.content.isVisible()
    assert created == ["p"]
    assert opened == ["p", "p"]
    assert not group.new_button.isEnabled()
    assert sidebar._groups[None].heading.isEnabled()


def test_local_loading_and_more_preserve_other_actions_and_opaque_cursor(qtbot: QtBot) -> None:
    sidebar = _sidebar(qtbot)
    sidebar.set_projects(ProjectPage((ProjectInfo("p", "Project", Path.cwd()),), "projects:/+?"))
    sidebar.set_threads(None, ThreadPage((_thread("free", None),), None, None))
    sidebar.set_threads("p", ThreadPage((_thread("one", "p"),), "threads:/+?", None))
    group = sidebar._groups["p"]
    group.heading.setChecked(True)
    sidebar.set_selection("p", "one")
    assert group.threads.item(0).isSelected()
    pages: list[tuple[str | None, str]] = []
    selected: list[str] = []
    sidebar.threads_more_requested.connect(lambda project, cursor: pages.append((project, cursor)))
    sidebar.thread_requested.connect(selected.append)
    group.more_button.click()
    assert pages == [("p", "threads:/+?")]
    sidebar.set_threads_loading("p", True)
    assert not group.threads.isEnabled()
    assert not group.more_button.isEnabled()
    assert sidebar._groups[None].new_button.isEnabled()
    assert sidebar._groups[None].threads.isEnabled()
    assert group.heading.isEnabled()
    sidebar.set_threads("p", ThreadPage((_thread("two", "p"),), None, None), append=True)
    assert group.threads.count() == 2
    assert group.threads.item(0).isSelected()
    assert not group.more_button.isVisible()
    qtbot.mouseClick(
        group.threads.viewport(),
        Qt.MouseButton.LeftButton,
        pos=group.threads.visualItemRect(group.threads.item(1)).center(),
    )
    assert selected == ["two"]
    # A request does not commit selection before the controller accepts it.
    assert group.threads.item(0).isSelected()
    sidebar.set_selection("p", "two")
    assert group.threads.item(1).isSelected()
    sidebar.set_threads_loading("p", True)
    sidebar.set_threads_loading("p", False)
    assert group.threads.count() == 2
    assert group.threads.item(1).isSelected()


def test_project_refresh_and_upsert_keep_expansion_selection_and_page_continuation(
    qtbot: QtBot,
) -> None:
    sidebar = _sidebar(qtbot)
    project = ProjectInfo("p", "Project", Path.cwd())
    other = ProjectInfo("other", "別Project", Path.cwd())
    sidebar.set_projects(ProjectPage((project, other), "next-projects"))
    sidebar.set_threads("p", ThreadPage((_thread("one", "p"),), None, None))
    group = sidebar._groups["p"]
    group.heading.setChecked(True)
    sidebar.set_selection("p", "one")
    sidebar.set_projects_loading(True)
    assert group.new_button.isEnabled()
    sidebar.set_projects(ProjectPage((other, ProjectInfo("p", "改名", Path.cwd())), "cursor-2"))
    first, second = sidebar._rows.itemAt(1), sidebar._rows.itemAt(2)
    assert first is not None
    assert second is not None
    assert first.widget() is sidebar._groups["other"]
    assert second.widget() is group
    assert sidebar._groups["p"] is group
    assert group.heading.isChecked()
    assert group.heading.text() == "改名"
    assert group.threads.item(0).isSelected()
    requested: list[str] = []
    sidebar.projects_more_requested.connect(requested.append)
    sidebar.upsert_project(ProjectInfo("picked", "選択したProject", Path.cwd()))
    sidebar.projects_more_button.click()
    assert requested == ["cursor-2"]
    sidebar.set_threads("picked", ThreadPage((), None, None))
    assert sidebar._groups["picked"].new_button.isEnabled()
    assert sidebar._groups["picked"].threads.count() == 0
    sidebar.set_threads("p", ThreadPage((_thread("one", "p", "再取得"),), None, None))
    assert group.threads.item(0).isSelected()
    assert group.threads.item(0).text() == "再取得"


def test_listing_can_continue_while_conversation_switching_is_disabled(qtbot: QtBot) -> None:
    sidebar = _sidebar(qtbot)
    sidebar.set_projects(ProjectPage((ProjectInfo("p", "Project", Path.cwd()),), "projects"))
    sidebar.set_threads("p", ThreadPage((_thread("one", "p"),), "threads", None))
    sidebar.set_listing_enabled(True)
    sidebar.set_actions_enabled(False)
    group = sidebar._groups["p"]
    assert not group.new_button.isEnabled()
    assert not group.threads.isEnabled()
    assert group.heading.isEnabled()
    assert group.more_button.isEnabled()
    assert sidebar.projects_more_button.isEnabled()
    sidebar.set_listing_enabled(False)
    assert not group.more_button.isEnabled()
    assert not sidebar.projects_more_button.isEnabled()
    assert group.heading.isEnabled()


def test_populated_sidebar_keeps_rows_and_groups_together_in_a_tall_window(qtbot: QtBot) -> None:
    window = MainWindow()
    window.allow_close = True
    qtbot.addWidget(window)
    window.resize(1921, 1111)
    with qtbot.waitActive(window):
        window.show()
    sidebar = window.sidebar
    sidebar.set_projects(
        ProjectPage(tuple(ProjectInfo(str(i), f"Project {i}", Path.cwd()) for i in range(3)), None)
    )
    sidebar.set_threads(
        None, ThreadPage((_thread("one", None), _thread("two", None)), "more", None)
    )
    sidebar.set_threads(None, ThreadPage((_thread("three", None),), None, None), append=True)
    qtbot.wait(1)
    group = sidebar._groups[None]
    first = group.threads.visualItemRect(group.threads.item(0))
    last = group.threads.visualItemRect(group.threads.item(2))
    heading_bottom = group.heading.mapToGlobal(group.heading.rect().bottomLeft()).y()
    rows_top = group.threads.viewport().mapToGlobal(first.topLeft()).y()
    rows_bottom = group.threads.viewport().mapToGlobal(last.bottomLeft()).y()
    next_heading_top = sidebar._groups["0"].heading.mapToGlobal(QPoint()).y()
    permitted_gap = group.heading.fontMetrics().lineSpacing()
    assert 0 <= rows_top - heading_bottom <= permitted_gap
    assert 0 <= next_heading_top - rows_bottom <= permitted_gap
    last_group = sidebar._groups["2"]
    assert last_group.mapTo(sidebar.viewport(), last_group.rect().bottomLeft()).y() < (
        sidebar.viewport().height() // 2
    )


def test_keyboard_navigation_requests_target_without_changing_confirmed_highlight(
    qtbot: QtBot,
) -> None:
    sidebar = _sidebar(qtbot)
    sidebar.set_threads(None, ThreadPage((_thread("one", None), _thread("two", None)), None, None))
    group = sidebar._groups[None]
    sidebar.set_selection(None, "one")
    group.threads.setCurrentRow(0)
    requested: list[str] = []
    sidebar.thread_requested.connect(requested.append)
    qtbot.keyClick(group.threads, Qt.Key.Key_Down)
    assert group.threads.currentRow() == 1
    assert group.threads.item(0).isSelected()
    assert not group.threads.item(1).isSelected()
    qtbot.keyClick(group.threads, Qt.Key.Key_Space)
    assert group.threads.item(0).isSelected()
    assert not requested
    qtbot.keyClick(group.threads, Qt.Key.Key_Return)
    assert requested == ["two"]
    assert group.threads.item(0).isSelected()
    sidebar.set_selection(None, "two")
    assert group.threads.item(1).isSelected()


def test_heading_keeps_plain_full_values_without_forcing_window_width(qtbot: QtBot) -> None:
    heading = ConversationHeading()
    qtbot.addWidget(heading)
    project = "<b>Project</b>" * 80
    title = "会話 & <img>" * 80
    heading.set_target(project, title, loading=True)
    heading.resize(210, heading.sizeHint().height())
    heading.show()
    assert heading.project.text() == project
    assert heading.title.text() == title
    assert heading.project.textFormat() == heading.title.textFormat() == Qt.TextFormat.PlainText
    assert heading.width() == 210
    assert heading.loading.isVisible()
    heading.set_target("未所属", "新しい会話")
    assert not heading.loading.isVisible()


def test_multiline_thread_names_and_previews_use_one_row_and_the_same_heading(qtbot: QtBot) -> None:
    sidebar = _sidebar(qtbot)
    preview = "  攻略の相談\r\n\t最初の条件\n次の条件\u2028調査\u2029メモ\n結果\n補足\n終了  "
    expected_preview = "攻略の相談 最初の条件 次の条件 調査 メモ 結果 補足 終了"
    choices = (
        (None, preview, expected_preview),
        ("  指定した\n会話名\t ", preview, "指定した 会話名"),
        (" \t", preview, expected_preview),
        (None, "\n\t ", "名前のない会話"),
    )
    threads = tuple(
        replace(_thread(str(index), None), name=name, preview=text)
        for index, (name, text, _expected) in enumerate(choices)
    )
    sidebar.set_threads(None, ThreadPage((_thread("single", None), *threads), None, None))
    qtbot.wait(1)
    rows = sidebar._groups[None].threads
    one_line_height = rows.visualItemRect(rows.item(0)).height()
    heading = ConversationHeading()
    qtbot.addWidget(heading)
    for index, (thread, (_name, original, expected)) in enumerate(
        zip(threads, choices, strict=True), 1
    ):
        row = rows.item(index)
        assert rows.visualItemRect(row).height() == one_line_height
        assert row.text() == expected
        assert row.data(Qt.ItemDataRole.AccessibleTextRole) == expected
        heading.set_target("Project", thread_title(thread))
        assert heading.title.text() == expected
        assert thread.preview == original


def test_window_sidebar_toggle_returns_width_and_keeps_existing_content_and_settings(
    qtbot: QtBot,
) -> None:
    window = MainWindow()
    window.allow_close = True
    qtbot.addWidget(window)
    window.resize(1100, 700)
    with qtbot.waitActive(window):
        window.show()
    assert window.splitter.count() == 2
    assert window.sidebar.isVisible()
    assert not window.sidebar._groups[None].new_button.isEnabled()
    assert not window.conversation_heading.isVisible()
    window.conversation_heading.set_target("Project", "会話")
    window.conversation_heading.show()
    window.connection.prompt.setPlainText("未送信")
    window.connection.transcript.update_item("message", "既存本文")
    qtbot.waitUntil(lambda: window.connection.transcript.rendering_idle)
    width = window.splitter.width()
    window.sidebar_button.click()
    qtbot.waitUntil(lambda: window.splitter.width() > width)
    assert not window.sidebar.isVisible()
    assert window.splitter.width() > width
    assert window.conversation_heading.isVisible()
    assert window.connection.prompt.toPlainText() == "未送信"
    assert "既存本文" in window.connection.transcript.toPlainText()
    window.sidebar_button.click()
    qtbot.waitUntil(
        lambda: window.sidebar.isVisible() and abs(window.splitter.width() - width) <= 1
    )
    assert abs(window.splitter.width() - width) <= 1
    assert window.settings_button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextOnly
    assert window.settings_button.defaultAction() is window.settings_action
    assert window.settings_action.shortcut().toString() == "Ctrl+,"
    selected: list[bool] = []
    window.settings_requested.connect(lambda: selected.append(True))
    window.settings_button.click()
    qtbot.keyClick(window, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
    assert selected == [True, True]
    assert (
        window.sidebar_button.mapToGlobal(QPoint()).x()
        < window.settings_button.mapToGlobal(QPoint()).x()
    )
    assert (
        window.settings_button.mapToGlobal(window.settings_button.rect().topRight()).x()
        < window.menuBar()
        .mapToGlobal(window.menuBar().actionGeometry(window.connection_menu.menuAction()).topLeft())
        .x()
    )
    assert isinstance(window.sidebar_button, QToolButton)
    assert not window.sidebar_button.icon().isNull()
    for pane, status in (
        (window.connection, window.connection.status),
        (window.video, window.video.status),
    ):
        left = pane.mapToGlobal(QPoint()).x()
        center = status.mapToGlobal(status.rect().center()).x()
        assert left <= center < left + pane.width()


def test_empty_placeholder_requires_completed_empty_page(qtbot: QtBot) -> None:
    sidebar = _sidebar(qtbot)
    sidebar.upsert_project(ProjectInfo("empty", "Empty", Path.cwd()))
    sidebar.expand_project("empty")
    group = sidebar._groups["empty"]
    qtbot.waitUntil(group.content.isVisible)
    assert not group.empty.isVisible()
    sidebar.set_threads_loading("empty", True)
    assert not group.empty.isVisible()
    sidebar.set_threads_loading("empty", False)
    assert not group.empty.isVisible()
    sidebar.set_threads("empty", ThreadPage((), None, None))
    assert group.empty.isVisible()
    assert group.threads.count() == 0
    sidebar.set_threads_loading("empty", True)
    assert not group.empty.isVisible()
    sidebar.set_threads("empty", ThreadPage((_thread("one", "empty"),), None, None))
    assert not group.empty.isVisible()
    assert group.threads.count() == 1
