"""Build the FilePilot Windows executable and, when available, its installer.

Usage:
    python build.py              Build a Windows release candidate
    python build.py --exe        Build the executable only
    python build.py --installer  Compile an installer from a current executable
    python build.py --dry-run    Validate and report release inputs only
    python build.py --clean      Remove only build/ and dist/
    python build.py --version    Print authoritative display and numeric versions
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Mapping

from app.product_identity import PRODUCT_IDENTITY
from app.windows_version import render_pyinstaller_version_file, windows_version_metadata


ROOT = Path(__file__).resolve().parent
APP_NAME = PRODUCT_IDENTITY.product_name
APP_VERSION = PRODUCT_IDENTITY.display_version
APP_EXE_NAME = f"{APP_NAME}.exe"
SPEC_FILE = ROOT / "FilePilot.spec"
DEFAULT_CONFIG = ROOT / "config" / "default_config.json"
ICON_FILE = ROOT / "icon.ico"
INSTALLER_FILE = ROOT / "installer.iss"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
VERSION_INFO_FILE = BUILD_DIR / "metadata" / "FilePilot-version-info.txt"
BUILD_IDENTITY_FILE = DIST_DIR / f"{APP_NAME}.version"
OCR_MANIFEST_FILE = ROOT / "third_party" / "ocr" / "runtime-manifest.json"
OCR_SOURCE_DIR = ROOT / "third_party" / "ocr" / "runtime"
OCR_DIST_DIR = DIST_DIR / "ocr"

PACKAGED_DATA_FILES = (DEFAULT_CONFIG, ICON_FILE)
EXCLUDED_DISTRIBUTION_PATHS = (
    "config/config.json",
    "backups/",
    "tests/",
    "opencode.json",
    "*.diff",
    "*.patch",
    ".env",
    ".git/",
    "logs/",
    "reports/",
    "operations.sqlite3",
)

def log(message: str) -> None:
    print(message)


def run(command: list[str]) -> bool:
    log(f"  > {' '.join(str(part) for part in command)}")
    return subprocess.run(command, cwd=ROOT, check=False).returncode == 0


def _walk_strings(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)
    elif isinstance(value, str):
        yield value


def release_source_files() -> tuple[Path, ...]:
    files = {
        ROOT / "build.py",
        SPEC_FILE,
        DEFAULT_CONFIG,
        ICON_FILE,
        INSTALLER_FILE,
        ROOT / "requirements.txt",
        ROOT / "requirements-qt.txt",
        ROOT / "requirements-build.txt",
        OCR_MANIFEST_FILE,
    }
    files.update((ROOT / "app").rglob("*.py"))
    return tuple(sorted((path for path in files if path.is_file()), key=str))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def release_source_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in release_source_files():
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256_file(path)))
    return digest.hexdigest()


def _load_ocr_manifest() -> tuple[dict | None, str | None]:
    if not OCR_MANIFEST_FILE.is_file():
        return None, "OCR runtime manifest is missing"
    try:
        manifest = json.loads(OCR_MANIFEST_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return None, f"OCR runtime manifest is invalid: {error}"
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        return None, "OCR runtime manifest schema is unsupported"
    if not isinstance(manifest.get("bundled"), bool):
        return None, "OCR runtime manifest must declare bundled as a boolean"
    if not isinstance(manifest.get("engine"), dict):
        return None, "OCR runtime manifest must declare engine metadata"
    if not isinstance(manifest.get("languages"), list):
        return None, "OCR runtime manifest must declare languages"
    if not isinstance(manifest.get("files"), list):
        return None, "OCR runtime manifest must declare files"
    return manifest, None


def validate_ocr_runtime_inputs() -> list[str]:
    """Fail closed before distributing any third-party OCR payload."""
    manifest, error = _load_ocr_manifest()
    if error:
        return [error]
    assert manifest is not None

    if not manifest["bundled"]:
        return [] if not manifest["files"] else [
            "OCR manifest cannot list distributable files while bundled is false"
        ]

    errors: list[str] = []
    engine = manifest["engine"]
    for field in ("name", "version", "license"):
        if not isinstance(engine.get(field), str) or not engine[field].strip():
            errors.append(f"OCR engine metadata is missing: {field}")

    language_codes = {
        item.get("code")
        for item in manifest["languages"]
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }
    for code in ("eng", "ara"):
        if code not in language_codes:
            errors.append(f"OCR manifest is missing required language: {code}")

    seen: set[str] = set()
    declared: set[str] = set()
    for item in manifest["files"]:
        if not isinstance(item, dict):
            errors.append("OCR manifest contains a malformed file entry")
            continue
        relative = item.get("path")
        expected_hash = item.get("sha256")
        license_name = item.get("license")
        if not isinstance(relative, str) or not relative:
            errors.append("OCR manifest contains an invalid file path")
            continue

        path = Path(relative)
        normalized = path.as_posix()
        if path.is_absolute() or normalized.startswith("../") or "/../" in f"/{normalized}/":
            errors.append(f"OCR manifest contains an unsafe file path: {relative}")
            continue

        key = normalized.casefold()
        if key in seen:
            errors.append(f"OCR manifest contains duplicate path: {relative}")
            continue
        seen.add(key)
        declared.add(normalized)

        if not isinstance(expected_hash, str) or re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash) is None:
            errors.append(f"OCR manifest has invalid sha256: {relative}")
            continue
        if not isinstance(license_name, str) or not license_name.strip():
            errors.append(f"OCR manifest is missing license metadata: {relative}")

        source = OCR_SOURCE_DIR / path
        try:
            source.resolve().relative_to(OCR_SOURCE_DIR.resolve())
        except ValueError:
            errors.append(f"OCR runtime file escapes source root: {relative}")
            continue
        if not source.is_file():
            errors.append(f"OCR runtime file is missing: {relative}")
            continue
        if _sha256_file(source).casefold() != expected_hash.casefold():
            errors.append(f"OCR runtime hash mismatch: {relative}")

    required = {
        "tesseract.exe",
        "tessdata/eng.traineddata",
        "tessdata/ara.traineddata",
    }
    for relative in sorted(required - declared):
        errors.append(f"OCR manifest is missing required file: {relative}")
    return errors


def stage_verified_ocr_runtime() -> bool:
    """Stage only a fully verified OCR sidecar into dist/ocr."""
    manifest, error = _load_ocr_manifest()
    if error:
        log(f"ERROR: {error}")
        return False
    assert manifest is not None

    if not manifest["bundled"]:
        log("OCR sidecar: not bundled in this development manifest")
        return True

    errors = validate_ocr_runtime_inputs()
    if errors:
        for message in errors:
            log(f"ERROR: {message}")
        return False

    if OCR_DIST_DIR.exists():
        shutil.rmtree(OCR_DIST_DIR)
    for item in manifest["files"]:
        source = OCR_SOURCE_DIR / item["path"]
        destination = OCR_DIST_DIR / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    shutil.copy2(OCR_MANIFEST_FILE, OCR_DIST_DIR / "runtime-manifest.json")
    log(f"OCR sidecar: verified and staged -> {OCR_DIST_DIR}")
    return True


def validate_release_inputs() -> list[str]:
    """Fail closed when the explicit release inputs violate the package contract."""
    errors: list[str] = []
    required = (SPEC_FILE, DEFAULT_CONFIG, ICON_FILE, INSTALLER_FILE, OCR_MANIFEST_FILE)
    for path in required:
        if not path.is_file():
            errors.append(f"Required release input is missing: {path.relative_to(ROOT)}")

    if DEFAULT_CONFIG.is_file():
        try:
            defaults = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            errors.append(f"Sanitized default config is invalid: {error}")
        else:
            if defaults.get("first_run_completed") is not False:
                errors.append("Sanitized defaults must require first-run setup")
            if not defaults.get("source_folder") or not defaults.get("organized_base_folder"):
                errors.append("Sanitized defaults must provide relative first-run folder names")
            ai = defaults.get("ai", {})
            if not isinstance(ai, dict) or ai.get("claude_api_key"):
                errors.append("Sanitized defaults must not contain an API key")
            for value in _walk_strings(defaults):
                if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\"):
                    errors.append("Sanitized defaults contain a machine-specific path")
                    break

    if SPEC_FILE.is_file():
        spec_text = SPEC_FILE.read_text(encoding="utf-8").replace("\\", "/").lower()
        unsafe_spec_inputs = (
            '"config" / "config.json"',
            'root / "tests"',
            'root / "backups"',
            'root / "opencode.json"',
            'root / ".env"',
        )
        for unsafe in unsafe_spec_inputs:
            if unsafe in spec_text:
                errors.append(f"PyInstaller spec includes forbidden input: {unsafe}")

    if INSTALLER_FILE.is_file():
        installer_text = INSTALLER_FILE.read_text(encoding="utf-8")
        if re.search(r"[A-Za-z]:\\", installer_text):
            errors.append("Installer contains a developer-machine absolute path")
        if "config\\config.json" in installer_text.lower():
            errors.append("Installer must not package the live source config")

    secret_pattern = re.compile(r"(?:sk-ant-|api[_-]?key\s*[=:]\s*['\"])[A-Za-z0-9_-]{12,}", re.I)
    users_path_pattern = re.compile(r"[A-Za-z]:[\\/]Users[\\/]", re.I)
    for path in (ROOT / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8", errors="replace")
        if secret_pattern.search(source):
            errors.append(f"Potential credential literal in packaged source: {path.relative_to(ROOT)}")
        if users_path_pattern.search(source):
            errors.append(f"Personal absolute path in packaged source: {path.relative_to(ROOT)}")

    errors.extend(validate_ocr_runtime_inputs())
    return errors


def print_static_review() -> bool:
    metadata = windows_version_metadata()
    log(f"Product: {APP_NAME} {APP_VERSION}")
    log(f"Build mode: professional Qt GUI, windowed one-file executable")
    log(f"Windows numeric version: {metadata.numeric_string}")
    log(f"Entry point: app/packaged_entry.py -> app.ui.qt.application.run_qt")
    log(f"Install destination: %LOCALAPPDATA%\\Programs\\{APP_NAME}")
    log(f"User data destination: %LOCALAPPDATA%\\{APP_NAME}")
    log("Packaged data:")
    for path in PACKAGED_DATA_FILES:
        log(f"  + {path.relative_to(ROOT)}")
    log("Explicitly excluded release inputs:")
    for path in EXCLUDED_DISTRIBUTION_PATHS:
        log(f"  - {path}")
    errors = validate_release_inputs()
    if errors:
        for error in errors:
            log(f"ERROR: {error}")
        return False
    log("Static release input validation: PASS")
    if ICON_FILE.stat().st_size < 1024:
        log("WARNING: icon.ico is valid for development but lacks production-size variants")
    return True


def find_inno_setup(
    *,
    environment: Mapping[str, str] | None = None,
    candidate_validator=None,
) -> Path | None:
    """Find ISCC deterministically: PATH, per-user, native, then x86 install."""
    if os.name != "nt":
        return None

    env = os.environ if environment is None else environment
    validate_candidate = (
        _is_usable_inno_compiler
        if candidate_validator is None
        else candidate_validator
    )
    candidates: list[Path] = []

    for entry in env.get("PATH", "").split(os.pathsep):
        clean_entry = entry.strip().strip('"')
        if clean_entry and Path(clean_entry).is_absolute():
            candidates.append(Path(clean_entry) / "ISCC.exe")

    standard_locations = (
        ("LOCALAPPDATA", Path("Programs") / "Inno Setup 6" / "ISCC.exe"),
        ("ProgramFiles", Path("Inno Setup 6") / "ISCC.exe"),
        ("ProgramFiles(x86)", Path("Inno Setup 6") / "ISCC.exe"),
    )
    for variable, relative_path in standard_locations:
        base = env.get(variable, "").strip()
        if base:
            candidates.append(Path(base).expanduser() / relative_path)

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.is_absolute():
            continue
        key = str(candidate).casefold()
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file() and validate_candidate(candidate):
            return candidate
    return None


def _is_usable_inno_compiler(candidate: Path) -> bool:
    """Confirm a candidate is Inno 6+ and can compile a disposable payload."""
    if not all(
        candidate.with_name(name).is_file()
        for name in ("ISCmplr.dll", "ISPP.dll")
    ):
        return False
    try:
        result = subprocess.run(
            [str(candidate), "/?"],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    version_match = re.search(
        r"Inno Setup (\d+) Command-Line Compiler",
        result.stdout,
    )
    if (
        result.returncode not in {0, 1}
        or version_match is None
        or int(version_match.group(1)) < 6
    ):
        return False

    try:
        with tempfile.TemporaryDirectory(prefix="FilePilot-Inno-Probe-") as temp_dir:
            probe_dir = Path(temp_dir)
            (probe_dir / "payload.txt").write_text("probe", encoding="ascii")
            script = probe_dir / "probe.iss"
            script.write_text(
                "#ifndef ProbeValue\n"
                '#error ProbeValue was not defined\n'
                "#endif\n"
                "[Setup]\n"
                "AppName=FilePilot Compiler Probe\n"
                "AppVersion=1\n"
                "DefaultDirName={tmp}\\FilePilotCompilerProbe\n"
                "Uninstallable=no\n"
                "OutputDir=output\n"
                "OutputBaseFilename=probe\n"
                "[Files]\n"
                'Source: "payload.txt"; DestDir: "{app}"\n',
                encoding="ascii",
            )
            probe = subprocess.run(
                [str(candidate), "/Qp", "/DProbeValue=1", str(script)],
                cwd=probe_dir,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=30,
            )
            output = probe_dir / "output" / "probe.exe"
            return probe.returncode == 0 and output.is_file()
    except (OSError, subprocess.TimeoutExpired):
        return False


def _safe_remove_output(path: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != ROOT or resolved.name not in {"build", "dist"}:
        raise RuntimeError(f"Refusing to remove uncontrolled path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def clean() -> None:
    for path in (BUILD_DIR, DIST_DIR):
        _safe_remove_output(path)
        log(f"Cleaned: {path}")


def prepare_build_outputs() -> None:
    _safe_remove_output(BUILD_DIR)
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    for path in (DIST_DIR / APP_EXE_NAME, BUILD_IDENTITY_FILE):
        path.unlink(missing_ok=True)
    installer_dir = DIST_DIR / "installer"
    if installer_dir.exists():
        shutil.rmtree(installer_dir)


def generate_version_metadata() -> None:
    VERSION_INFO_FILE.parent.mkdir(parents=True, exist_ok=True)
    VERSION_INFO_FILE.write_text(render_pyinstaller_version_file(), encoding="utf-8")
    log(f"Generated: {VERSION_INFO_FILE}")


def check_pyinstaller() -> bool:
    try:
        import PyInstaller
    except ImportError:
        log("ERROR: PyInstaller is unavailable. Install requirements-build.txt.")
        return False
    log(f"PyInstaller: {PyInstaller.__version__}")
    return True


def build_executable() -> bool:
    generate_version_metadata()
    start = time.monotonic()
    ok = run([
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR / "pyinstaller"),
        str(SPEC_FILE),
    ])
    executable = DIST_DIR / APP_EXE_NAME
    if not ok or not executable.is_file():
        log(f"Executable build: FAIL ({executable} was not produced)")
        return False
    identity = {
        "display_version": APP_VERSION,
        "numeric_version": windows_version_metadata().numeric_string,
        "executable_sha256": _sha256_file(executable),
        "source_fingerprint": release_source_fingerprint(),
    }
    BUILD_IDENTITY_FILE.write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    log(f"Executable build: PASS -> {executable}")
    log(f"Size: {executable.stat().st_size / (1024 * 1024):.1f} MB")
    log(f"Elapsed: {time.monotonic() - start:.0f}s")
    return True


def executable_matches_identity() -> bool:
    executable = DIST_DIR / APP_EXE_NAME
    if not executable.is_file() or not BUILD_IDENTITY_FILE.is_file():
        return False
    try:
        identity = json.loads(BUILD_IDENTITY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return identity == {
        "display_version": APP_VERSION,
        "numeric_version": windows_version_metadata().numeric_string,
        "executable_sha256": _sha256_file(executable),
        "source_fingerprint": release_source_fingerprint(),
    }


def build_installer(*, required: bool) -> bool | None:
    compiler = find_inno_setup()
    if compiler is None:
        message = "Inno Setup 6 compiler is unavailable; installer compilation not performed."
        log(f"ERROR: {message}" if required else message)
        return False if required else None
    if not executable_matches_identity():
        log("ERROR: Build the executable for the current product identity first.")
        return False
    metadata = windows_version_metadata()
    ok = run([
        str(compiler),
        f"/DAppName={APP_NAME}",
        f"/DAppVersion={APP_VERSION}",
        f"/DAppNumericVersion={metadata.numeric_string}",
        f"/DAppExeName={APP_EXE_NAME}",
        str(INSTALLER_FILE),
    ])
    output = DIST_DIR / "installer" / f"FilePilot-Setup-{APP_VERSION}.exe"
    if not ok or not output.is_file():
        log(f"Installer build: FAIL ({output} was not produced)")
        return False
    log(f"Installer build: PASS -> {output}")
    return True


def main() -> int:
    args = set(sys.argv[1:])
    valid_args = {"--version", "--clean", "--dry-run", "--exe", "--installer"}
    unknown = args - valid_args
    if unknown or ({"--exe", "--installer"} <= args):
        log(f"ERROR: unsupported arguments: {' '.join(sorted(unknown or args))}")
        return 2
    metadata = windows_version_metadata()
    if "--version" in args:
        log(f"{APP_NAME} {APP_VERSION} (Windows {metadata.numeric_string})")
        return 0
    if "--clean" in args:
        clean()
        return 0
    if not print_static_review():
        return 1
    if "--dry-run" in args:
        return 0
    if "--installer" in args:
        return 0 if build_installer(required=True) else 1
    if not check_pyinstaller():
        return 1
    prepare_build_outputs()
    if not build_executable():
        return 1
    if not stage_verified_ocr_runtime():
        return 1
    installer_result = None if "--exe" in args else build_installer(required=False)
    if installer_result is False:
        return 1
    log(f"Release candidate output: {DIST_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
