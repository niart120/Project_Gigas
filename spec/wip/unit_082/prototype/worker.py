"""One in-flight native text parse; pending changes retain only the latest source."""

from time import perf_counter

from document import MessageView, blocks, rich_document
from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication


class Parser(QObject):
    ready = Signal(object)

    @Slot(object)
    def parse(self, job):
        generation, jobs, font = job
        results = []
        for key, source, phase, revision in jobs:
            start = perf_counter()
            doc = rich_document(source, phase, font)
            snapshot = blocks(doc)
            elapsed = (perf_counter() - start) * 1000
            # No images, resources, widgets or shared document are touched here.
            doc.moveToThread(QApplication.instance().thread())
            results.append((key, source, phase, revision, doc, snapshot, elapsed))
        self.ready.emit((generation, results))


class AsyncMessageView(MessageView):
    request = Signal(object)

    def __init__(self):
        super().__init__()
        self.thread = QThread(self)
        self.parser = Parser()
        self.parser.moveToThread(self.thread)
        self.request.connect(self.parser.parse)
        self.parser.ready.connect(self.accept)
        self.thread.finished.connect(self.parser.deleteLater)
        self.thread.start()
        self.busy = False
        self.latest = {}
        self.revisions = {}
        self.final_keys = set()
        self.applied_count = 0
        self.next_start = 0

    def enqueue(self, key, delta, *, generation=None, phase="unknown"):
        if (generation is not None and generation != self.generation) or key in self.completed:
            return
        source, phase = self.latest.get(
            key,
            (self.messages[key].source, self.messages[key].phase)
            if key in self.messages
            else ("", phase),
        )
        self.latest[key] = source + delta, phase
        self.pending[key] = self.latest[key]
        if not self.timer.isActive() and not self.busy:
            self.timer.start(self.interval_ms)

    def flush(self):
        self.timer.stop()
        if self.busy or not self.pending:
            return
        jobs = [
            (key, source, phase, self.revisions.get(key, 0))
            for key, (source, phase) in self.pending.items()
        ]
        self.pending.clear()
        self.busy = True
        self.next_start = perf_counter() + self.interval_ms / 1000
        self.request.emit((self.generation, jobs, QFont(self.font())))

    @Slot(object)
    def accept(self, result):
        self.busy = False
        generation, results = result
        if generation == self.generation:
            for key, source, phase, revision, doc, snapshot, elapsed in results:
                if revision != self.revisions.get(key, 0):
                    continue
                self.apply_result(key, source, phase, doc, snapshot, elapsed)
                if key in self.final_keys:
                    self.freeze(key)
                    self.latest.pop(key, None)
                    self.final_keys.discard(key)
        if self.pending:
            self.timer.start(max(0, round((self.next_start - perf_counter()) * 1000)))

    def apply_result(self, key, source, phase, doc, snapshot, elapsed):
        self.put(key, source, phase, prepared=(doc, snapshot))
        self.last_cost["worker_parse_ms"] = elapsed
        self.applied_count += 1

    def finish(self, key, source, phase="final_answer"):
        self.revisions[key] = self.revisions.get(key, 0) + 1
        self.completed.add(key)
        self.final_keys.add(key)
        self.latest[key] = source, phase
        self.pending[key] = source, phase
        self.flush()

    def reset(self):
        super().reset()
        self.latest.clear()
        self.revisions.clear()
        self.final_keys.clear()

    def shutdown(self):
        self.timer.stop()
        self.thread.quit()
        self.thread.wait()
