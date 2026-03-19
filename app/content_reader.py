from pathlib import Path

from docx import Document
from pypdf import PdfReader


TEXT_BASED_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".log"
}


def safe_trim(text: str, max_chars: int = 4000) -> str:
    return text[:max_chars].strip().lower()


def read_plain_text(file_path: Path, max_chars: int = 4000) -> str:
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        return safe_trim(content, max_chars=max_chars)
    except Exception:
        return ""


def read_pdf_text(file_path: Path, max_pages: int = 2, max_chars: int = 4000) -> str:
    try:
        reader = PdfReader(str(file_path))
        texts = []

        total_pages = min(len(reader.pages), max_pages)
        for i in range(total_pages):
            page = reader.pages[i]
            page_text = page.extract_text() or ""
            texts.append(page_text)

        return safe_trim("\n".join(texts), max_chars=max_chars)
    except Exception:
        return ""


def read_docx_text(file_path: Path, max_paragraphs: int = 30, max_chars: int = 4000) -> str:
    try:
        doc = Document(str(file_path))
        texts = []

        for para in doc.paragraphs[:max_paragraphs]:
            if para.text.strip():
                texts.append(para.text)

        return safe_trim("\n".join(texts), max_chars=max_chars)
    except Exception:
        return ""


def extract_file_content(file_path: Path, max_chars: int = 4000) -> str:
    suffix = file_path.suffix.lower()

    if suffix in TEXT_BASED_EXTENSIONS:
        return read_plain_text(file_path, max_chars=max_chars)

    if suffix == ".pdf":
        return read_pdf_text(file_path, max_chars=max_chars)

    if suffix == ".docx":
        return read_docx_text(file_path, max_chars=max_chars)

    return ""