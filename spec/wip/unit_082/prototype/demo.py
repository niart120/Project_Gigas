"""Native M1 visual fixture; no server, recording or hardware is started."""

import argparse
import json
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QApplication, QFrame
from subtitle import Subtitle
from worker import AsyncMessageView

from gigas.runtime.codex.models import PlanEvent, PlanStep
from gigas.ui.main_window import MainWindow

ANSWER = """## 北の出口に到着しました

出口の通過を確認し、**入力を停止**しました。

- 現在地：北側の出口
- 追加の操作：なし

記録は `notes/current.md` に残しました。

```text
location: north_exit
input: stopped
```

> 次の移動は、新しい指示を受けてから行います。

[攻略メモを開く](https://example.com/notes)
"""


class Demo(MainWindow):
    def __init__(self):
        super().__init__()
        self.allow_close = True
        self.setWindowTitle("GIGAS · 活動表示 M1 試作")
        self.sidebar_pane.setFixedWidth(180)
        self.connection._display.set_heading("未所属", "北の出口へ")
        self.view = AsyncMessageView()
        old = self.connection.transcript
        old.parentWidget().layout().replaceWidget(old, self.view)
        old.hide()
        self.subtitle = Subtitle()
        panel = self.connection.plan_panel
        panel.goal_value.setMaximumHeight(62)
        self.subtitle.setFixedHeight(120)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        panel.layout().addWidget(line)
        panel.layout().addWidget(self.subtitle)
        for label in (panel.goal_heading, panel.heading):
            label.setFont(QFont("Meiryo", 10.5, QFont.Weight.Bold))
        self.expanded = False
        self.view.anchorClicked.connect(self.link)
        menu = self.menuBar().addMenu("試作")
        for title, callback in (
            ("短いPlan", lambda: self.state(False)),
            ("長いPlan・推論", lambda: self.state(True)),
            ("活動の展開／収納", self.toggle_activity),
            ("ストリーミング再生", self.replay),
            ("再生を中断", self.stop),
        ):
            action = menu.addAction(title)
            action.triggered.connect(callback)
            if title == "ストリーミング再生":
                action.setShortcut("F5")
            elif title == "再生を中断":
                action.setShortcut("F6")
        summary_action = menu.addAction("推論要約を表示")
        summary_action.setCheckable(True)
        summary_action.setChecked(True)
        summary_action.toggled.connect(self.subtitle.setVisible)
        self.replay_timer = QTimer(self)
        self.replay_timer.timeout.connect(self.tick)
        self.replay_index = 0
        self.state(False)

    def state(self, long):
        self.replay_timer.stop()
        self.view.reset()
        self.view.put(
            "user", "北の出口まで進んで、到着したら止まってください。", "user", markdown=False
        )
        self.view.put(
            "progress", "北側の出口を確認しました。障害物を避けながら進みます。", "commentary"
        )
        self.activity()
        self.view.put("answer", ANSWER)
        panel = self.connection.plan_panel
        panel.goal_value.update_item("goal", "北の出口まで進み、到着したら停止する", kind="plan")
        panel.goal_status.setText("進行中" if long else "完了")
        names = ["出口を確認する", "北へ移動する", "到着を確認する"]
        if long:
            names = [f"区間{i + 1}の位置と進路を確認し、移動後に画面を比較する" for i in range(18)]
        panel.set_plan(
            PlanEvent(
                "turn-demo",
                tuple(
                    PlanStep(
                        name,
                        "completed"
                        if not long
                        else "inProgress"
                        if i == 2
                        else "completed"
                        if i < 2
                        else "pending",
                    )
                    for i, name in enumerate(names)
                ),
            )
        )
        panel.view.verticalScrollBar().setValue(0)
        self.subtitle.start("turn-demo")
        text = "出口を通過した画面を確認できた。ここで入力を止める。"
        if long:
            text = (
                "出口と障害物の位置を確認する。短く移動して画面を比較する。" * 16
                + "北側の通路が見えた。次の移動は短い入力にする。"
            )
        self.subtitle.delta("turn-demo", "reasoning-demo", 0, text)
        if not long:
            self.subtitle.finish("turn-demo")
        self.view.verticalScrollBar().setValue(0)

    def activity(self):
        marker = "閉じる" if self.expanded else "詳細"
        source = f"画面取得 · 完了　[ {marker} ](gigas-demo:activity)\n\n入力 · 完了"
        if self.expanded:
            source += (
                "\n\n入力：左スティック 上 0.4秒\n\n結果：送出完了\n\n"
                "```text\nobservation: screen-004\ninput: sent\n```"
            )
        self.view.put("tools", source, "activity")

    def toggle_activity(self):
        self.expanded = not self.expanded
        self.activity()

    def link(self, url):
        if url.scheme() == "gigas-demo":
            self.toggle_activity()
        # External navigation is deliberately not part of this fixture.

    def replay(self):
        self.state(True)
        self.view.put("answer", "", "unknown")
        self.replay_index = 0
        self.replay_timer.start(20)

    def tick(self):
        part = ANSWER[self.replay_index : self.replay_index + 3]
        self.replay_index += 3
        self.view.enqueue("answer", part)
        self.subtitle.delta(
            "turn-demo",
            "reasoning-live",
            0,
            "画面を確認する。" if self.replay_index % 9 == 0 else "",
        )
        if self.replay_index >= len(ANSWER):
            self.replay_timer.stop()
            self.view.finish("answer", ANSWER)
            self.subtitle.finish("turn-demo")

    def stop(self):
        self.replay_timer.stop()
        self.view.flush()
        self.subtitle.finish("turn-demo", "中断")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--stay", action="store_true")
    args = parser.parse_args()
    app = QApplication([])
    app.setStyle("Fusion")
    palette = app.style().standardPalette()
    palette.setColor(QPalette.ColorRole.Window, "#f5f5f5")
    palette.setColor(QPalette.ColorRole.Base, "#ffffff")
    palette.setColor(QPalette.ColorRole.Text, "#202020")
    palette.setColor(QPalette.ColorRole.WindowText, "#202020")
    palette.setColor(QPalette.ColorRole.Button, "#f5f5f5")
    palette.setColor(QPalette.ColorRole.ButtonText, "#202020")
    palette.setColor(QPalette.ColorRole.Link, "#245ca0")
    palette.setColor(QPalette.ColorRole.Highlight, "#d8e8fa")
    palette.setColor(QPalette.ColorRole.HighlightedText, "#202020")
    palette.setColor(QPalette.ColorRole.AlternateBase, "#f5f5f5")
    palette.setColor(QPalette.ColorRole.PlaceholderText, "#777777")
    app.setPalette(palette)
    app.setFont(QFont("Meiryo", 10.5))
    window = Demo()
    scale = app.primaryScreen().devicePixelRatio()
    window.resize(round(1496 / scale), round(1052 / scale))
    window.splitter.setSizes([700, 495])
    window.show()

    def capture():
        args.capture.mkdir(parents=True, exist_ok=True)
        dimensions = []
        for name, long, sidebar in (
            ("short", False, True),
            ("long", True, True),
            ("sidebar-closed", True, False),
        ):
            window.sidebar_button.setChecked(sidebar)
            window.state(long)
            app.processEvents()
            window.grab().save(str(args.capture / f"{name}.png"))
            dimensions.append(
                {
                    "state": name,
                    "window": [window.width(), window.height()],
                    "dpr": window.devicePixelRatioF(),
                    "video": [window.video.width(), window.video.height()],
                    "plan": [
                        window.connection.plan_panel.view.width(),
                        window.connection.plan_panel.view.height(),
                    ],
                    "subtitle": [window.subtitle.width(), window.subtitle.height()],
                }
            )
        window.sidebar_button.setChecked(True)
        window.state(False)
        app.processEvents()
        window.resize(900, 640)
        app.processEvents()
        window.grab().save(str(args.capture / "compact.png"))
        dimensions.append(
            {
                "state": "compact",
                "window": [window.width(), window.height()],
                "dpr": window.devicePixelRatioF(),
                "video": [window.video.width(), window.video.height()],
                "plan": [
                    window.connection.plan_panel.view.width(),
                    window.connection.plan_panel.view.height(),
                ],
                "subtitle": [window.subtitle.width(), window.subtitle.height()],
            }
        )
        (args.capture / "dimensions.json").write_text(
            json.dumps(dimensions, indent=2), encoding="utf-8"
        )
        window.resize(round(1496 / scale), round(1052 / scale))
        if not args.stay:
            window.close()

    if args.capture:
        QTimer.singleShot(500, capture)
    app.exec()
    window.view.shutdown()


if __name__ == "__main__":
    main()
