import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtTest import QTest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from pypdf import PdfWriter
from app.ui.qt.application import create_application
from app.ui.qt.library_controller import LibraryController
from app.ui.qt.widgets.library_preview import LibraryPreview, read_library_preview
from app.ui.qt.widgets.library_workspace import LibraryWorkspace
from app.library_catalog import LibraryFile, LibrarySnapshot
from app.ocr_runtime import OCRRuntime
from tests.test_qt_library import ControllerStub


class LibraryPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_application([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.controller = ControllerStub()
        self.widget = LibraryWorkspace(self.controller, SimpleNamespace())

    def tearDown(self):
        self.widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.tmp.cleanup()

    def test_pdf_pages_are_rendered_without_changing_source(self):
        source = self.root / 'pages.pdf'
        writer = PdfWriter()
        writer.add_blank_page(width=400, height=600)
        writer.add_blank_page(width=600, height=400)
        writer.write(source)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        first, second = read_library_preview(source, 0), read_library_preview(source, 1)
        self.assertEqual(first['count'], 2)
        self.assertEqual(second['page'], 1)
        self.assertFalse(first['image'].isNull())
        self.assertNotEqual(first['image'].size(), second['image'].size())
        self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())

    def test_selection_has_inline_actions_and_ignores_old_preview(self):
        first, second = self.root / 'first.txt', self.root / 'second.txt'
        first.write_text('First original'); second.write_text('Second original')
        records = tuple(LibraryFile(p, 'indexed', '', False) for p in (first, second))
        self.widget._result('status', LibrarySnapshot((), 2, 2, 0, records, {}))
        self.widget.files.topLevelItem(0).setSelected(True)
        self.widget.files.topLevelItem(0).setSelected(False)
        self.widget.files.topLevelItem(1).setSelected(True)
        QTest.qWait(150)
        self.assertEqual([r for r in self.controller.requests if r[0] == 'preview'], [('preview', {'path': str(second), 'page': 0})])
        self.controller.result.emit('preview', read_library_preview(first))
        self.assertEqual(self.widget.preview.text.toPlainText(), '')
        self.controller.result.emit('preview', read_library_preview(second))
        self.assertEqual(self.widget.preview.text.toPlainText(), 'Second original')
        self.assertTrue(self.widget.summary_button.isEnabled())
        self.assertTrue(self.widget.preview.isAncestorOf(self.widget.summary_button))
        self.widget.resize(650, 1300); self.widget.show(); QTest.qWait(20)
        self.assertEqual(self.widget.document_body.orientation(), Qt.Orientation.Vertical)
        self.assertFalse(self.widget.summary_button.isHidden())

    def test_ocr_failure_offers_diagnostic_and_repair_instructions(self):
        source = self.root / 'scan.pdf'
        source.write_bytes(b'broken PDF')
        self.widget._result('status', LibrarySnapshot((), 1, 0, 0, (LibraryFile(source, 'ocr_unavailable', '', True),), {}))
        item = self.widget.files.topLevelItem(0)
        item.setSelected(True)
        self.assertFalse(self.widget.ocr_check_button.isHidden())
        self.assertFalse(self.widget.summary_button.isEnabled())
        self.assertEqual(item.text(2), 'Not ready')
        self.widget.ocr_check_button.click()
        self.assertEqual(self.controller.requests[-1][0], 'ocr_status')
        self.widget._result('ocr_status', OCRRuntime(False, None, None, 'unavailable', (), 'Bundled runtime missing'))
        self.assertIn('Setup', self.widget.detail.text())
        with self.assertRaisesRegex(ValueError, 'damaged|protected'):
            read_library_preview(source)
        self.assertEqual(source.read_bytes(), b'broken PDF')

    def test_empty_and_filtered_library_do_not_show_an_empty_preview_panel(self):
        self.widget._result('status', LibrarySnapshot((), 0, 0, 0, (), {}))
        self.assertTrue(self.widget.document_body.isHidden())
        source = self.root / 'note.txt'
        source.write_text('A note')
        self.widget._result('status', LibrarySnapshot((), 1, 1, 0, (LibraryFile(source, 'indexed', '', False),), {}))
        self.assertFalse(self.widget.document_body.isHidden())
        self.widget.filter.setText('no matching file')
        self.assertTrue(self.widget.document_body.isHidden())
        self.assertFalse(self.widget.empty_state.isHidden())

    def test_preview_runs_off_gui_thread_and_newer_request_wins(self):
        controller = LibraryController(SimpleNamespace())
        entered, release = threading.Event(), threading.Event()
        results, threads = [], []
        def render(path, page):
            threads.append(threading.get_ident())
            if path == 'old':
                entered.set(); release.wait(2)
            return {'path': path}
        controller.result.connect(lambda key, value: results.append(value))
        try:
            with patch('app.ui.qt.library_controller.read_library_preview', side_effect=render):
                controller.request('preview', path='old')
                self.assertTrue(entered.wait(1))
                controller.request('preview', path='new')
                for _ in range(100):
                    if results: break
                    QTest.qWait(10)
                release.set()
                controller.pool.waitForDone(1000)
                QTest.qWait(20)
            self.assertEqual(results, [{'path': 'new'}])
            self.assertTrue(all(t != threading.get_ident() for t in threads))
        finally:
            release.set(); controller.close(); controller.pool.waitForDone(1000); controller.deleteLater()
