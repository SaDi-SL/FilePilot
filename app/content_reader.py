from __future__ import annotations

import logging
from pathlib import Path

from docx import Document
from pypdf import PdfReader

logger = logging.getLogger(__name__)

TEXT_BASED_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".log"
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}
EXCEL_EXTENSIONS = {".xlsx", ".xls"}


def trim_text(
    text: str,
    *,
    max_chars: int = 4000,
    lowercase: bool = False,
) -> str:
    trimmed = text[:max_chars].strip()
    return trimmed.lower() if lowercase else trimmed


def safe_trim(text: str, max_chars: int = 4000) -> str:
    """Compatibility helper for existing classification callers."""
    return trim_text(text, max_chars=max_chars, lowercase=True)


def read_plain_text(
    file_path: Path,
    max_chars: int = 4000,
    *,
    lowercase: bool = True,
) -> str:
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        return trim_text(content, max_chars=max_chars, lowercase=lowercase)
    except Exception:
        return ""


def read_pdf_text(
    file_path: Path,
    max_pages: int = 2,
    max_chars: int = 4000,
    *,
    lowercase: bool = True,
) -> str:
    try:
        reader = PdfReader(str(file_path))
        texts = []
        total_pages = min(len(reader.pages), max_pages)
        for index in range(total_pages):
            page_text = reader.pages[index].extract_text() or ""
            texts.append(page_text)
            if sum(len(text) for text in texts) >= max_chars:
                break
        return trim_text(
            "\n".join(texts),
            max_chars=max_chars,
            lowercase=lowercase,
        )
    except Exception:
        return ""


def read_docx_text(
    file_path: Path,
    max_paragraphs: int | None = 30,
    max_chars: int = 4000,
    *,
    lowercase: bool = True,
) -> str:
    try:
        document = Document(str(file_path))
        texts = []
        for index, paragraph in enumerate(document.paragraphs):
            if max_paragraphs is not None and index >= max_paragraphs:
                break
            if paragraph.text.strip():
                texts.append(paragraph.text)
            if sum(len(text) for text in texts) >= max_chars:
                break
        return trim_text(
            "\n".join(texts),
            max_chars=max_chars,
            lowercase=lowercase,
        )
    except Exception:
        return ""


def read_excel_text(
    file_path: Path,
    max_rows: int = 51,
    max_chars: int = 4000,
    *,
    lowercase: bool = False,
) -> str:
    try:
        import openpyxl

        workbook = openpyxl.load_workbook(
            str(file_path),
            read_only=True,
            data_only=True,
        )
        try:
            worksheet = workbook.active
            rows = []
            for index, row in enumerate(worksheet.iter_rows(values_only=True)):
                if index >= max_rows:
                    break
                row_text = " | ".join(str(cell) for cell in row if cell is not None)
                if row_text.strip():
                    rows.append(row_text)
                if sum(len(text) for text in rows) >= max_chars:
                    break
            return trim_text(
                "\n".join(rows),
                max_chars=max_chars,
                lowercase=lowercase,
            )
        finally:
            workbook.close()
    except Exception:
        return ""


def read_image_ocr(
    file_path: Path,
    max_chars: int = 4000,
    *,
    lowercase: bool = False,
) -> str:
    try:
        import pytesseract
        from PIL import Image

        with Image.open(str(file_path)) as image:
            text = pytesseract.image_to_string(image)
        return trim_text(text, max_chars=max_chars, lowercase=lowercase)
    except Exception as error:
        logger.debug("OCR extraction unavailable for %s: %s", file_path.name, error)
        return ""


def extract_file_content(
    file_path: Path,
    max_chars: int = 4000,
    *,
    lowercase: bool = True,
    max_pdf_pages: int = 2,
    max_docx_paragraphs: int | None = 30,
) -> str:
    """Extract local text without AI and without mutating the source file."""
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()

    if suffix in TEXT_BASED_EXTENSIONS:
        return read_plain_text(
            file_path,
            max_chars=max_chars,
            lowercase=lowercase,
        )

    if suffix == ".pdf":
        return read_pdf_text(
            file_path,
            max_pages=max_pdf_pages,
            max_chars=max_chars,
            lowercase=lowercase,
        )

    if suffix == ".docx":
        return read_docx_text(
            file_path,
            max_paragraphs=max_docx_paragraphs,
            max_chars=max_chars,
            lowercase=lowercase,
        )

    if suffix in EXCEL_EXTENSIONS:
        return read_excel_text(
            file_path,
            max_chars=max_chars,
            lowercase=lowercase,
        )

    if suffix in IMAGE_EXTENSIONS:
        return read_image_ocr(
            file_path,
            max_chars=max_chars,
            lowercase=lowercase,
        )

    return ""
