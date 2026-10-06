import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ocr_engine import OCRExecutionResult
from app.pdf_ocr import run_pdf_ocr


class _FakeImage:
    def save(self, path, format=None):
        Path(path).write_bytes(b"rendered")


class _FakeBitmap:
    def to_pil(self):
        return _FakeImage()

    def close(self):
        pass


class _FakePage:
    def render(self, *, scale):
        return _FakeBitmap()

    def close(self):
        pass


class _FakeDocument:
    def __init__(self, _path, pages=3):
        self.pages = [_FakePage() for _ in range(pages)]
        self.closed = False

    def __len__(self):
        return len(self.pages)

    def __getitem__(self, index):
        return self.pages[index]

    def close(self):
        self.closed = True


class PDFOCRTests(unittest.TestCase):
    def test_missing_renderer_is_explicit_and_non_mutating(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "scan.pdf"
            source.write_bytes(b"pdf")
            before = source.read_bytes()
            with patch("app.pdf_ocr._load_pdfium", return_value=None):
                result = run_pdf_ocr(source)

            self.assertEqual(source.read_bytes(), before)

        self.assertEqual(result.status, "ocr_unavailable")
        self.assertEqual(result.pages_attempted, 0)

    def test_success_is_page_bounded_and_temp_images_are_removed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "scan.pdf"
            source.write_bytes(b"pdf")
            fake_pdfium = type("Pdfium", (), {"PdfDocument": _FakeDocument})
            seen_paths = []

            def image_ocr(path, **_kwargs):
                seen_paths.append(Path(path))
                self.assertTrue(Path(path).is_file())
                return OCRExecutionResult(
                    text="Hello مرحبا",
                    status="extracted",
                    languages=("eng", "ara"),
                )

            with patch("app.pdf_ocr._load_pdfium", return_value=fake_pdfium):
                result = run_pdf_ocr(
                    source,
                    max_pages=2,
                    image_ocr=image_ocr,
                )

            self.assertEqual(result.status, "extracted")
            self.assertEqual(result.pages_attempted, 2)
            self.assertEqual(result.pages_extracted, 2)
            self.assertIn("مرحبا", result.text)
            self.assertEqual(len(seen_paths), 2)
            self.assertTrue(all(not path.exists() for path in seen_paths))

    def test_total_time_budget_stops_before_later_pages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "scan.pdf"
            source.write_bytes(b"pdf")
            fake_pdfium = type("Pdfium", (), {"PdfDocument": _FakeDocument})
            times = iter((0.0, 0.0, 0.0, 2.1))
            calls = []

            def image_ocr(path, **kwargs):
                calls.append((Path(path), kwargs["timeout_seconds"]))
                return OCRExecutionResult(
                    text="first page",
                    status="extracted",
                    languages=("eng", "ara"),
                )

            with patch("app.pdf_ocr._load_pdfium", return_value=fake_pdfium):
                result = run_pdf_ocr(
                    source,
                    max_pages=3,
                    timeout_seconds=20,
                    total_timeout_seconds=2,
                    image_ocr=image_ocr,
                    clock=lambda: next(times),
                )

        self.assertEqual(result.status, "extraction_failed")
        self.assertEqual(result.detail, "PDF OCR time budget exceeded")
        self.assertEqual(result.pages_attempted, 1)
        self.assertEqual(len(calls), 1)

    def test_page_timeout_is_reduced_to_remaining_total_budget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "scan.pdf"
            source.write_bytes(b"pdf")
            fake_pdfium = type("Pdfium", (), {"PdfDocument": _FakeDocument})
            times = iter((10.0, 10.0, 11.2, 11.3, 11.3))
            observed_timeouts = []

            def image_ocr(_path, **kwargs):
                observed_timeouts.append(kwargs["timeout_seconds"])
                return OCRExecutionResult(
                    text="bounded",
                    status="extracted",
                    languages=("eng", "ara"),
                )

            with patch("app.pdf_ocr._load_pdfium", return_value=fake_pdfium):
                result = run_pdf_ocr(
                    source,
                    max_pages=1,
                    timeout_seconds=20,
                    total_timeout_seconds=3,
                    image_ocr=image_ocr,
                    clock=lambda: next(times),
                )

        self.assertEqual(result.status, "extracted")
        self.assertEqual(observed_timeouts, [2])

    def test_ocr_unavailable_stops_without_processing_later_pages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "scan.pdf"
            source.write_bytes(b"pdf")
            fake_pdfium = type("Pdfium", (), {"PdfDocument": _FakeDocument})
            calls = []

            def image_ocr(path, **_kwargs):
                calls.append(path)
                return OCRExecutionResult(
                    text="",
                    status="ocr_unavailable",
                    languages=(),
                    detail="runtime unavailable",
                )

            with patch("app.pdf_ocr._load_pdfium", return_value=fake_pdfium):
                result = run_pdf_ocr(source, image_ocr=image_ocr)

        self.assertEqual(result.status, "ocr_unavailable")
        self.assertEqual(result.pages_attempted, 1)
        self.assertEqual(len(calls), 1)

    def test_page_render_failure_is_sanitized(self):
        class BrokenPage:
            def render(self, *, scale):
                raise RuntimeError("C:/Users/Secret/private-data")

            def close(self):
                pass

        class BrokenDocument(_FakeDocument):
            def __init__(self, _path):
                self.pages = [BrokenPage()]

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "private.pdf"
            source.write_bytes(b"pdf")
            fake_pdfium = type("Pdfium", (), {"PdfDocument": BrokenDocument})
            with patch("app.pdf_ocr._load_pdfium", return_value=fake_pdfium):
                result = run_pdf_ocr(source)

        self.assertEqual(result.status, "extraction_failed")
        self.assertEqual(result.detail, "PDF page rendering failed")
        self.assertNotIn("Secret", result.detail or "")

    def test_empty_ocr_is_distinct_from_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "blank.pdf"
            source.write_bytes(b"pdf")
            fake_pdfium = type("Pdfium", (), {"PdfDocument": _FakeDocument})

            def image_ocr(_path, **_kwargs):
                return OCRExecutionResult(
                    text="",
                    status="empty",
                    languages=("eng", "ara"),
                )

            with patch("app.pdf_ocr._load_pdfium", return_value=fake_pdfium):
                result = run_pdf_ocr(
                    source,
                    max_pages=2,
                    image_ocr=image_ocr,
                )

        self.assertEqual(result.status, "empty")
        self.assertEqual(result.pages_attempted, 2)
        self.assertEqual(result.pages_extracted, 0)


if __name__ == "__main__":
    unittest.main()
