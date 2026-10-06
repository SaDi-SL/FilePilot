from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from app.ocr_engine import run_image_ocr
from app.pdf_ocr import run_pdf_ocr

logger = logging.getLogger(__name__)

TEXT_BASED_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".log"
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}
EXCEL_EXTENSIONS = {".xlsx"}
LEGACY_EXCEL_EXTENSIONS = {".xls"}


@dataclass(frozen=True)
class ContentExtractionResult:
    """Truthful local extraction outcome used by indexing and future OCR flows."""

    text: str
    status: str
    method: str
    detail: str | None = None

    @property
    def has_text(self) -> bool:
        return bool(self.text)


@dataclass(frozen=True)
class _ReaderResult:
    text: str
    failed: bool = False
    detail: str | None = None


def _failure_detail(error: Exception) -> str:
    return type(error).__name__


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


def _read_plain_text_result(
    file_path: Path,
    max_chars: int = 4000,
    *,
    lowercase: bool = True,
) -> _ReaderResult:
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        return _ReaderResult(
            text=trim_text(content, max_chars=max_chars, lowercase=lowercase),
        )
    except Exception as error:
        return _ReaderResult(text="", failed=True, detail=_failure_detail(error))


def read_plain_text(
    file_path: Path,
    max_chars: int = 4000,
    *,
    lowercase: bool = True,
) -> str:
    """Compatibility API: preserve the legacy empty-string-on-failure contract."""
    return _read_plain_text_result(
        file_path,
        max_chars=max_chars,
        lowercase=lowercase,
    ).text


def _read_pdf_text_result(
    file_path: Path,
    max_pages: int = 2,
    max_chars: int = 4000,
    *,
    lowercase: bool = True,
) -> _ReaderResult:
    try:
        reader = PdfReader(str(file_path))
        texts = []
        total_pages = min(len(reader.pages), max_pages)
        for index in range(total_pages):
            page_text = reader.pages[index].extract_text() or ""
            texts.append(page_text)
            if sum(len(text) for text in texts) >= max_chars:
                break
        return _ReaderResult(
            text=trim_text(
                "\n".join(texts),
                max_chars=max_chars,
                lowercase=lowercase,
            )
        )
    except Exception as error:
        return _ReaderResult(text="", failed=True, detail=_failure_detail(error))


def read_pdf_text(
    file_path: Path,
    max_pages: int = 2,
    max_chars: int = 4000,
    *,
    lowercase: bool = True,
) -> str:
    """Compatibility API: preserve the legacy empty-string-on-failure contract."""
    return _read_pdf_text_result(
        file_path,
        max_pages=max_pages,
        max_chars=max_chars,
        lowercase=lowercase,
    ).text


def _read_docx_text_result(
    file_path: Path,
    max_paragraphs: int | None = 30,
    max_chars: int = 4000,
    *,
    lowercase: bool = True,
) -> _ReaderResult:
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
        return _ReaderResult(
            text=trim_text(
                "\n".join(texts),
                max_chars=max_chars,
                lowercase=lowercase,
            )
        )
    except Exception as error:
        return _ReaderResult(text="", failed=True, detail=_failure_detail(error))


def read_docx_text(
    file_path: Path,
    max_paragraphs: int | None = 30,
    max_chars: int = 4000,
    *,
    lowercase: bool = True,
) -> str:
    """Compatibility API: preserve the legacy empty-string-on-failure contract."""
    return _read_docx_text_result(
        file_path,
        max_paragraphs=max_paragraphs,
        max_chars=max_chars,
        lowercase=lowercase,
    ).text


def _read_excel_text_result(
    file_path: Path,
    max_rows: int = 51,
    max_chars: int = 4000,
    *,
    lowercase: bool = False,
) -> _ReaderResult:
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
            return _ReaderResult(
                text=trim_text(
                    "\n".join(rows),
                    max_chars=max_chars,
                    lowercase=lowercase,
                )
            )
        finally:
            workbook.close()
    except Exception as error:
        return _ReaderResult(text="", failed=True, detail=_failure_detail(error))


def read_excel_text(
    file_path: Path,
    max_rows: int = 51,
    max_chars: int = 4000,
    *,
    lowercase: bool = False,
) -> str:
    """Compatibility API: preserve the legacy empty-string-on-failure contract."""
    return _read_excel_text_result(
        file_path,
        max_rows=max_rows,
        max_chars=max_chars,
        lowercase=lowercase,
    ).text


def extract_image_ocr_result(
    file_path: Path,
    max_chars: int = 4000,
    *,
    lowercase: bool = False,
) -> ContentExtractionResult:
    result = run_image_ocr(
        file_path,
        max_output_chars=max_chars,
    )
    text = trim_text(
        result.text,
        max_chars=max_chars,
        lowercase=lowercase,
    )
    return ContentExtractionResult(
        text=text,
        status=result.status,
        method="image_ocr",
        detail=result.detail,
    )

def read_image_ocr(
    file_path: Path,
    max_chars: int = 4000,
    *,
    lowercase: bool = False,
) -> str:
    """Compatibility API returning only OCR text."""
    return extract_image_ocr_result(
        file_path,
        max_chars=max_chars,
        lowercase=lowercase,
    ).text


def extract_file_content_result(
    file_path: Path,
    max_chars: int = 4000,
    *,
    lowercase: bool = True,
    max_pdf_pages: int = 2,
    max_docx_paragraphs: int | None = 30,
) -> ContentExtractionResult:
    """Extract local text and preserve why content was or was not available."""
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()

    try:
        if suffix in TEXT_BASED_EXTENSIONS:
            read = _read_plain_text_result(
                file_path,
                max_chars=max_chars,
                lowercase=lowercase,
            )
            return ContentExtractionResult(
                text=read.text,
                status="extraction_failed" if read.failed else ("extracted" if read.text else "empty"),
                method="plain_text",
                detail=read.detail,
            )

        if suffix == ".pdf":
            read = _read_pdf_text_result(
                file_path,
                max_pages=max_pdf_pages,
                max_chars=max_chars,
                lowercase=lowercase,
            )
            if read.failed:
                return ContentExtractionResult(
                    text="",
                    status="extraction_failed",
                    method="pdf_text",
                    detail=read.detail,
                )
            if read.text:
                return ContentExtractionResult(
                    text=read.text,
                    status="extracted",
                    method="pdf_text",
                )

            ocr = run_pdf_ocr(
                file_path,
                max_pages=max_pdf_pages,
                max_output_chars=max_chars,
            )
            ocr_text = trim_text(
                ocr.text,
                max_chars=max_chars,
                lowercase=lowercase,
            )
            return ContentExtractionResult(
                text=ocr_text,
                status=ocr.status,
                method="pdf_ocr",
                detail=ocr.detail,
            )

        if suffix == ".docx":
            read = _read_docx_text_result(
                file_path,
                max_paragraphs=max_docx_paragraphs,
                max_chars=max_chars,
                lowercase=lowercase,
            )
            return ContentExtractionResult(
                text=read.text,
                status="extraction_failed" if read.failed else ("extracted" if read.text else "empty"),
                method="docx",
                detail=read.detail,
            )

        if suffix in EXCEL_EXTENSIONS:
            read = _read_excel_text_result(
                file_path,
                max_chars=max_chars,
                lowercase=lowercase,
            )
            return ContentExtractionResult(
                text=read.text,
                status="extraction_failed" if read.failed else ("extracted" if read.text else "empty"),
                method="xlsx",
                detail=read.detail,
            )

        if suffix in LEGACY_EXCEL_EXTENSIONS:
            return ContentExtractionResult(
                text="",
                status="unsupported_format",
                method="none",
                detail="Legacy .xls files are not supported by the local extractor",
            )

        if suffix in IMAGE_EXTENSIONS:
            return extract_image_ocr_result(
                file_path,
                max_chars=max_chars,
                lowercase=lowercase,
            )

        return ContentExtractionResult(
            text="",
            status="unsupported_format",
            method="none",
        )
    except Exception as error:
        logger.debug("Content extraction failed for %s: %s", file_path.name, error)
        return ContentExtractionResult(
            text="",
            status="extraction_failed",
            method="none",
            detail=type(error).__name__,
        )


def extract_file_content(
    file_path: Path,
    max_chars: int = 4000,
    *,
    lowercase: bool = True,
    max_pdf_pages: int = 2,
    max_docx_paragraphs: int | None = 30,
) -> str:
    """Compatibility API returning only extracted text."""
    return extract_file_content_result(
        file_path,
        max_chars=max_chars,
        lowercase=lowercase,
        max_pdf_pages=max_pdf_pages,
        max_docx_paragraphs=max_docx_paragraphs,
    ).text
