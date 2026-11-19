import json
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtGui import QImage

from gigas.ui.play_history import PlayHistory

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


def test_history_groups_samples_and_legacy_final_image(tmp_path: Path, qtbot: "QtBot") -> None:
    observations = tmp_path / "observations"
    observations.mkdir()
    first = "20260101T120000-000Z"
    final = "20260101T120001-000Z"
    for identifier, color in [(first, "#246cd0"), (final, "#118844")]:
        image = QImage(640, 360, QImage.Format.Format_RGB32)
        image.fill(color)
        assert image.save(str(observations / f"{identifier}.png"))
    steps = [{"kind": "press", "buttons": ["A"], "duration_ms": 800}]
    records = [
        (kind, {"operation_id": identifier, **fields})
        for identifier, frames in [
            ("old", {}),
            (
                "new",
                {
                    "samples": [
                        {"offset_ms": 300, "step": 1, "observation_id": first},
                        {"offset_ms": 600, "step": None, "observation_id": None},
                    ]
                },
            ),
        ]
        for kind, fields in [
            ("started", {"sequence": steps}),
            ("finished", {"result": "completed", "observation_id": final, **frames}),
        ]
    ]
    with (tmp_path / "events.jsonl").open("w", encoding="utf-8") as stream:
        for kind, payload in records:
            stream.write(
                json.dumps(
                    {
                        "type": f"input.operation_{kind}",
                        "recorded_at": "2026-01-01T12:00:00Z",
                        "payload": payload,
                    }
                )
                + "\n"
            )
    dialog = PlayHistory(tmp_path)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.tree.topLevelItemCount() == 2)
    legacy, current = (dialog.tree.topLevelItem(index) for index in range(2))
    assert current is not None
    assert legacy is not None
    assert legacy.childCount() == 1
    assert current.childCount() == 3
    captured, missing, last = (current.child(i) for i in range(3))
    assert captured is not None
    assert missing is not None
    assert last is not None
    current.setExpanded(True)
    dialog.tree.setCurrentItem(captured)
    assert dialog.preview.pixmap().toImage().pixelColor(0, 0).name() == "#246cd0"
    dialog.tree.setCurrentItem(missing)
    assert missing.text(1) == "未取得"
    assert dialog.preview.pixmap().isNull()
    dialog.tree.setCurrentItem(last)
    assert dialog.preview.pixmap().toImage().pixelColor(0, 0).name() == "#118844"
    dialog.close()
