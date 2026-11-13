from dataclasses import dataclass
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from pytestqt.qtbot import QtBot

from gigas.app import video
from gigas.app.settings_worker import SettingsIO
from gigas.app.shell import ApplicationShell
from gigas.game_io.capture import CaptureService
from gigas.game_io.capture.models import Frame
from gigas.runtime.codex.models import (
    HumanRequest,
    PlanEvent,
    PlanStep,
    Question,
    QuestionOption,
    SessionStatus,
    TextEvent,
    TurnStarted,
)
from gigas.services.codex import ServiceEvent, ServiceResult
from gigas.storage.settings import Settings, SettingsError, load_settings, save_settings
from gigas.ui.main_window import MainWindow
from gigas.ui.request_card import AnswerSubmission
from tests.integration.peers.capture_peer import PeerBackend
from tests.integration.peers.controlled_capture import ControlledCapture
from tests.integration.test_video_gui import (
    video_shell as video_shell,  # noqa: PLC0414 -- expose the shared pytest fixture
)


@dataclass
class PreviewClocks:
    """Replay precise timer events beside a coarse Windows elapsed clock."""

    elapsed: float = 0.0

    def monotonic(self) -> float:
        """Return elapsed time quantized to the Windows 15.625 ms tick."""
        return 1000.0 + int(self.elapsed * 64) / 64

    def perf_counter(self) -> float:
        """Return the precise elapsed time of the same event."""
        return 1000.0 + self.elapsed


@pytest.mark.parametrize("fail", [False, True])
def test_menu_applies_only_successfully_saved_size_without_changing_capture(
    qtbot: QtBot, tmp_path: Path, fail: bool
) -> None:
    entered = Event()
    release = Event()

    def save(path: Path, settings: Settings) -> None:
        entered.set()
        if not release.wait(5):
            message = "save was not released"
            raise TimeoutError(message)
        if fail:
            message = "injected save failure"
            raise SettingsError(message)
        save_settings(path, settings)

    path = tmp_path / "settings.toml"
    save_settings(path, Settings(capture_device="path-b"))
    capture = ControlledCapture()
    shell = ApplicationShell(path, SettingsIO(load_settings, save), capture_service=capture)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        shell.apply_settings(shell.baseline)
        capture.stream()
        qtbot.waitUntil(lambda: not shell.video.applying)
        original = capture.snapshot().frame
        original_panes = shell.window.splitter.sizes()
        actions = shell.window.window_size_actions

        actions["hd"].trigger()

        qtbot.waitUntil(entered.is_set)
        assert actions["full_hd"].isChecked()
        assert shell.window.splitter.sizes() == original_panes
        assert all(not action.isEnabled() for action in actions.values())
        assert capture.snapshot().phase == "streaming"
        capture.stream(2)
        release.set()
        qtbot.waitUntil(lambda: shell.operation is None)
        expected = "full_hd" if fail else "hd"
        assert shell.baseline.window_size_preset == expected
        assert load_settings(path).window_size_preset == expected
        assert actions[expected].isChecked()
        assert (shell.window.splitter.sizes() == original_panes) == fail
        assert len(capture.connections) == 1
        assert original is not None
        current = capture.snapshot().frame
        assert current is not None
        assert (current.generation, current.sequence) == (original.generation, 2)
    finally:
        release.set()
        qtbot.waitUntil(lambda: shell.operation is None)
        shell.window.close()
        qtbot.waitUntil(lambda: not shell.window.isVisible())


def test_all_presets_share_settings_selection_and_fit_available_screen(
    video_shell: tuple[ApplicationShell, ControlledCapture], qtbot: QtBot
) -> None:
    shell, _capture = video_shell
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    actions = shell.window.window_size_actions
    assert set(actions) == {"hd", "full_hd", "wqhd", "four_k"}
    dialog.close_button.click()
    for key, action in actions.items():
        action.trigger()
        qtbot.waitUntil(lambda: shell.operation is None)
        assert shell.baseline.window_size_preset == key
        shell.open_settings()
        assert dialog.window_size_preset.currentData() == key
        dialog.close_button.click()
        assert [name for name, candidate in actions.items() if candidate.isChecked()] == [key]
        available = shell.window.screen().availableGeometry()
        assert available.contains(shell.window.frameGeometry())
        assert not shell.window.settings_button.icon().isNull()
        assert (
            shell.window.settings_button.geometry().right()
            < shell.window.menuBar().actionGeometry(shell.window.project_menu.menuAction()).left()
        )
        assert shell.window.video_device_actions[""].isCheckable()
        assert shell.window.connection.connect_action in shell.window.codex_menu.actions()
        assert shell.window.notifications.entry_action in shell.window.view_menu.actions()
        conversation = shell.window.connection
        preview = shell.window.video
        assert preview.rect().contains(preview.view.geometry())
        assert abs(preview.view.width() * 9 - preview.view.height() * 16) <= 16
        assert conversation.mapToGlobal(QPoint()).x() < preview.mapToGlobal(QPoint()).x()
        assert preview.width() > conversation.width()
        for pane, status in ((conversation, conversation.status), (preview, preview.status)):
            left = pane.mapToGlobal(QPoint()).x()
            center = status.mapToGlobal(status.rect().center()).x()
            assert left <= center < left + pane.width()
        for widget in (
            shell.window.menuBar(),
            shell.window.settings_button,
            shell.window.connection.send_button,
            shell.window.video.status,
            shell.window.connection.status,
        ):
            assert widget.isVisible()
            assert available.contains(QRect(widget.mapToGlobal(QPoint()), widget.size()))


def test_small_dialogue_keeps_controls_and_scrollable_text_reachable(qtbot: QtBot) -> None:
    window = MainWindow()
    window.allow_close = True
    qtbot.addWidget(window)
    panel = window.connection
    panel.activate(1)
    panel.handle_result(ServiceResult(1, "connect"))
    header = "長い質問の識別名" * 20
    option = "長い選択肢の本文" * 20
    for event in (
        TurnStarted("turn"),
        SessionStatus("waiting", "selected-model", "high"),
        TextEvent("response", "公開された判断本文\n" * 30),
        PlanEvent(
            "turn",
            (
                PlanStep("観測", "completed"),
                PlanStep("移動", "inProgress"),
                PlanStep("停止", "pending"),
            ),
        ),
        HumanRequest(
            "question",
            1,
            "thread",
            "turn",
            "question",
            questions=(Question("route", header, "経路を選ぶ", (QuestionOption(option, "説明"),)),),
        ),
        HumanRequest(
            "approval",
            2,
            "thread",
            "turn",
            "command",
            details="確認対象の詳細\n" * 20,
            decisions=("accept", "decline", "cancel"),
        ),
    ):
        panel.handle_event(ServiceEvent(1, event))
    window.resize(800, 600)
    with qtbot.waitActive(window):
        window.show()
    window.splitter.setSizes([400, 400])
    qtbot.waitUntil(panel.transcript.isVisible)
    assert (window.width(), window.height()) == (800, 600)
    assert header in panel.transcript.toPlainText()
    question = panel.request_views["question"]
    assert option in question.question_inputs["route"].choices
    assert panel.transcript.viewport().height() >= 5 * panel.transcript.fontMetrics().lineSpacing()
    plan = panel.plan_panel.view
    plan.setTextCursor(plan.document().find("停止"))
    plan.ensureCursorVisible()
    assert plan.viewport().rect().contains(plan.cursorRect())
    panel.prompt.setPlainText("北側から進む\n危険なら停止する")
    assert panel.prompt.viewport().height() >= 2 * panel.prompt.fontMetrics().lineSpacing()
    assert panel.prompt.verticalScrollBar().maximum() == 0
    assert panel.interrupt_button.accessibleName() == "中断"
    assert panel.composer.isAncestorOf(panel.interrupt_button)
    for widget in (
        panel.plan_panel,
        panel.transcript,
        panel.prompt,
        panel.interrupt_button,
        panel.send_button,
    ):
        assert widget.isVisible()
        assert window.rect().contains(QRect(widget.mapTo(window, QPoint()), widget.size()))
    assert abs(window.video.view.width() * 9 - window.video.view.height() * 16) <= 16
    assert window.video.mapTo(window, QPoint()).y() < panel.plan_panel.mapTo(window, QPoint()).y()
    bar = panel.transcript.verticalScrollBar()
    assert bar.maximum() > 0
    button = panel.requests["approval"].decision_buttons["decline"]
    button.setFocus(Qt.FocusReason.TabFocusReason)
    viewport = panel.transcript.viewport()
    qtbot.waitUntil(
        lambda: viewport.rect().contains(QRect(button.mapTo(viewport, QPoint()), button.size()))
    )
    answers: list[object] = []
    instructions: list[tuple[str, str]] = []
    stops: list[bool] = []
    panel.answer_requested.connect(answers.append)
    panel.steer_requested.connect(lambda text, turn: instructions.append((text, turn)))
    panel.interrupt_requested.connect(lambda: stops.append(True))
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(panel.send_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(panel.interrupt_button, Qt.MouseButton.LeftButton)
    assert answers == [AnswerSubmission("approval", decision="decline")]
    assert instructions == [("北側から進む\n危険なら停止する", "turn")]
    assert stops == [True]


def test_saved_display_fps_changes_without_reconnecting_and_dirty_edits_block_menu(
    video_shell: tuple[ApplicationShell, ControlledCapture],
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell, capture = video_shell
    clock = PreviewClocks()
    monkeypatch.setattr(video, "time", clock)
    shell.apply_settings(shell.baseline)
    capture.stream()
    qtbot.waitUntil(lambda: not shell.video.applying)
    shell.video.timer.stop()
    shell.open_settings()
    dialog = shell.dialog
    assert dialog is not None
    dialog.preview_fps.setCurrentIndex(dialog.preview_fps.findData(15))
    dialog.window_size_preset.setCurrentIndex(dialog.window_size_preset.findData("hd"))
    dialog.model.setText("unsaved")
    assert all(not action.isEnabled() for action in shell.window.window_size_actions.values())
    shell.window.window_size_actions["four_k"].trigger()
    assert dialog.values().model == "unsaved"
    assert shell.operation is None
    assert shell.baseline.window_size_preset == "full_hd"
    dialog.save_button.click()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.baseline.window_size_preset == "hd"
    delivered: list[int] = []
    real_set_frame = shell.window.video.view.set_frame

    def record(frame: Frame | None) -> None:
        if frame is not None:
            delivered.append(frame.sequence)
        real_set_frame(frame)

    monkeypatch.setattr(shell.window.video.view, "set_frame", record)
    clock.elapsed = 1
    capture.stream(1)
    shell.video.timer.timeout.emit()
    clock.elapsed = 1.034
    capture.stream(2)
    shell.video.timer.timeout.emit()
    assert delivered == [1]
    clock.elapsed = 1.068
    shell.video.timer.timeout.emit()
    assert delivered == [1, 2]
    assert len(capture.connections) == 1
    dialog.close_button.click()
    shell.window.window_size_actions["wqhd"].trigger()
    qtbot.waitUntil(lambda: shell.operation is None)
    assert shell.window.window_size_actions["wqhd"].isChecked()
    shell.open_settings()
    assert dialog.window_size_preset.currentData() == "wqhd"


def test_real_capture_readers_continue_through_display_resize(qtbot: QtBot, tmp_path: Path) -> None:
    marker = tmp_path / "release.txt"
    service = CaptureService(backend_factory=PeerBackend(str(marker)))
    observer = service.reader()
    path = tmp_path / "settings.toml"
    save_settings(path, Settings(capture_device="path-b"))
    shell = ApplicationShell(path, capture_service=service)
    shell.start()
    try:
        qtbot.waitUntil(lambda: shell.ready)
        shell.apply_settings(shell.baseline)
        qtbot.waitUntil(lambda: observer.snapshot().frame is not None, timeout=8000)
        held = observer.snapshot().frame
        assert held is not None
        qtbot.waitUntil(lambda: shell.window.video.source.text() == "Capture (2)")
        shell.open_settings()
        assert shell.dialog is not None
        assert shell.dialog.capture_choices.currentData() == "path-b"
        assert shell.dialog.capture_choices.findData("path-a") > 0
        assert shell.dialog.capture_choices.count() == 3
        pixels = held.bgr
        shell.window.window_size_actions["hd"].trigger()
        qtbot.waitUntil(lambda: shell.operation is None)
        qtbot.waitUntil(
            lambda: (
                (latest := shell.video.reader.snapshot().frame) is not None
                and latest.sequence > held.sequence
            )
        )
        current = shell.video.reader.snapshot().frame
        assert current is not None
        assert current.generation == held.generation
        assert (current.width, current.height) == (1920, 1080)
        assert held.bgr == pixels
        assert Path(str(marker) + ".opened").read_text(encoding="utf-8") == "opened\n"
    finally:
        shell.window.close()
        qtbot.waitUntil(lambda: not shell.window.isVisible(), timeout=8000)
    assert not service.is_alive()
    assert marker.read_text(encoding="utf-8") == "released\n"
