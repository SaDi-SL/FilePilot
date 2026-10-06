from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.ocr_runtime import OCRRuntime, discover_ocr_runtime


DEFAULT_OCR_LANGUAGES = ("eng", "ara")
DEFAULT_OCR_TIMEOUT_SECONDS = 20
MAX_OCR_OUTPUT_CHARS = 100_000
_LANGUAGE_CODE = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class OCRExecutionResult:
    text: str
    status: str
    languages: tuple[str, ...]
    detail: str | None = None


def _requested_languages(
    runtime: OCRRuntime,
    requested: Iterable[str],
) -> tuple[str, ...]:
    clean = tuple(
        language
        for language in requested
        if isinstance(language, str) and _LANGUAGE_CODE.fullmatch(language)
    )
    if not clean:
        return ()

    if not runtime.languages:
        return clean

    available = {language.casefold(): language for language in runtime.languages}
    return tuple(
        available[language.casefold()]
        for language in clean
        if language.casefold() in available
    )


def run_image_ocr(
    file_path: str | Path,
    *,
    runtime: OCRRuntime | None = None,
    languages: Iterable[str] = DEFAULT_OCR_LANGUAGES,
    timeout_seconds: int = DEFAULT_OCR_TIMEOUT_SECONDS,
    max_output_chars: int = MAX_OCR_OUTPUT_CHARS,
) -> OCRExecutionResult:
    """Run local Tesseract directly with bounded resources and no shell."""
    source = Path(file_path).resolve()
    selected_runtime = runtime or discover_ocr_runtime()

    if not selected_runtime.available or selected_runtime.executable is None:
        return OCRExecutionResult(
            text="",
            status="ocr_unavailable",
            languages=(),
            detail=selected_runtime.detail or "OCR runtime is unavailable",
        )

    executable = selected_runtime.executable
    if not executable.is_absolute() or not executable.is_file():
        return OCRExecutionResult(
            text="",
            status="ocr_unavailable",
            languages=(),
            detail="OCR executable is unavailable",
        )

    try:
        if not source.is_file():
            return OCRExecutionResult(
                text="",
                status="extraction_failed",
                languages=(),
                detail="OCR source file is unavailable",
            )
    except OSError:
        return OCRExecutionResult(
            text="",
            status="extraction_failed",
            languages=(),
            detail="OCR source file is unavailable",
        )

    selected_languages = _requested_languages(selected_runtime, languages)
    if not selected_languages:
        return OCRExecutionResult(
            text="",
            status="ocr_unavailable",
            languages=(),
            detail="Required OCR language data is unavailable",
        )

    command = [
        str(executable),
        str(source),
        "stdout",
        "-l",
        "+".join(selected_languages),
    ]
    if selected_runtime.tessdata_dir is not None:
        command.extend(["--tessdata-dir", str(selected_runtime.tessdata_dir)])

    bounded_timeout = max(1, min(int(timeout_seconds), 120))
    bounded_output = max(1, min(int(max_output_chars), MAX_OCR_OUTPUT_CHARS))
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        completed = subprocess.run(
            command,
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=bounded_timeout,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired:
        return OCRExecutionResult(
            text="",
            status="extraction_failed",
            languages=selected_languages,
            detail="OCR timed out",
        )
    except OSError:
        return OCRExecutionResult(
            text="",
            status="ocr_unavailable",
            languages=selected_languages,
            detail="OCR process could not be started",
        )

    if completed.returncode != 0:
        return OCRExecutionResult(
            text="",
            status="extraction_failed",
            languages=selected_languages,
            detail=f"OCR process failed with exit code {completed.returncode}",
        )

    text = (completed.stdout or "")[:bounded_output].strip()
    return OCRExecutionResult(
        text=text,
        status="extracted" if text else "empty",
        languages=selected_languages,
        detail=None if text else "OCR found no searchable text",
    )
