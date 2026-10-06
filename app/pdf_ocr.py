from __future__ import annotations

import math
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from app.ocr_engine import (
    DEFAULT_OCR_LANGUAGES,
    DEFAULT_OCR_TIMEOUT_SECONDS,
    OCRExecutionResult,
    run_image_ocr,
)
from app.ocr_runtime import OCRRuntime


DEFAULT_PDF_OCR_MAX_PAGES = 5
DEFAULT_PDF_RENDER_SCALE = 2.0
MAX_PDF_OCR_PAGES = 20
DEFAULT_PDF_OCR_TOTAL_TIMEOUT_SECONDS = 60
MAX_PDF_OCR_TOTAL_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class PDFOCRResult:
    text: str
    status: str
    pages_attempted: int
    pages_extracted: int
    detail: str | None = None


def _load_pdfium():
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    return pdfium


def run_pdf_ocr(
    file_path: str | Path,
    *,
    runtime: OCRRuntime | None = None,
    languages: Iterable[str] = DEFAULT_OCR_LANGUAGES,
    max_pages: int = DEFAULT_PDF_OCR_MAX_PAGES,
    render_scale: float = DEFAULT_PDF_RENDER_SCALE,
    timeout_seconds: int = DEFAULT_OCR_TIMEOUT_SECONDS,
    total_timeout_seconds: int = DEFAULT_PDF_OCR_TOTAL_TIMEOUT_SECONDS,
    max_output_chars: int = 100_000,
    image_ocr: Callable[..., OCRExecutionResult] = run_image_ocr,
    clock: Callable[[], float] = time.monotonic,
) -> PDFOCRResult:
    """Render a bounded number of PDF pages and OCR them without persistent temp files."""
    source = Path(file_path).resolve()
    if not source.is_file():
        return PDFOCRResult(
            text="",
            status="extraction_failed",
            pages_attempted=0,
            pages_extracted=0,
            detail="PDF source file is unavailable",
        )

    pdfium = _load_pdfium()
    if pdfium is None:
        return PDFOCRResult(
            text="",
            status="ocr_unavailable",
            pages_attempted=0,
            pages_extracted=0,
            detail="PDF OCR renderer is unavailable",
        )

    bounded_pages = max(1, min(int(max_pages), MAX_PDF_OCR_PAGES))
    bounded_scale = max(1.0, min(float(render_scale), 4.0))
    bounded_output = max(1, min(int(max_output_chars), 100_000))
    bounded_total_timeout = max(
        1,
        min(int(total_timeout_seconds), MAX_PDF_OCR_TOTAL_TIMEOUT_SECONDS),
    )
    started_at = clock()
    deadline = started_at + bounded_total_timeout

    try:
        document = pdfium.PdfDocument(str(source))
    except Exception:
        return PDFOCRResult(
            text="",
            status="extraction_failed",
            pages_attempted=0,
            pages_extracted=0,
            detail="PDF could not be opened for OCR",
        )

    parts: list[str] = []
    pages_attempted = 0
    pages_extracted = 0
    try:
        page_count = min(len(document), bounded_pages)
        with tempfile.TemporaryDirectory(prefix="FilePilot-PDF-OCR-") as temp_dir:
            temp_root = Path(temp_dir)
            for page_index in range(page_count):
                if clock() >= deadline:
                    return PDFOCRResult(
                        text="",
                        status="extraction_failed",
                        pages_attempted=pages_attempted,
                        pages_extracted=pages_extracted,
                        detail="PDF OCR time budget exceeded",
                    )

                pages_attempted += 1
                page = None
                bitmap = None
                try:
                    page = document[page_index]
                    bitmap = page.render(scale=bounded_scale)
                    image = bitmap.to_pil()
                    rendered = temp_root / f"page-{page_index + 1}.png"
                    image.save(rendered, format="PNG")

                    remaining = bounded_output - sum(len(part) for part in parts)
                    if remaining <= 0:
                        break
                    remaining_seconds = deadline - clock()
                    if remaining_seconds <= 0:
                        return PDFOCRResult(
                            text="",
                            status="extraction_failed",
                            pages_attempted=pages_attempted,
                            pages_extracted=pages_extracted,
                            detail="PDF OCR time budget exceeded",
                        )
                    page_timeout = max(
                        1,
                        min(int(timeout_seconds), int(math.ceil(remaining_seconds))),
                    )

                    result = image_ocr(
                        rendered,
                        runtime=runtime,
                        languages=languages,
                        timeout_seconds=page_timeout,
                        max_output_chars=remaining,
                    )
                    if clock() > deadline:
                        return PDFOCRResult(
                            text="",
                            status="extraction_failed",
                            pages_attempted=pages_attempted,
                            pages_extracted=pages_extracted,
                            detail="PDF OCR time budget exceeded",
                        )
                    if result.status == "ocr_unavailable":
                        return PDFOCRResult(
                            text="",
                            status="ocr_unavailable",
                            pages_attempted=pages_attempted,
                            pages_extracted=pages_extracted,
                            detail=result.detail,
                        )
                    if result.status == "extraction_failed":
                        return PDFOCRResult(
                            text="",
                            status="extraction_failed",
                            pages_attempted=pages_attempted,
                            pages_extracted=pages_extracted,
                            detail=result.detail or "OCR failed while processing PDF page",
                        )
                    if result.text:
                        parts.append(result.text)
                        pages_extracted += 1
                except Exception:
                    return PDFOCRResult(
                        text="",
                        status="extraction_failed",
                        pages_attempted=pages_attempted,
                        pages_extracted=pages_extracted,
                        detail="PDF page rendering failed",
                    )
                finally:
                    for resource in (bitmap, page):
                        close = getattr(resource, "close", None)
                        if callable(close):
                            try:
                                close()
                            except Exception:
                                pass
    finally:
        close = getattr(document, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    text = "\n".join(parts)[:bounded_output].strip()
    return PDFOCRResult(
        text=text,
        status="extracted" if text else "empty",
        pages_attempted=pages_attempted,
        pages_extracted=pages_extracted,
        detail=None if text else "OCR found no searchable text in rendered PDF pages",
    )
