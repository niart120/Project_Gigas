"""Inspect one conversation's recorded input sequences and their observations."""

import json
import re
from datetime import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout

if TYPE_CHECKING:
    from pathlib import Path

    from PySide6.QtGui import QResizeEvent
    from PySide6.QtWidgets import QWidget


class PlayHistory(QDialog):
    """Browse a snapshot of the recorded operations without controlling gameplay."""

    def __init__(self, directory: "Path", parent: "QWidget | None" = None) -> None:
        """Read the selected record directory in batches on the Qt loop."""
        super().__init__(parent)
        self.setWindowTitle("プレイ履歴")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(900, 520)
        self._directory = directory
        self._rows: dict[str, QTreeWidgetItem] = {}
        self._original = QPixmap()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["操作・撮影時点", "状態"])
        self.tree.setColumnWidth(0, 230)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(120, 90)
        splitter = QSplitter()
        splitter.addWidget(self.tree)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([330, 548])
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)
        self.tree.currentItemChanged.connect(self._select)
        splitter.splitterMoved.connect(self._scale)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._read_batch)
        try:
            self._stream = (directory / "events.jsonl").open(encoding="utf-8")
        except FileNotFoundError:
            self.preview.setText("操作記録なし")
            return
        except OSError as error:
            self.preview.setText(f"操作記録を開けません。{error}")
            return
        self.finished.connect(self._timer.stop)
        self.finished.connect(lambda _: self._stream.close())
        self._timer.start(0)

    def _read_batch(self) -> None:
        try:
            for _ in range(128):
                line = self._stream.readline()
                if not line:
                    self._timer.stop()
                    self._stream.close()
                    if not self._rows:
                        self.preview.setText("操作記録なし")
                    return
                event = json.loads(line)
                if isinstance(event, dict):
                    self._entry(
                        event.get("type"), event.get("payload"), str(event.get("recorded_at", ""))
                    )
        except (ValueError, OSError) as error:
            self._timer.stop()
            self._stream.close()
            self.preview.setText(f"操作記録を読み込めません。{error}")

    def _entry(self, kind: object, payload: object, timestamp: str) -> None:
        if kind not in ("input.operation_started", "input.operation_finished") or not isinstance(
            payload, dict
        ):
            return
        identifier = payload.get("operation_id")
        if not isinstance(identifier, str):
            return
        row = self._rows.get(identifier)
        if row is None:
            try:
                local_time = datetime.fromisoformat(timestamp).astimezone()
                label = local_time.replace(tzinfo=None).isoformat(sep=" ", timespec="milliseconds")
            except ValueError:
                label = timestamp
            row = QTreeWidgetItem([label, ""])
            row.setToolTip(0, identifier)
            self.tree.addTopLevelItem(row)
            self._rows[identifier] = row
        if kind == "input.operation_started":
            steps = payload.get("sequence")
            if isinstance(steps, list):
                row.setToolTip(0, json.dumps(steps, ensure_ascii=False, indent=2))
                row.setText(1, f"{len(steps)} ステップ")
            return
        status = payload.get("result")
        row.setText(
            1,
            {"completed": "完了", "interrupted": "中止", "failed": "失敗"}.get(
                str(status), str(status)
            ),
        )
        row.setToolTip(1, str(payload.get("reason", "")))
        samples = payload.get("samples", [])
        for sample in samples if isinstance(samples, list) else ():
            if isinstance(sample, dict):
                label = f"{sample.get('offset_ms')} ms"
                if sample.get("step") is not None:
                    label += f" · 操作 {sample['step']}"
                self._frame(row, label, sample.get("observation_id"), sample.get("captured_at"))
        if payload.get("observation_id"):
            self._frame(row, "最終画像", payload["observation_id"])

    @staticmethod
    def _frame(row: QTreeWidgetItem, label: str, image: object, captured_at: object = None) -> None:
        child = QTreeWidgetItem(row, [label, "" if image else "未取得"])
        if isinstance(image, str) and re.fullmatch(r"[0-9]{8}T[0-9]{6}-[0-9]{3}Z", image):
            child.setData(0, Qt.ItemDataRole.UserRole, image)
            child.setToolTip(0, str(captured_at or image))

    def _select(self, item: QTreeWidgetItem | None) -> None:
        image = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        self._original = QPixmap()
        self.preview.clear()
        if image:
            self._original.load(str(self._directory / "observations" / f"{image}.png"))
            if self._original.isNull():
                self.preview.setText("記録画像を開けません。")
            else:
                self._scale()

    def _scale(self) -> None:
        if not self._original.isNull():
            self.preview.setPixmap(
                self._original.scaled(
                    self.preview.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event: "QResizeEvent") -> None:  # noqa: N802
        """Fit the selected image to the resized preview."""
        super().resizeEvent(event)
        self._scale()
