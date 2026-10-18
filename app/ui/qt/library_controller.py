"""Dedicated bounded worker lane for document jobs; lifecycle commands keep their own thread."""
import time
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from app.library_catalog import IndexControl
from app.product_support import check_updates, diagnostics


class _Signals(QObject):
    finished = Signal(object)
    progress = Signal(object)


class _Task(QRunnable):
    def __init__(self, key, sequence, action):
        super().__init__()
        self.key, self.sequence, self.action = key, sequence, action
        self.signals = _Signals()

    def run(self):
        try:
            result, error = self.action(), ""
        except Exception as exc:
            result, error = None, str(exc)
        self.signals.finished.emit((self.key, self.sequence, result, error))


class LibraryController(QObject):
    result = Signal(str, object)
    error = Signal(str, str)
    progress = Signal(object)
    busy_changed = Signal(bool)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self._sequences = {}
        self._active = set()
        self._closed = False
        self.control = None
        self._last_progress = 0.0

    def request(self, key, **payload):
        if self._closed:
            return
        if key in {"index", "document", "add", "remove", "local_ai"} and (self.control is not None or self._active.intersection({"index", "document", "add", "remove", "local_ai"})):
            self.error.emit(key, "Wait for the current library task to finish before starting another.")
            return
        actions = {
            "status": lambda: self.service.library_snapshot(),
            "local_ai": lambda: self.service.prepare_local_ai(),
            "updates": check_updates,
            "diagnostics": lambda: diagnostics(self.service),
            "add": lambda: self.service.add_library_folder(payload["path"]),
            "remove": lambda: self.service.remove_library_folder(payload["path"]),
            "document": lambda: (self.service.suggest_document_organization(payload["paths"],
                language=payload.get("language", "English")) if payload.get("suggest") else
                self.service.summarize_documents(payload["paths"],
                language=payload.get("language", "English"), compare=payload.get("compare", False))),
        }
        if key == "index":
            self.control = IndexControl()
            control = self.control
            self.busy_changed.emit(True)
            action = lambda: self.service.refresh_search_index(control=control, progress=self._publish_progress)
        else:
            action = actions.get(key)
            if action is None:
                self.error.emit(key, "Unknown library operation")
                return
        sequence = self._sequences.get(key, 0) + 1
        self._sequences[key] = sequence
        self._active.add(key)
        task = _Task(key, sequence, action)
        task.signals.finished.connect(self._finished)
        if key == "index":
            # Emit through a QObject signal; widget updates always happen on the GUI thread.
            self._progress_signal = task.signals.progress
            task.signals.progress.connect(self._forward_progress)
        self.pool.start(task)

    def _publish_progress(self, value):
        now = time.monotonic()
        if now - self._last_progress >= 0.1:
            self._last_progress = now
            self._progress_signal.emit(value)

    @Slot(object)
    def _forward_progress(self, value):
        if not self._closed:
            self.progress.emit(value)

    @Slot(object)
    def _finished(self, payload):
        key, sequence, result, error = payload
        if self._closed or self._sequences.get(key) != sequence:
            return
        self._active.discard(key)
        if key == "index":
            self.control = None
            self.busy_changed.emit(False)
        if error:
            self.error.emit(key, error)
        else:
            self.result.emit(key, result)

    def pause(self, paused):
        if self.control:
            self.control.paused.set() if paused else self.control.paused.clear()

    def cancel(self):
        if self.control:
            self.control.cancelled.set()

    def close(self):
        self._closed = True
        self.cancel()
        self.pool.clear()
