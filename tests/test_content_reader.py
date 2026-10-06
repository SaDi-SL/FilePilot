import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import ai_document_analyzer
from app.content_reader import (
    extract_file_content,
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
