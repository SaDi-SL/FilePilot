import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    from PySide6.QtCore import QObject, Signal, QCoreApplication, QEvent
    from PySide6.QtTest import QTest
except ImportError as exc:
    raise unittest.SkipTest("Qt library tests require PySide6") from exc

from app.library_catalog import LibraryFile, LibrarySnapshot
from app.local_rag import RAGAnswer, RAGSource
from app.product_search import SearchRefreshResult
from app.ui.qt.application import create_application
from app.ui.qt.library_controller import LibraryController
from app.ui.qt.widgets.library_workspace import LibraryWorkspace


class ControllerStub(QObject):
    result = Signal(str, object)
    error = Signal(str, str)
    progress = Signal(object)
    busy_changed = Signal(bool)
    def __init__(self):
        super().__init__()
        self.requests = []
    def request(self, key, **payload): self.requests.append((key, payload))
    def pause(self, paused): pass
    def cancel(self): pass


class LibraryQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = create_application([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name).resolve() / "report.txt"
        self.path.write_text("report")
        self.controller = ControllerStub()
        self.widget = LibraryWorkspace(self.controller, SimpleNamespace())
        self.snapshot = LibrarySnapshot((), 1, 1, 0,
            (LibraryFile(self.path, "indexed", "2026-10-18T10:00", False),), {})

    def tearDown(self):
        self.widget.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.tmp.cleanup()

    def test_filtering_selection_and_explicit_summary_do_not_start_indexing(self):
        self.widget._result("status", self.snapshot)
        self.assertEqual(self.widget.files.topLevelItemCount(), 1)
        self.assertEqual(self.controller.requests, [])
        self.widget.files.topLevelItem(0).setSelected(True)
        self.assertTrue(self.widget.summary_button.isEnabled())
        self.widget._document(False)
        key, payload = self.controller.requests[-1]
        self.assertEqual(key, "document")
        self.assertEqual(payload["paths"], [self.path])
        self.assertTrue(self.widget._document_busy)
        self.widget._error("document", "No local model")
        self.widget.filter.setText("not-present")
        self.assertEqual(self.widget.files.topLevelItemCount(), 0)
        self.assertFalse(self.widget.summary_button.isEnabled())

    def test_sources_show_exact_text_and_busy_state_preserves_no_fake_completion(self):
        self.widget._busy_changed(True)
        self.assertFalse(self.widget.update_button.isEnabled())
        self.assertTrue(self.widget.stop_button.isEnabled())
        self.widget._busy_changed(False)
        source = RAGSource("S1", self.path, self.path.name, None, 0, 1.0, "Exact evidence")
        self.widget._result("document", RAGAnswer("Summary", "Evidence [S1]", (source,), "ollama", "answered"))
        self.assertEqual(self.widget.answer.toPlainText(), "Evidence [S1]")
        self.assertIn("Exact evidence", self.widget.excerpt.toPlainText())
        self.assertIn(str(self.path), self.widget.excerpt.toPlainText())

    def test_prepare_ai_shows_missing_model_and_restores_button(self):
        from app.local_ai_readiness import LocalAIReadiness
        self.widget.ai_button.click()
        self.assertEqual(self.controller.requests[-1][0], "local_ai")
        self.assertFalse(self.widget.ai_button.isEnabled())
        self.widget._result("local_ai", LocalAIReadiness(True, ("gemma4:e4b-it-qat",), "missing", False, "bge-m3", False))
        self.assertIn("not installed", self.widget.message.text())
        self.assertIn("gemma4:e4b-it-qat", self.widget.message.text())
        self.assertTrue(self.widget.ai_button.isEnabled())

    def test_compact_layout_does_not_force_a_wide_workspace(self):
        self.widget.resize(620, 900)
        self.widget.show()
        self.app.processEvents()
        self.assertLessEqual(self.widget.minimumSizeHint().width(), 620)
        self.assertTrue(self.widget.files.isColumnHidden(3))
        self.assertFalse(self.widget.auto_refresh.isChecked())

    def test_controller_runs_off_gui_thread_and_rejects_index_during_document_job(self):
        entered, release = threading.Event(), threading.Event()
        threads = []
        def document(*args, **kw):
            threads.append(threading.get_ident())
            entered.set(); release.wait(2)
            return "done"
        service = SimpleNamespace(summarize_documents=document)
        controller = LibraryController(service)
        results, errors = [], []
        controller.result.connect(lambda key, value: results.append((key, value)))
        controller.error.connect(lambda key, value: errors.append((key, value)))
        controller.request("document", paths=[self.path])
        self.assertTrue(entered.wait(1))
        controller.request("index")
        self.assertTrue(errors)
        release.set()
        for _ in range(100):
            if results: break
            QTest.qWait(10)
        self.assertNotEqual(threads[0], threading.get_ident())
        self.assertEqual(results, [("document", "done")])
        controller.close()
        controller.pool.waitForDone(1000)
        controller.deleteLater()

    def test_index_pause_stop_and_progress_use_dedicated_lane(self):
        entered, release = threading.Event(), threading.Event()
        def refresh(*, control, progress):
            from app.library_catalog import IndexProgress
            progress(IndexProgress("extracting", 1, None, "report.txt"))
            entered.set(); release.wait(2)
            return SearchRefreshResult(1, 1, 0, 0, 0, cancelled=control.cancelled.is_set())
        controller = LibraryController(SimpleNamespace(refresh_search_index=refresh))
        progress, result = [], []
        controller.progress.connect(progress.append)
        controller.result.connect(lambda key, value: result.append(value))
        controller.request("index")
        self.assertTrue(entered.wait(1))
        controller.pause(True)
        self.assertTrue(controller.control.paused.is_set())
        controller.cancel()
        release.set()
        for _ in range(100):
            if result: break
            QTest.qWait(10)
        self.assertTrue(progress)
        self.assertTrue(result[0].cancelled)
        self.assertIsNone(controller.control)
        controller.close(); controller.pool.waitForDone(1000); controller.deleteLater()

    def test_preview_summary_entry_uses_the_selected_path_without_reindexing(self):
        self.widget.summarize_path(self.path)
        self.assertEqual(self.controller.requests[0][0], "document")
        self.assertEqual(self.controller.requests[0][1]["paths"], [self.path])
