from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OCR_ROOT = ROOT / "third_party" / "ocr"
RUNTIME_ROOT = OCR_ROOT / "runtime"
MANIFEST_FILE = OCR_ROOT / "runtime-manifest.json"

REQUIRED_LANGUAGES = ("eng", "ara")
RUNTIME_EXECUTABLE = "tesseract.exe"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_clear_runtime() -> None:
    resolved = RUNTIME_ROOT.resolve()
    expected_parent = OCR_ROOT.resolve()
    if resolved.parent != expected_parent or resolved.name != "runtime":
        raise RuntimeError(f"Refusing to clear uncontrolled path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def _copy_runtime_files(source: Path) -> list[Path]:
    executable = source / "tesseract.exe"
    tessdata = source / "tessdata"
    if not executable.is_file():
        raise ValueError(f"Tesseract executable was not found: {executable}")
    if not tessdata.is_dir():
        raise ValueError(f"Tesseract language directory was not found: {tessdata}")

    missing_languages = [
        code for code in REQUIRED_LANGUAGES
        if not (tessdata / f"{code}.traineddata").is_file()
    ]
    if missing_languages:
        raise ValueError(
            "Required OCR language data is missing: " + ", ".join(missing_languages)
        )

    _safe_clear_runtime()
    RUNTIME_ROOT.mkdir(parents=True)

    copied: list[Path] = []

    # Tesseract's Windows build relies on DLLs beside the executable. Ship the
    # runtime executable plus its top-level DLL set, but never auxiliary tools,
    # training executables, uninstallers, or unrelated programs.
    runtime_files = [executable]
    runtime_files.extend(
        candidate
        for candidate in sorted(source.iterdir(), key=lambda item: item.name.casefold())
        if candidate.is_file() and candidate.suffix.casefold() == ".dll"
    )
    for candidate in runtime_files:
        destination = RUNTIME_ROOT / candidate.name
        shutil.copy2(candidate, destination)
        copied.append(destination)

    destination_tessdata = RUNTIME_ROOT / "tessdata"
    destination_tessdata.mkdir()
    for code in REQUIRED_LANGUAGES:
        source_language = tessdata / f"{code}.traineddata"
        destination = destination_tessdata / source_language.name
        shutil.copy2(source_language, destination)
        copied.append(destination)

    return copied


def _write_manifest(files: list[Path]) -> None:
    original = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    entries = []
    for path in sorted(files, key=lambda item: item.relative_to(RUNTIME_ROOT).as_posix().casefold()):
        relative = path.relative_to(RUNTIME_ROOT).as_posix()
        entries.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "license": (
                    "Apache-2.0"
                    if relative == "tesseract.exe" or relative.startswith("tessdata/")
                    else "REVIEW_REQUIRED"
                ),
            }
        )

    original["bundled"] = True
    original["files"] = entries
    MANIFEST_FILE.write_text(
        json.dumps(original, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage a reviewed local Tesseract installation for FilePilot packaging."
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Tesseract installation directory containing tesseract.exe and tessdata/",
    )
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    if not source.is_dir():
        print(f"ERROR: source directory does not exist: {source}")
        return 2

    try:
        files = _copy_runtime_files(source)
        _write_manifest(files)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}")
        return 1

    print(f"Staged OCR runtime: {RUNTIME_ROOT}")
    print(f"Files: {len(files)}")
    print(f"Languages: {', '.join(REQUIRED_LANGUAGES)}")
    print(f"Manifest: {MANIFEST_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
