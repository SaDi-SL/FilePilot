from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from app.application_paths import ApplicationPaths, get_application_paths


@dataclass(frozen=True)
class OCRRuntime:
    """Resolved local OCR capability without starting any OCR process."""

    available: bool
    executable: Path | None
    tessdata_dir: Path | None
    source: str
    languages: tuple[str, ...]
    detail: str | None = None

    def supports(self, language: str) -> bool:
        return language.casefold() in {item.casefold() for item in self.languages}


def _discover_languages(tessdata_dir: Path | None) -> tuple[str, ...]:
    if tessdata_dir is None or not tessdata_dir.is_dir():
        return ()
    languages = []
    try:
        for candidate in tessdata_dir.glob("*.traineddata"):
            if candidate.is_file() and candidate.stem:
                languages.append(candidate.stem)
    except OSError:
        return ()
    return tuple(sorted(set(languages), key=str.casefold))


def discover_ocr_runtime(
    *,
    paths: ApplicationPaths | None = None,
    environment: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> OCRRuntime:
    """Resolve bundled OCR first; permit PATH fallback only in source/development mode."""
    resolved_paths = paths or get_application_paths()
    bundled_executable = resolved_paths.tesseract_executable
    bundled_tessdata = resolved_paths.tesseract_data_dir

    if bundled_executable.is_file():
        languages = _discover_languages(bundled_tessdata)
        return OCRRuntime(
            available=True,
            executable=bundled_executable.resolve(),
            tessdata_dir=bundled_tessdata.resolve() if bundled_tessdata.is_dir() else None,
            source="bundled",
            languages=languages,
            detail=None if languages else "Bundled Tesseract was found without language data",
        )

    if resolved_paths.installed:
        return OCRRuntime(
            available=False,
            executable=None,
            tessdata_dir=None,
            source="unavailable",
            languages=(),
            detail="Bundled OCR runtime is not installed",
        )

    env = dict(os.environ if environment is None else environment)
    path_value = env.get("PATH", "")
    executable_value = which("tesseract") if path_value is not None else None
    if not executable_value:
        return OCRRuntime(
            available=False,
            executable=None,
            tessdata_dir=None,
            source="unavailable",
            languages=(),
            detail="Tesseract is not available in the development environment",
        )

    executable = Path(executable_value).expanduser()
    if not executable.is_absolute() or not executable.is_file():
        return OCRRuntime(
            available=False,
            executable=None,
            tessdata_dir=None,
            source="unavailable",
            languages=(),
            detail="Tesseract PATH entry is not a usable absolute executable",
        )

    tessdata_env = env.get("TESSDATA_PREFIX", "").strip()
    tessdata_dir = Path(tessdata_env).expanduser() if tessdata_env else executable.parent / "tessdata"
    if not tessdata_dir.is_absolute():
        tessdata_dir = None

    return OCRRuntime(
        available=True,
        executable=executable.resolve(),
        tessdata_dir=tessdata_dir.resolve() if tessdata_dir and tessdata_dir.is_dir() else None,
        source="path",
        languages=_discover_languages(tessdata_dir),
    )
