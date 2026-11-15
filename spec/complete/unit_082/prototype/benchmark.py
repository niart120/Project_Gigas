"""Replay independent producer traffic through the native Qt event loop."""

import argparse
import ctypes
import json
import platform
import statistics
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from document import MessageView
from PySide6 import __version__
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from worker import AsyncMessageView

from gigas.game_io.capture.models import Frame
from gigas.ui.video_panel import VideoView

SAMPLE = (
    "## 現在地の確認\n\n北側の出口に向かって進む。**障害物**を確認し、"
    "短い入力で移動する 😀。現在の画面と直前の画面を比較する。\n\n"
    "- 進路を確認\n- 移動後に観測\n\n`notes/current.md` に現在地を記録する。\n\n"
)


def body(length):
    return (SAMPLE * (length // len(SAMPLE) + 1))[:length]


def summary(values):
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    return {
        "count": len(values),
        "median": round(statistics.median(values), 3),
        "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
        "max": round(max(values), 3),
    }


def memory_mib():
    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("faults", ctypes.c_ulong),
            ("peak", ctypes.c_size_t),
            ("working", ctypes.c_size_t),
            ("quota_peak", ctypes.c_size_t),
            ("quota", ctypes.c_size_t),
            ("nonpaged_peak", ctypes.c_size_t),
            ("nonpaged", ctypes.c_size_t),
            ("pagefile", ctypes.c_size_t),
            ("peak_pagefile", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    ctypes.windll.psapi.GetProcessMemoryInfo(
        ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    )
    return round(counters.working / 1024**2, 2)


class Traffic(QObject):
    delta = Signal(str, float)
    done = Signal()
    stop = Signal(float)


class MeasuredView(MessageView):
    def __init__(self):
        super().__init__()
        self.receipts = []
        self.costs = []
        self.latencies = []
        self.delivery = []
        self.max_pending = 0

    def receive(self, text, sent):
        self.delivery.append((time.perf_counter() - sent) * 1000)
        self.receipts.append(sent)
        self.max_pending = max(self.max_pending, len(self.receipts))
        self.enqueue("live", text, phase="commentary")

    def flush(self):
        if not self.pending:
            return
        start = time.perf_counter()
        super().flush()
        self.viewport().repaint()
        end = time.perf_counter()
        self.costs.append({**self.last_cost, "flush_paint_ms": (end - start) * 1000})
        self.latencies.extend((end - t) * 1000 for t in self.receipts)
        self.receipts.clear()


class MeasuredAsyncView(AsyncMessageView, MeasuredView):
    def __init__(self):
        super().__init__()
        self.inflight_receipts = []

    def flush(self):
        if not self.busy and self.pending:
            self.inflight_receipts, self.receipts = self.receipts, []
        super().flush()

    def apply_result(self, key, source, phase, doc, snapshot, elapsed):
        start = time.perf_counter()
        super().apply_result(key, source, phase, doc, snapshot, elapsed)
        self.viewport().repaint()
        end = time.perf_counter()
        self.costs.append({**self.last_cost, "flush_paint_ms": (end - start) * 1000})
        self.latencies.extend((end - t) * 1000 for t in self.inflight_receipts)
        self.inflight_receipts.clear()


def trial(app, view, traffic, editor, button, preview, length, interval, duration):
    view.interval_ms = interval
    view.put("live", body(length), "commentary")
    if isinstance(view, AsyncMessageView):
        view.latest.clear()
    app.processEvents()
    view.receipts.clear()
    view.costs.clear()
    view.latencies.clear()
    view.delivery.clear()
    view.max_pending = 0
    gaps, video_gaps, stop_latency = [], [], []
    last = [time.perf_counter(), time.perf_counter()]
    done = []
    received_count = [0]

    def tick():
        now = time.perf_counter()
        gaps.append((now - last[0]) * 1000)
        last[0] = now
        editor.setText(str(len(gaps)))

    def video_tick():
        now = time.perf_counter()
        video_gaps.append((now - last[1]) * 1000)
        last[1] = now
        preview.set_frame(
            Frame(
                pixels,
                1280,
                720,
                1,
                len(video_gaps),
                datetime.now(UTC),
                time.perf_counter_ns(),
            )
        )
        preview.repaint()

    def stop(sent):
        button.click()
        stop_latency.append((time.perf_counter() - sent) * 1000)

    def delivered(text, sent):
        received_count[0] += 1
        view.receive(text, sent)

    def finished():
        done.append(True)

    traffic.delta.connect(delivered)
    traffic.stop.connect(stop)
    traffic.done.connect(finished)
    heartbeat, video = QTimer(), QTimer()
    heartbeat.setTimerType(Qt.TimerType.PreciseTimer)
    video.setTimerType(Qt.TimerType.PreciseTimer)
    heartbeat.timeout.connect(tick)
    video.timeout.connect(video_tick)
    heartbeat.start(10)
    video.start(33)
    chunk = " 更新を確認する😀。"
    pixels = bytes([48, 64, 80]) * (1280 * 720)
    regular_count = round(duration / 0.02)
    total_count = regular_count + 200
    memory_before = memory_mib()
    cpu_before, wall_before = time.process_time(), time.perf_counter()

    def produce():
        for i in range(regular_count):
            traffic.delta.emit(chunk, time.perf_counter())
            if i == regular_count // 2:
                for _ in range(200):
                    traffic.delta.emit(chunk, time.perf_counter())
                traffic.stop.emit(time.perf_counter())
            time.sleep(0.02)
        traffic.done.emit()

    producer = threading.Thread(target=produce)
    producer.start()
    while (
        not done or view.pending or getattr(view, "busy", False) or received_count[0] < total_count
    ):
        app.processEvents()
        time.sleep(0.001)
    producer.join()
    wall = time.perf_counter() - wall_before
    cpu = time.process_time() - cpu_before
    heartbeat.stop()
    video.stop()
    traffic.delta.disconnect(delivered)
    traffic.stop.disconnect(stop)
    traffic.done.disconnect(finished)
    assert view.messages["live"].source == body(length) + chunk * total_count
    return {
        "body_chars": length,
        "interval_ms": interval,
        "wall_s": round(wall, 3),
        "process_cpu_s": round(cpu, 3),
        "working_set_before_mib": memory_before,
        "working_set_after_mib": memory_mib(),
        "updates": len(view.costs),
        "pending_events_max": view.max_pending,
        "pending_events_end": len(view.receipts),
        "source_chars_end": len(view.messages["live"].source),
        "cost_ms": {key: summary([row[key] for row in view.costs]) for key in view.costs[0]},
        "receive_to_paint_ms": summary(view.latencies),
        "delivery_queue_ms": summary(view.delivery),
        "heartbeat_gap_ms": summary(gaps),
        "video_timer_gap_ms": summary(video_gaps),
        "stop_request_ms": summary(stop_latency),
        "exact_source": True,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--duration", type=float, default=3)
    parser.add_argument("--history", type=int, nargs="+", default=[100, 1000])
    parser.add_argument("--length", type=int, nargs="+", default=[10000, 100000])
    parser.add_argument("--interval", type=int, nargs="+", default=[16, 50, 100])
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    app = QApplication([])
    window = QWidget()
    window.resize(1100, 700)
    layout = QVBoxLayout(window)
    view = MeasuredAsyncView() if args.worker else MeasuredView()
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
    traffic = Traffic()
    rows = []
    warm = body(1000)
    view.put("warm", warm)
    app.processEvents()
    view.reset()
    for history in [100] if args.quick else args.history:
        view.reset()
        for i in range(history):
            view.put(f"history-{i}", f"記録 {i}\n\n" + warm[:250], "final_answer")
            view.freeze(f"history-{i}")
        for length in ([10000] if args.quick else args.length) * args.repeat:
            for interval in [50] if args.quick else args.interval:
                row = {
                    "history_items": history,
                    **trial(
                        app, view, traffic, editor, button, preview, length, interval, args.duration
                    ),
                }
                rows.append(row)
                print(
                    json.dumps(
                        {
                            key: row[key]
                            for key in (
                                "history_items",
                                "body_chars",
                                "interval_ms",
                                "updates",
                                "cost_ms",
                                "receive_to_paint_ms",
                                "heartbeat_gap_ms",
                            )
                        },
                        ensure_ascii=True,
                    ),
                    flush=True,
                )
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(
                    json.dumps(
                        {
                            "platform": platform.platform(),
                            "python": platform.python_version(),
                            "pyside": __version__,
                            "qt_platform": app.platformName(),
                            "worker": args.worker,
                            "rows": rows,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
    if args.worker:
        view.shutdown()
    window.close()


if __name__ == "__main__":
    main()
