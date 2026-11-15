"""Check the layout and streaming fixture together with its worker."""

from demo import ANSWER, Demo
from document import rich_document
from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont, QImage, QTextDocument


def test_long_right_pane_and_summary_setting(qtbot):
    window = Demo()
    qtbot.addWidget(window)
    try:
        window.resize(997, 701)
        window.show()
        qtbot.wait(20)
        height = window.subtitle.height()
        window.state(True)
        window.connection.plan_panel.goal_value.update_item("goal", "長い目標\n" * 40, kind="plan")
        qtbot.wait(20)
        assert window.subtitle.height() == height
        assert window.subtitle.isVisible()
        assert window.connection.plan_panel.view.verticalScrollBar().maximum() > 0
        before = window.connection.plan_panel.view.height()
        window.subtitle.hide()
        qtbot.wait(20)
        assert window.connection.plan_panel.view.height() > before
        window.subtitle.show()
        window.replay()
        qtbot.waitUntil(lambda: not window.replay_timer.isActive(), timeout=5000)
        qtbot.waitUntil(lambda: not window.view.busy and not window.view.pending, timeout=5000)
        assert window.view.messages["answer"].source == ANSWER
        assert window.view.messages["answer"].phase == "final_answer"
        assert not window.subtitle.active
    finally:
        window.replay_timer.stop()
        window.view.shutdown()


def test_worker_document_never_loads_local_image(tmp_path):
    image = QImage(2, 2, QImage.Format.Format_RGB32)
    image.fill(0)
    path = tmp_path / "image.png"
    assert image.save(str(path))
    url = QUrl.fromLocalFile(str(path))
    doc = rich_document(f"![画像]({url.toString()})", "final_answer", QFont("Meiryo", 10))
    assert doc.resource(QTextDocument.ResourceType.ImageResource, url) is None
