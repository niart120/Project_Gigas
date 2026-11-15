"""M3 native product UI fixture; no server, recording or hardware is opened."""

import argparse
import json
from pathlib import Path

from demo import ANSWER
from PySide6.QtCore import QTimer
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from gigas.runtime.codex.history import item_key
from gigas.runtime.codex.models import (
    ActivityEvent,
    ConversationStatus,
    GoalState,
    PlanEvent,
    PlanStep,
    ReasoningEvent,
    SummaryPart,
    TextEvent,
)
from gigas.services.codex import ServiceEvent
from gigas.ui.main_window import MainWindow


class ProductDemo(MainWindow):
    def __init__(self):
        super().__init__()
        self.allow_close = True
        self.setWindowTitle("GIGAS · M3 製品表示の通知再生")
        self.connection.activate(1)
        self.connection._display.set_heading("表示検証", "北の出口へ")
        self.menu = self.menuBar().addMenu("検証")
        for title, callback, shortcut in (
            ("通常", lambda: self.state(False), "F1"),
            ("長いPlan・推論", lambda: self.state(True), "F2"),
            ("ストリーミング", self.replay, "F5"),
            ("中断", self.stop, "F6"),
        ):
            action = self.menu.addAction(title)
            action.triggered.connect(callback)
            action.setShortcut(shortcut)
        show = self.menu.addAction("推論要約を表示")
        show.setCheckable(True)
        show.setChecked(True)
        show.toggled.connect(self.connection.transcript.set_reasoning_visible)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.index = 0
        self.state(False)

    def emit_event(self, event):
        self.connection.handle_event(ServiceEvent(1, event))

    def state(self, long):
        self.timer.stop()
        self.emit_event(ConversationStatus("demo"))
        self.connection.transcript.clear()
        self.goal = GoalState("北の出口まで進み、到着したら停止する", "active")
        self.emit_event(ConversationStatus("demo", "turn", goal=self.goal))
        self.emit_event(
            TextEvent("user", "北の出口まで進んで、到着したら止まってください。", role="user")
        )
        self.emit_event(
            TextEvent(
                "progress",
                "北側の出口を確認しました。障害物を避けながら進みます。",
                replace=True,
                turn_id="turn",
                phase="commentary",
                completed=True,
            )
        )
        self.emit_event(
            ActivityEvent(
                "turn",
                "tool",
                "mcpToolCall",
                "gigas_demo/act_and_observe",
                "completed",
                "入力を送出し、移動後の画面を取得しました。",
                arguments={"steps": [{"left_stick": [0, -1], "duration": 0.4}]},
                completed=True,
            )
        )
        self.emit_event(
            ReasoningEvent(
                "turn",
                "reason",
                (
                    SummaryPart(
                        0,
                        "出口と障害物を確認する。" * (80 if long else 1)
                        + "北側の通路が見えた。次の移動は短い入力にする。",
                    ),
                ),
            )
        )
        steps = ["出口を確認する", "北へ移動する", "到着を確認する"]
        if long:
            steps = [f"区間{i + 1}の位置と進路を確認し、移動後に画面を比較する" for i in range(18)]
        self.emit_event(
            PlanEvent(
                "turn",
                tuple(
                    PlanStep(step, "inProgress" if i == 2 else "completed" if i < 2 else "pending")
                    for i, step in enumerate(steps)
                ),
            )
        )
        self.emit_event(
            TextEvent(
                "answer", ANSWER, replace=True, turn_id="turn", phase="final_answer", completed=True
            )
        )
        if not long:
            self.emit_event(ConversationStatus("demo", goal=self.goal))

    def replay(self):
        self.state(True)
        self.index = 0
        self.connection.transcript.update_item("stream", "")
        self.timer.start(50)

    def tick(self):
        self.index += 1
        self.emit_event(
            TextEvent(
                "stream", ANSWER[: self.index], replace=True, turn_id="turn", phase="commentary"
            )
        )
        self.emit_event(
            ReasoningEvent(
                "turn",
                "reason-live",
                (SummaryPart(0, "画面を確認し、短い入力で進む。" * max(1, self.index // 8)),),
            )
        )
        if self.index >= len(ANSWER):
            self.timer.stop()
            self.emit_event(
                TextEvent(
                    "stream",
                    ANSWER,
                    replace=True,
                    turn_id="turn",
                    phase="final_answer",
                    completed=True,
                )
            )
            self.emit_event(ConversationStatus("demo", goal=self.goal))

    def stop(self):
        self.timer.stop()
        self.emit_event(ConversationStatus("demo", stopping=True))

    def closeEvent(self, event):  # noqa: N802 -- Qt override
        self.timer.stop()
        self.connection.transcript.shutdown()
        if not self.connection.transcript.shutdown_complete:
            event.ignore()
            QTimer.singleShot(10, self.close)
        else:
            super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--stay", action="store_true")
    parser.add_argument("--light", action="store_true")
    args = parser.parse_args()
    app = QApplication([])
    if args.light:
        app.setStyle("Fusion")
        palette = app.style().standardPalette()
        for role, color in (
            (QPalette.ColorRole.Window, "#f5f5f5"),
            (QPalette.ColorRole.Base, "#ffffff"),
            (QPalette.ColorRole.Text, "#202020"),
            (QPalette.ColorRole.WindowText, "#202020"),
            (QPalette.ColorRole.Button, "#f5f5f5"),
            (QPalette.ColorRole.ButtonText, "#202020"),
            (QPalette.ColorRole.Link, "#245ca0"),
            (QPalette.ColorRole.Highlight, "#d8e8fa"),
            (QPalette.ColorRole.HighlightedText, "#202020"),
            (QPalette.ColorRole.AlternateBase, "#f5f5f5"),
            (QPalette.ColorRole.PlaceholderText, "#777777"),
        ):
            palette.setColor(role, color)
        app.setPalette(palette)
    window = ProductDemo()
    window.resize(1100, 760)
    window.splitter.setSizes([510, 360])
    window.show()

    def capture():
        args.capture.mkdir(parents=True, exist_ok=True)
        dimensions = []
        for name, long, sidebar, expanded in (
            ("short", False, True, False),
            ("long", True, True, False),
            ("sidebar-closed", True, False, False),
            ("expanded", True, False, True),
        ):
            window.sidebar_button.setChecked(sidebar)
            window.state(long)
            if expanded:
                window.connection.transcript.toggle_details(
                    item_key("demo", "turn", "tool", kind="activity")
                )
            while not window.connection.transcript.rendering_idle:
                app.processEvents()
            QTest.qWait(80)
            window.connection.transcript.verticalScrollBar().setValue(0)
            window.grab().save(str(args.capture / f"{name}.png"))
            dimensions.append(
                {
                    "state": name,
                    "window": [window.width(), window.height()],
                    "dpr": window.devicePixelRatioF(),
                    "video": [window.video.width(), window.video.height()],
                    "subtitle": [
                        window.connection.plan_panel.reasoning.width(),
                        window.connection.plan_panel.reasoning.height(),
                    ],
                }
            )
        window.resize(900, 640)
        window.sidebar_button.setChecked(True)
        QTest.qWait(80)
        window.grab().save(str(args.capture / "compact.png"))
        (args.capture / "dimensions.json").write_text(
            json.dumps(dimensions, indent=2), encoding="utf-8"
        )
        window.resize(1100, 760)
        window.state(False)
        if not args.stay:
            window.close()

    if args.capture:
        QTimer.singleShot(200, capture)
    app.exec()


if __name__ == "__main__":
    main()
