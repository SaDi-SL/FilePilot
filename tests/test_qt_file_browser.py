import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QImage, QPainter, QPdfWriter
from app.ui.qt.document_preview import render_preview
from app.file_browser import BrowserListing
from app.ui.qt.application import create_application
from app.ui.qt.widgets.file_browser import FileBrowser


class QtFileBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_application([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "Reports").mkdir()
        (self.root / "Reports" / "report.txt").write_text("Exact report content", encoding="utf-8")
        (self.root / "notes.txt").write_text("notes", encoding="utf-8")
        self.browser = FileBrowser(SimpleNamespace())
        self.browser.configure(SimpleNamespace(folders=SimpleNamespace(organized_folder=self.root)))
        self.wait_for(lambda: self.browser.items.count() == 2)

    def tearDown(self):
        self.browser._pool.waitForDone(3000)
        self.browser.close()
        self.browser.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def wait_for(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(condition())

    def test_browse_filter_up_and_text_preview(self):
        self.assertIn("1 file", self.browser.items.item(0).text())
        self.browser.filter_input.setText("REPORT")
        self.assertEqual(self.browser.items.count(), 1)
        self.browser._activate(self.browser.items.item(0))
        self.wait_for(lambda: self.browser.items.count() == 1 and self.browser.refresh_button.isEnabled())
        self.assertEqual(self.browser.current_path, self.root / "Reports")
        self.browser.items.setCurrentRow(0)
        self.wait_for(lambda: "Exact report content" in self.browser.preview.toPlainText())
        self.assertIn("report.txt", self.browser.detail_title.text())
        self.browser._up()
        self.wait_for(lambda: self.browser.items.count() == 2)
        self.assertEqual(self.browser.current_path, self.root)
        self.assertFalse(self.browser.up_button.isEnabled())

    def test_stale_listing_and_preview_are_ignored(self):
        generation = self.browser._generation
        self.browser._loaded((generation - 1, BrowserListing(self.root, ()), None))
        self.assertEqual(self.browser.items.count(), 2)
        self.browser._preview_loaded((self.browser._preview_generation - 1, "Stale text", None))
        self.assertNotIn("Stale text", self.browser.preview.toPlainText())

    def test_long_filename_and_path_are_preserved_in_selectable_details(self):
        name = "Interview_" + "long_filename_" * 10 + ".txt"
        path = self.root / name
        path.write_text("content", encoding="utf-8")
        self.browser.load(self.root)
        self.wait_for(lambda: self.browser.items.count() == 3)
        self.browser.filter_input.setText("Interview")
        self.browser.items.setCurrentRow(0)
        self.assertEqual(self.browser.detail_title.toolTip(), name)
        self.assertTrue(self.browser.detail_info.toPlainText().startswith(str(path)))
        self.assertTrue(self.browser.detail_info.isReadOnly())

    def test_file_open_and_parent_use_local_urls_and_missing_is_reported(self):
        self.browser.filter_input.setText("notes")
        self.browser.items.setCurrentRow(0)
        with patch("app.ui.qt.widgets.file_browser.QDesktopServices.openUrl", return_value=True) as opener:
            self.browser.open_button.click()
            self.assertEqual(opener.call_args.args[0].toLocalFile(), str(self.root / "notes.txt"))
            self.browser.open_folder_button.click()
            self.assertEqual(opener.call_args.args[0].toLocalFile(), str(self.root))
            self.browser._pool.waitForDone(3000)
            (self.root / "notes.txt").unlink()
            self.browser.open_button.click()
            self.assertEqual(opener.call_count, 2)
            self.assertIn("no longer available", self.browser.status.text())

    def test_missing_directory_can_be_retried(self):
        self.browser.load(self.root / "missing")
        self.wait_for(lambda: "Could not read" in self.browser.status.text())
        self.assertTrue(self.browser.refresh_button.isEnabled())
        self.browser.home_button.click()
        self.wait_for(lambda: self.browser.items.count() == 2)

    def test_pdf_preview_page_navigation_and_zoom(self):
        path = self.root / "two-pages.pdf"
        writer = QPdfWriter(str(path))
        painter = QPainter(writer)
        painter.drawText(100, 300, "First page")
        writer.newPage()
        painter.drawText(100, 300, "Second page")
        painter.end()
        del writer
        self.browser.load(self.root)
        self.wait_for(lambda: self.browser.items.count() == 3)
        self.browser.filter_input.setText("two-pages")
        self.browser.items.setCurrentRow(0)
        self.wait_for(lambda: "Page 1 of 2" in self.browser.visual_status.text())
        self.assertFalse(self.browser.visual_image.pixmap().isNull())
        self.assertEqual(self.browser._rendered_image.pixelColor(0, 0).name(), "#ffffff")
        self.assertFalse(self.browser.visual_previous.isEnabled())
        self.browser.visual_next.click()
        self.wait_for(lambda: "Page 2 of 2" in self.browser.visual_status.text())
        self.assertTrue(self.browser.visual_previous.isEnabled())
        self.assertFalse(self.browser.visual_next.isEnabled())
        fitted_width = self.browser.visual_image.pixmap().width()
        self.browser.visual_zoom.setCurrentIndex(1)
        self.assertGreater(self.browser.visual_image.pixmap().width(), fitted_width)
        self.browser.visual_previous.click()
        self.wait_for(lambda: "Page 1 of 2" in self.browser.visual_status.text())

    def test_image_preview_and_switching_clears_stale_visuals(self):
        image = QImage(640, 400, QImage.Format.Format_RGB32)
        image.fill(0xff8844)
        image.save(str(self.root / "photo.png"))
        self.browser.load(self.root)
        self.wait_for(lambda: self.browser.items.count() == 3)
        self.browser.filter_input.setText("photo")
        self.browser.items.setCurrentRow(0)
        self.wait_for(lambda: "Image preview" in self.browser.visual_status.text())
        self.assertFalse(self.browser.visual_navigation.isVisibleTo(self.browser.visual_panel))
        generation = self.browser._preview_generation
        self.browser.filter_input.setText("notes")
        self.browser.items.setCurrentRow(0)
        self.browser._visual_loaded((generation, (image, 0, 1), None))
        self.assertTrue(self.browser.visual_panel.isHidden())
        self.assertIsNone(self.browser._rendered_image)

    def test_invalid_pdf_and_oversized_file_show_preview_errors(self):
        broken = self.root / "broken.pdf"
        broken.write_bytes(b"invalid pdf")
        with self.assertRaisesRegex(ValueError, "cannot be previewed"):
            render_preview(broken)
        with patch("pathlib.Path.stat", return_value=SimpleNamespace(st_size=51 * 1024 * 1024)):
            with self.assertRaisesRegex(ValueError, "50 MB"):
                render_preview(broken)
        self.browser.load(self.root)
        self.wait_for(lambda: self.browser.items.count() == 3)
        self.browser.filter_input.setText("broken")
        self.browser.items.setCurrentRow(0)
        self.wait_for(lambda: "cannot be previewed" in self.browser.visual_status.text())
        self.assertTrue(self.browser.open_button.isEnabled())
