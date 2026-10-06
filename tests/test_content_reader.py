import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import ai_document_analyzer
from app.content_reader import (
    extract_file_content,
    extract_file_content_result,
    extract_image_ocr_result,
    read_docx_text,
    read_pdf_text,
    safe_trim,
    trim_text,
)


class ContentReaderTests(unittest.TestCase):
    def test_trim_text_preserves_case_unless_requested(self):
        self.assertEqual(trim_text("  Hello World  "), "Hello World")
        self.assertEqual(
            trim_text("  Hello World  ", lowercase=True),
            "hello world",
        )
        self.assertEqual(safe_trim("  Hello World  "), "hello world")

    def test_plain_text_extraction_preserves_legacy_lowercase_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Example.TXT"
            path.write_text("FilePilot Search", encoding="utf-8")

            self.assertEqual(
                extract_file_content(path),
                "filepilot search",
            )
            self.assertEqual(
                extract_file_content(path, lowercase=False),
                "FilePilot Search",
            )

    def test_unsupported_file_returns_empty_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "archive.bin"
            path.write_bytes(b"binary")

            self.assertEqual(extract_file_content(path), "")

    def test_structured_result_distinguishes_unsupported_and_legacy_xls(self):
        unsupported = extract_file_content_result(Path("archive.bin"))
        legacy_xls = extract_file_content_result(Path("legacy.xls"))

        self.assertEqual(unsupported.status, "unsupported_format")
        self.assertEqual(unsupported.text, "")
        self.assertEqual(legacy_xls.status, "unsupported_format")
        self.assertIn(".xls", legacy_xls.detail or "")

    def test_structured_plain_text_failure_is_not_reported_as_empty(self):
        with patch("pathlib.Path.read_text", side_effect=PermissionError("blocked")):
            result = extract_file_content_result(Path("blocked.txt"))

        self.assertEqual(result.status, "extraction_failed")
        self.assertEqual(result.method, "plain_text")
        self.assertEqual(result.detail, "PermissionError")
        self.assertEqual(result.text, "")

    def test_malformed_pdf_does_not_fall_through_to_ocr(self):
        with (
            patch("app.content_reader.PdfReader", side_effect=ValueError("broken")),
            patch("app.content_reader.run_pdf_ocr") as ocr,
        ):
            result = extract_file_content_result(Path("broken.pdf"))

        self.assertEqual(result.status, "extraction_failed")
        self.assertEqual(result.method, "pdf_text")
        self.assertEqual(result.detail, "ValueError")
        self.assertEqual(result.text, "")
        ocr.assert_not_called()

    def test_structured_docx_failure_is_not_reported_as_empty(self):
        with patch("app.content_reader.Document", side_effect=OSError("broken")):
            result = extract_file_content_result(Path("broken.docx"))

        self.assertEqual(result.status, "extraction_failed")
        self.assertEqual(result.method, "docx")
        self.assertEqual(result.detail, "OSError")

    def test_structured_xlsx_failure_is_not_reported_as_empty(self):
        with patch.dict("sys.modules", {"openpyxl": None}):
            result = extract_file_content_result(Path("broken.xlsx"))

        self.assertEqual(result.status, "extraction_failed")
        self.assertEqual(result.method, "xlsx")
        self.assertIn(result.detail, {"ModuleNotFoundError", "ImportError"})

    def test_pdf_without_embedded_text_uses_ocr_fallback(self):
        result_type = type(
            "PDFOCRResult",
            (),
            {
                "text": "Scanned مرحبا",
                "status": "extracted",
                "detail": None,
            },
        )
        reader_result_type = type(
            "ReaderResult",
            (),
            {
                "text": "",
                "failed": False,
                "detail": None,
            },
        )
        with (
            patch(
                "app.content_reader._read_pdf_text_result",
                return_value=reader_result_type(),
            ),
            patch("app.content_reader.run_pdf_ocr", return_value=result_type()) as ocr,
        ):
            result = extract_file_content_result(
                Path("scan.pdf"),
                max_chars=123,
                max_pdf_pages=4,
                lowercase=False,
            )

        self.assertEqual(result.status, "extracted")
        self.assertEqual(result.method, "pdf_ocr")
        self.assertEqual(result.text, "Scanned مرحبا")
        ocr.assert_called_once_with(
            Path("scan.pdf"),
            max_pages=4,
            max_output_chars=123,
        )

    def test_image_without_ocr_runtime_reports_ocr_unavailable(self):
        result_type = type(
            "OCRResult",
            (),
            {
                "text": "",
                "status": "ocr_unavailable",
                "detail": "Bundled OCR runtime is not installed",
            },
        )
        with patch("app.content_reader.run_image_ocr", return_value=result_type()):
            result = extract_image_ocr_result(Path("scan.png"))

        self.assertEqual(result.status, "ocr_unavailable")
        self.assertEqual(result.method, "image_ocr")

    def test_image_with_available_ocr_but_no_text_reports_empty(self):
        result_type = type(
            "OCRResult",
            (),
            {
                "text": "",
                "status": "empty",
                "detail": "OCR found no searchable text",
            },
        )
        with patch("app.content_reader.run_image_ocr", return_value=result_type()):
            result = extract_image_ocr_result(Path("scan.png"))

        self.assertEqual(result.status, "empty")
        self.assertEqual(result.method, "image_ocr")

    def test_legacy_reader_limits_remain_default_contract(self):
        with patch("app.content_reader.PdfReader") as reader:
            page_type = type("Page", (), {"extract_text": lambda self: "Page"})
            reader.return_value.pages = [page_type(), page_type(), page_type()]
            self.assertEqual(read_pdf_text(Path("report.pdf")), "page\npage")

        with patch("app.content_reader.Document") as document:
            paragraph_type = type("Paragraph", (), {})
            paragraphs = []
            for index in range(35):
                paragraph = paragraph_type()
                paragraph.text = f"P{index}"
                paragraphs.append(paragraph)
            document.return_value.paragraphs = paragraphs
            result = read_docx_text(Path("report.docx"), max_chars=10000)
            self.assertIn("p29", result)
            self.assertNotIn("p30", result)

    def test_ai_analyzer_delegates_to_canonical_extractor(self):
        source = Path("C:/Inbox/report.pdf")
        with patch(
            "app.ai_document_analyzer.extract_file_content",
            return_value="Canonical Text",
        ) as extractor:
            result = ai_document_analyzer._extract_text(source, max_chars=123)

        self.assertEqual(result, "Canonical Text")
        extractor.assert_called_once_with(
            source,
            max_chars=123,
            lowercase=False,
            max_pdf_pages=5,
            max_docx_paragraphs=None,
        )

    def test_ai_analyzer_keeps_legacy_placeholder_when_no_text_is_available(self):
        source = Path("C:/Inbox/archive.bin")
        with patch(
            "app.ai_document_analyzer.extract_file_content",
            return_value="",
        ):
            result = ai_document_analyzer._extract_text(source)

        self.assertEqual(result, "[Cannot read content of .bin file]")


if __name__ == "__main__":
    unittest.main()
