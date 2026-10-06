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
from app.ui.qt.application import create_application
from app.ui.qt.pages.my_files import MyFilesPage


class MyFilesBridgeStub(QObject):
    preview_changed = Signal(object)
    organize_started = Signal(str)
    organize_completed = Signal(object)
    safety_request_failed = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.preview_requests = []
        self.organize_requests = []

    def request_preview(self, source):
        self.preview_requests.append(source)

    def request_organize(self, source):
        self.organize_requests.append(source)


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
