"""
build.py — One-command build script for FilePilot.

Builds both the portable EXE and the Inno Setup installer.

Usage:
    python build.py              Build everything
    python build.py --exe        Build portable EXE only
    python build.py --installer  Build installer only (requires EXE first)
    python build.py --clean      Clean build artifacts
    python build.py --version    Show current version

Requirements:
    pip install pyinstaller
    Inno Setup 6+ installed at default path (for --installer)
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ── Config ────────────────────────────────────────────────────────────────────
APP_NAME    = "FilePilot"
APP_VERSION = "1.0.0"
SPEC_FILE   = ROOT / "FilePilot.spec"
DIST_DIR    = ROOT / "dist"
BUILD_DIR   = ROOT / "build"

INNO_PATHS = [
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe"),
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str, color: str = "") -> None:
    colors = {"green": "\033[92m", "red": "\033[91m",
              "yellow": "\033[93m", "blue": "\033[94m", "": ""}
    reset = "\033[0m" if color else ""
    print(f"{colors.get(color, '')}{msg}{reset}")


def run(cmd: list[str], cwd: Path = ROOT) -> bool:
    log(f"  > {' '.join(str(c) for c in cmd)}", "blue")
    result = subprocess.run(cmd, cwd=cwd)
    return result.returncode == 0


def check_requirements() -> bool:
    log("\nChecking requirements...", "yellow")
    ok = True

    # PyInstaller
    try:
        import PyInstaller
        log(f"  [+] PyInstaller {PyInstaller.__version__}", "green")
    except ImportError:
        log("  [-] PyInstaller not found. Run: pip install pyinstaller", "red")
        ok = False

    # Spec file
    if SPEC_FILE.exists():
        log(f"  [+] {SPEC_FILE.name}", "green")
    else:
        log(f"  [-] {SPEC_FILE.name} not found", "red")
        ok = False

    # Icon
    icon = ROOT / "icon.ico"
    if icon.exists():
        log(f"  [+] icon.ico", "green")
    else:
        log("  [!] icon.ico not found — EXE will have default icon", "yellow")

    return ok


def build_exe() -> bool:
    log(f"\nBuilding {APP_NAME}.exe...", "yellow")
    start = time.time()

    ok = run([
        sys.executable, "-m", "PyInstaller",
        str(SPEC_FILE),
        "--clean",
        "--noconfirm",
    ])

    elapsed = time.time() - start
    exe_path = DIST_DIR / f"{APP_NAME}.exe"

    if ok and exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        log(f"\n  [+] Built: {exe_path}", "green")
        log(f"      Size:  {size_mb:.1f} MB", "green")
        log(f"      Time:  {elapsed:.0f}s", "green")
        return True
    else:
        log(f"\n  [-] Build failed", "red")
        return False


def build_installer() -> bool:
    log(f"\nBuilding installer...", "yellow")

    # Find Inno Setup
    iscc = None
    for path in INNO_PATHS:
        if path.exists():
            iscc = path
            break

    if not iscc:
        log("  [-] Inno Setup not found.", "red")
        log("      Download from: https://jrsoftware.org/isdl.php", "yellow")
        return False

    # Check EXE exists
    exe_path = DIST_DIR / f"{APP_NAME}.exe"
    if not exe_path.exists():
        log(f"  [-] {APP_NAME}.exe not found. Build it first.", "red")
        return False

    iss_file = ROOT / "installer.iss"
    if not iss_file.exists():
        log(f"  [-] installer.iss not found", "red")
        return False

    log(f"  Using: {iscc}", "blue")
    ok = run([str(iscc), str(iss_file)])

    installer_dir = DIST_DIR / "installer"
    if ok and installer_dir.exists():
        installers = list(installer_dir.glob("*.exe"))
        if installers:
            f = installers[0]
            size_mb = f.stat().st_size / (1024 * 1024)
            log(f"\n  [+] Built: {f}", "green")
            log(f"      Size:  {size_mb:.1f} MB", "green")
            return True

    log(f"\n  [-] Installer build failed", "red")
    return False


def clean() -> None:
    log("\nCleaning build artifacts...", "yellow")
    for d in [BUILD_DIR, DIST_DIR]:
        if d.exists():
            shutil.rmtree(d)
            log(f"  Removed: {d}", "green")
    log("  Done.", "green")


def print_summary(exe_ok: bool, installer_ok: bool) -> None:
    log("\n" + "─" * 50, "")
    log(f"  BUILD SUMMARY — {APP_NAME} v{APP_VERSION}", "yellow")
    log("─" * 50, "")
    log(f"  Portable EXE:  {'OK  →  dist/FilePilot.exe' if exe_ok else 'FAILED'}", "green" if exe_ok else "red")
    log(f"  Installer:     {'OK  →  dist/installer/' if installer_ok else 'FAILED'}", "green" if installer_ok else "red")

    if exe_ok:
        log("\nNext steps:", "yellow")
        log("  1. Test:    dist\\FilePilot.exe")
        if installer_ok:
            log("  2. Share:   dist\\installer\\FilePilot_Setup_v*.exe")
        log("  3. Upload to GitHub Releases", "")
    log("")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]

    if "--version" in args:
        print(f"{APP_NAME} v{APP_VERSION}")
        return

    if "--clean" in args:
        clean()
        return

    log(f"\n{'='*50}", "")
    log(f"  {APP_NAME} v{APP_VERSION} — Build Script", "yellow")
    log(f"{'='*50}\n", "")

    if not check_requirements():
        log("\nFix the issues above and try again.\n", "red")
        sys.exit(1)

    exe_only       = "--exe" in args
    installer_only = "--installer" in args
    build_all      = not exe_only and not installer_only

    exe_ok       = False
    installer_ok = False

    if build_all or exe_only:
        exe_ok = build_exe()

    if build_all or installer_only:
        installer_ok = build_installer()

    print_summary(exe_ok, installer_ok)

    if not exe_ok and (build_all or exe_only):
        sys.exit(1)


if __name__ == "__main__":
    main()
