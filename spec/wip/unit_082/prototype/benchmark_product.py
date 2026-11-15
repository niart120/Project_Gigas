"""M3 performance replay using the actual product conversation widget.

Shares only the M1 traffic generator and measurements, never its renderer.
The explicit waits below belong to this standalone measurement process.
"""

import argparse
import json
import platform
import time
from pathlib import Path
from types import SimpleNamespace

from benchmark import Traffic, body, trial
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gigas.runtime.codex.history import HistoryEntry
from gigas.ui.conversation_transcript import ConversationTranscript
from gigas.ui.transcript import Transcript
from gigas.ui.video_panel import VideoView


class Measured(ConversationTranscript):
    def __init__(self):
        super().__init__()
        self.setFont(QFont("Meiryo", 10.5))
        self.messages = {}
        self.receipts = []
        self.costs = []
        self.latencies = []
        self.delivery = []
        self.max_pending = 0
        self.history_costs = []

    @property
    def pending(self):
        return self._parser._pending

    @property
    def busy(self):
        return self._parser._busy

    def put(self, key, source, phase="final_answer"):
        self.messages[key] = SimpleNamespace(source=source)
        self.update_entry(
            HistoryEntry(key, "assistant", source, phase=phase, completed=key != "live")
        )
        while not self.rendering_idle:
            QApplication.processEvents()
            time.sleep(0.001)

    def reset(self):
        self.clear()
        self.messages.clear()

    def receive(self, text, sent):
        self.delivery.append((time.perf_counter() - sent) * 1000)
        self.messages["live"].source += text
        self.receipts.append((len(self.messages["live"].source), sent))
        self.max_pending = max(self.max_pending, len(self.receipts))
        self.update_entry(
            HistoryEntry("live", "assistant", text, phase="commentary", completed=False),
            append=True,
        )

    def _parsed(self, result):
        start = time.perf_counter()
        super()._parsed(result)
        self.viewport().repaint()
        end = time.perf_counter()
        if result.job.key == id(self._items.get("live")):
            self.costs.append({"flush_paint_ms": (end - start) * 1000})
            done = [sent for length, sent in self.receipts if length <= len(result.job.source)]
            self.receipts = [
                (length, sent) for length, sent in self.receipts if length > len(result.job.source)
            ]
            self.latencies.extend((end - sent) * 1000 for sent in done)
        else:
            self.history_costs.append((end - start) * 1000)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--history", type=int, nargs="+", default=[100, 1000])
    parser.add_argument("--length", type=int, nargs="+", default=[10000, 100000])
    parser.add_argument("--duration", type=float, default=3)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--history-only", action="store_true")
    parser.add_argument("--default-font", action="store_true")
    parser.add_argument("--plain-first-paint", action="store_true")
    args = parser.parse_args()
    app = QApplication([])
    if args.plain_first_paint:
        view = Transcript()
        view.setFont(QFont("Meiryo", 10.5))
        view.resize(700, 600)
        view.show()
        app.processEvents()
        costs = []
        for index in range(2):
            started = time.perf_counter()
            view.update_entry(HistoryEntry(str(index), "assistant", body(250)))
            view.viewport().repaint()
            costs.append(round((time.perf_counter() - started) * 1000, 3))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps({"renderer": "existing plain Transcript", "first_two_paints_ms": costs}),
            encoding="utf-8",
        )
        view.close()
        return
    window = QWidget()
    window.setWindowTitle("GIGAS M3 · 製品表示の性能計測")
    window.resize(1100, 700)
    layout = QVBoxLayout(window)
    view = Measured()
    if args.default_font:
        view.setFont(app.font())
    content = QHBoxLayout()
    content.addWidget(view, 2)
    preview = VideoView()
    preview.setFixedSize(400, 225)
    content.addWidget(preview, 1)
    layout.addLayout(content, 1)
    controls = QHBoxLayout()
    editor, button = QLineEdit(), QPushButton("中断受付の計測")
    controls.addWidget(editor)
    controls.addWidget(button)
    layout.addLayout(controls)
    window.show()
    app.processEvents()
    traffic = Traffic()
    rows = []
    loads = []
    for history in args.history:
        view.reset()
        view.history_costs.clear()
        entries = tuple(
            HistoryEntry(
                f"history-{i}", "assistant", f"記録 {i}\n\n" + body(250), phase="final_answer"
            )
            for i in range(history)
        )
        gaps = []
        last = time.perf_counter()

        def heartbeat(gaps=gaps):
            nonlocal last
            now = time.perf_counter()
            gaps.append((now - last) * 1000)
            last = now

        timer = QTimer()
        timer.timeout.connect(heartbeat)
        timer.start(10)
        started = time.perf_counter()
        view.prepend_items(entries)
        while not view.rendering_idle:
            app.processEvents()
            time.sleep(0.001)
        heartbeat()
        timer.stop()
        loads.append(
            {
                "history_items": history,
                "load_ms": round((time.perf_counter() - started) * 1000, 3),
                "max_gui_gap_ms": round(max(gaps), 3),
                "first_item_paint_ms": round(view.history_costs[0], 3),
                "max_item_paint_ms": round(max(view.history_costs), 3),
            }
        )
        for length in [] if args.history_only else args.length * args.repeat:
            row = {
                "history_items": history,
                **trial(app, view, traffic, editor, button, preview, length, 50, args.duration),
            }
            retained = view.item_entry("live")
            assert retained is not None
            assert retained.text == view.messages["live"].source
            assert not view.receipts
            assert view.rendering_idle
            row["product_source_matches"] = True
            rows.append(row)
            print(json.dumps(row, ensure_ascii=True), flush=True)
    view.shutdown()
    while not view.shutdown_complete:
        app.processEvents()
        time.sleep(0.001)
    window.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "platform": platform.platform(),
                "renderer": "product ConversationTranscript",
                "font": view.font().toString(),
                "dpr": window.devicePixelRatioF(),
                "history_load": loads,
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
