import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
except ImportError as error:
    raise unittest.SkipTest(
        "PySide6 is required for Qt tests; install requirements-qt.txt"
    ) from error

from app.application_service import (
    OperationPreview,
    SafetyDataState,
)
from app.mover import DuplicateStatus, MoveResult, MoveStatus, PreviewStatus
from app.product_search import SearchRefreshResult
from app.search_index import SearchResult, SemanticSearchResult
from app.ui.qt.application import create_application
from app.ui.qt.pages.my_files import MyFilesPage


class MyFilesBridgeStub(QObject):
    preview_changed = Signal(object)
    organize_started = Signal(str)
    organize_completed = Signal(object)
    safety_request_failed = Signal(str, str)
    search_results_changed = Signal(str, object)
    semantic_search_results_changed = Signal(str, object)
    search_refresh_completed = Signal(object)
    search_request_failed = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.preview_requests = []
        self.organize_requests = []
        self.search_requests = []
        self.semantic_search_requests = []
        self.search_refresh_requests = 0

    def request_preview(self, source):
        self.preview_requests.append(source)

    def request_organize(self, source):
        self.organize_requests.append(source)

    def request_search(self, query, limit=25):
        self.search_requests.append((query, limit))

    def request_semantic_search(self, query, limit=25):
        self.semantic_search_requests.append((query, limit))

    def request_search_refresh(self):
        self.search_refresh_requests += 1


class QtMyFilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_application([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "report.txt"
        self.source.write_text("content", encoding="utf-8")
        self.bridge = MyFilesBridgeStub()
        self.page = MyFilesPage(self.bridge)

    def tearDown(self):
        self.page.close()
        self.app.processEvents()
        self.temp_dir.cleanup()

    def _ready_preview(self):
        return OperationPreview(
            state=SafetyDataState.AVAILABLE,
            status=PreviewStatus.READY,
            source=self.source,
            category="documents",
            proposed_destination=self.root / "organized" / "documents" / self.source.name,
            duplicate_status=DuplicateStatus.NOT_FOUND,
            safety_validated=True,
            execution_possible=True,
            classification_method="extension",
            message="Ready",
            warning="Preview is advisory",
        )

    def test_search_request_uses_local_semantic_product_data(self):
        self.page.search_input.setText("documents about train testing")
        self.page._request_search()

        self.assertEqual(
            self.bridge.semantic_search_requests,
            [("documents about train testing", 25)],
        )
        self.assertEqual(self.bridge.search_requests, [])
        self.assertFalse(self.page.search_button.isEnabled())

        result = SemanticSearchResult(
            path=self.root / "organized" / "reports" / "IR50.pdf",
            filename="IR50.pdf",
            extension=".pdf",
            category="reports",
            score=0.72,
        )
        self.bridge.semantic_search_results_changed.emit(
            "documents about train testing",
            (result,),
        )
        self.app.processEvents()

        self.assertTrue(self.page.search_button.isEnabled())
        self.assertEqual(self.page.search_results.count(), 1)
        self.assertTrue(self.page.search_results.isVisibleTo(self.page))
        self.assertIn("1 smart result", self.page.search_status.text())

    def test_semantic_failure_falls_back_to_exact_local_search(self):
        self.page.search_input.setText("traction")
        self.page._request_search()

        self.bridge.search_request_failed.emit(
            "semantic_search",
            "Local semantic search model is unavailable",
        )
        self.app.processEvents()

        self.assertEqual(self.bridge.search_requests, [("traction", 25)])
        self.assertFalse(self.page.search_button.isEnabled())

        result = SearchResult(
            path=self.root / "organized" / "reports" / "IR50.txt",
            filename="IR50.txt",
            extension=".txt",
            category="reports",
            snippet="Alstom [traction] verification",
            rank=-1.0,
        )
        self.bridge.search_results_changed.emit("traction", (result,))
        self.app.processEvents()

        self.assertTrue(self.page.search_button.isEnabled())
        self.assertEqual(self.page.search_results.count(), 1)

    def test_stale_search_result_does_not_replace_current_query(self):
        self.page.search_input.setText("new query")
        result = SearchResult(
            path=self.root / "old.txt",
            filename="old.txt",
            extension=".txt",
            category=None,
            snippet="old result",
            rank=-1.0,
        )

        self.bridge.search_results_changed.emit("old query", (result,))
        self.app.processEvents()

        self.assertEqual(self.page.search_results.count(), 0)

    def test_search_refresh_reports_real_index_counts(self):
        self.page._request_search_refresh()
        self.assertEqual(self.bridge.search_refresh_requests, 1)
        self.assertFalse(self.page.refresh_search_button.isEnabled())

        self.bridge.search_refresh_completed.emit(
            SearchRefreshResult(
                scanned=3,
                indexed=2,
                unchanged=1,
                removed=0,
                failed=0,
            )
        )
        self.app.processEvents()

        self.assertTrue(self.page.refresh_search_button.isEnabled())
        self.assertIn("2 indexed", self.page.search_status.text())
        self.assertIn("1 unchanged", self.page.search_status.text())

    def test_select_then_preview_enables_only_safe_sequence(self):
        self.page._select_source(str(self.source))

        self.assertTrue(self.page.preview_button.isEnabled())
        self.assertFalse(self.page.organize_button.isEnabled())

        self.page._request_preview()

        self.assertEqual(self.bridge.preview_requests, [str(self.source)])
        self.assertFalse(self.page.preview_button.isEnabled())
        self.assertFalse(self.page.organize_button.isEnabled())

        self.bridge.preview_changed.emit(self._ready_preview())
        self.app.processEvents()

        self.assertTrue(self.page.preview_button.isEnabled())
        self.assertTrue(self.page.organize_button.isEnabled())
        self.assertEqual(self.page.preview_badge.text(), "Ready")
        self.assertEqual(self.page.duplicate_value.text(), "No duplicate found")
        self.assertEqual(self.page.safety_value.text(), "Preview checks passed")

    def test_duplicate_preview_never_enables_organize(self):
        self.page._select_source(str(self.source))
        preview = OperationPreview(
            state=SafetyDataState.AVAILABLE,
            status=PreviewStatus.DUPLICATE,
            source=self.source,
            category="documents",
            proposed_destination=self.root / "organized" / "documents" / self.source.name,
            duplicate_status=DuplicateStatus.PROVEN,
            duplicate_of=self.root / "organized" / "documents" / "existing.txt",
            safety_validated=True,
            execution_possible=False,
            classification_method="extension",
            message="Duplicate",
        )

        self.bridge.preview_changed.emit(preview)
        self.app.processEvents()

        self.assertFalse(self.page.organize_button.isEnabled())
        self.assertEqual(self.page.preview_badge.text(), "Duplicate")
        self.assertIn("Verified duplicate", self.page.duplicate_value.text())

    def test_organize_success_updates_ui_and_disables_repeat(self):
        self.page._select_source(str(self.source))
        self.bridge.preview_changed.emit(self._ready_preview())
        self.app.processEvents()

        self.page._organize_file()
        self.assertEqual(self.bridge.organize_requests, [str(self.source)])
        self.assertFalse(self.page.organize_button.isEnabled())

        destination = self.root / "organized" / "documents" / self.source.name
        result = MoveResult(
            MoveStatus.MOVED,
            self.source,
            destination=destination,
            operation_id="operation-1",
        )
        self.bridge.organize_completed.emit(result)
        self.app.processEvents()

        self.assertEqual(self.page.preview_badge.text(), "Organized")
        self.assertEqual(self.page.safety_value.text(), "Execution completed safely")
        self.assertFalse(self.page.preview_button.isEnabled())
        self.assertFalse(self.page.organize_button.isEnabled())
        self.assertEqual(self.page.destination_value.text(), str(destination))

    def test_failed_organize_keeps_source_retryable_but_requires_new_preview(self):
        self.page._select_source(str(self.source))
        self.bridge.preview_changed.emit(self._ready_preview())
        self.app.processEvents()
        self.page._organize_file()

        result = MoveResult(
            MoveStatus.MOVE_FAILED,
            self.source,
            error="Stop automatic organization before organizing a selected file. Nothing changed.",
        )
        self.bridge.organize_completed.emit(result)
        self.app.processEvents()

        self.assertEqual(self.page.preview_badge.text(), "Not organized")
        self.assertIn("Stop automatic organization", self.page.preview_message.text())
        self.assertTrue(self.page.preview_button.isEnabled())
        self.assertFalse(self.page.organize_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
