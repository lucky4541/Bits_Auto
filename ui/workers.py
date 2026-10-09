"""Background workers: engine calls run off the UI thread; progress arrives via signals."""
from __future__ import annotations

import threading
import traceback

from PySide6.QtCore import QObject, QThread, Signal


class Worker(QObject):
    progress = Signal(dict)
    finished = Signal(object)
    failed = Signal(str, str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self):
        try:
            res = self.fn(*self.args, progress=lambda **kw: self.progress.emit(kw), cancel=self.is_cancelled, **self.kwargs)
            self.finished.emit(res)
        except Exception as e:
            name = type(e).__name__
            if name == "Cancelled":
                self.failed.emit("Cancelled", "The operation was cancelled. Completed work is kept and can be resumed.")
            else:
                self.failed.emit(f"{name}: {e}", traceback.format_exc())


def start(owner, worker: Worker) -> QThread:
    """Run a worker in a QThread owned by `owner` (kept alive until finished)."""
    th = QThread(owner)
    worker.moveToThread(th)
    th.started.connect(worker.run)
    worker.finished.connect(th.quit)
    worker.failed.connect(lambda *_: th.quit())
    th.finished.connect(worker.deleteLater)
    th.finished.connect(th.deleteLater)
    owner._threads = getattr(owner, "_threads", [])
    owner._threads.append((th, worker))
    th.start()
    return th
