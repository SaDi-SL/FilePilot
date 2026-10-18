"""Launch the current Qt source on Windows; no EXE or installer build required."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
import hashlib
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
IMPORT_CHECK = "import PySide6.QtWidgets, watchdog, pystray, PIL, winshell, win32api, docx, pypdf, pypdfium2, openpyxl"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}_is1"


def installed_locations(environment):
    """Read Inno's saved installation location, then try the normal per-user path."""
    locations = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "InstallLocation")
            if value and Path(value).is_absolute():
                locations.append(Path(value))
    except (ImportError, OSError):
        pass
    local = environment.get("LOCALAPPDATA", "").strip()
    if local and Path(local).is_absolute():
        locations.append(Path(local) / "Programs" / "FilePilot")
    return tuple(dict.fromkeys(locations))


def preview_environment(environment, locations):
    env = dict(environment)
    profile = env.get("FILEPILOT_DEV_PROFILE", "").strip()
    if not profile:
        local = env.get("LOCALAPPDATA", "").strip()
        if not local or not Path(local).is_absolute():
            raise RuntimeError("LOCALAPPDATA is unavailable. Cannot locate your FilePilot settings.")
        profile = str(Path(local) / "FilePilot")
    if not Path(profile).is_absolute():
        raise RuntimeError("FILEPILOT_DEV_PROFILE must be an absolute path.")
    env["FILEPILOT_DEV_PROFILE"] = profile
    # Reuse the installed engine read-only. Never copy it or change machine PATH.
    ocr = None
    for location in locations:
        candidate = location / "ocr"
        if ((location / "FilePilot.exe").is_file()
                and (candidate / "tesseract.exe").is_file()
                and all((candidate / "tessdata" / f"{lang}.traineddata").is_file()
                        for lang in ("ara", "eng", "swe"))):
            ocr = candidate.resolve()
            env["PATH"] = str(ocr) + os.pathsep + env.get("PATH", "")
            env["TESSDATA_PREFIX"] = str(ocr / "tessdata")
            break
    return env, ocr


def ensure_installed_app_closed(run=subprocess.run):
    result = run(["tasklist", "/FI", "IMAGENAME eq FilePilot.exe", "/FO", "CSV", "/NH"],
                 capture_output=True, text=True, check=True)
    for row in csv.reader(result.stdout.splitlines()):
        if row and row[0].casefold() == "filepilot.exe":
            raise RuntimeError("Close the installed FilePilot window first, then open start-filepilot.cmd again. No process was stopped.")


@contextmanager
def preview_lock(profile):
    """Hold one preview per profile until its child closes, including setup time."""
    import msvcrt
    profile.mkdir(parents=True, exist_ok=True)
    with (profile / ".preview.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("A FilePilot preview is already starting or running. Use its existing window.") from error
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def dependency_fingerprint(root):
    digest = hashlib.sha256(f"{sys.version_info[:2]}:dev-launcher-v1".encode())
    for name in ("requirements.txt", "requirements-qt.txt"):
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def prepare_python(root, *, repair=False, run=subprocess.run):
    venv = root / ".venv-dev"
    python = venv / "Scripts" / "python.exe"
    if not python.is_file():
        print("First run: preparing a private Python environment…", flush=True)
        run([sys.executable, "-m", "venv", str(venv)], cwd=root, check=True)
    stamp = venv / ".requirements.sha256"
    expected = dependency_fingerprint(root)
    installed = stamp.read_text().strip() if stamp.is_file() else ""
    if repair or installed != expected:
        print("Preparing FilePilot libraries (internet required for this step)…", flush=True)
        run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
             "-r", str(root / "requirements-qt.txt")], cwd=root, check=True)
        # Failed dependency checks never mark setup complete.
        run([str(python), "-c", IMPORT_CHECK], cwd=root, check=True)
        stamp.write_text(expected, encoding="ascii")
    else:
        run([str(python), "-c", IMPORT_CHECK], cwd=root, check=True)
    return python


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repair", action="store_true", help="Check/install dependencies again")
    parser.add_argument("--check", action="store_true", help="Prepare and check the environment without opening the window")
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("This launcher is for Windows. Other source runs use python -m app.ui.qt.")
        return 1
    if not (3, 12) <= sys.version_info[:2] < (3, 14):
        print("Use Python 3.12 (recommended) or 3.13 for the development preview.")
        return 1
    try:
        ensure_installed_app_closed()
        env, ocr = preview_environment(os.environ, installed_locations(os.environ))
        print("Preview settings and index: " + env["FILEPILOT_DEV_PROFILE"], flush=True)
        print("OCR: " + (str(ocr) if ocr else "No installed language bundle found; ordinary development OCR discovery will be used."), flush=True)
        with preview_lock(Path(env["FILEPILOT_DEV_PROFILE"])):
            python = prepare_python(ROOT, repair=args.repair)
            if args.check:
                print("Ready. Open start-filepilot.cmd to run the current project.")
                return 0
            # Check again after a potentially slow first setup.
            ensure_installed_app_closed()
            print("Opening the current Qt interface. Close it normally before updating the project.", flush=True)
            return subprocess.run([str(python), "-m", "app.packaged_entry"],
                                  cwd=ROOT, env=env, check=False).returncode
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Preview could not start: {error}", file=sys.stderr)
        print("For library setup problems, try: start-filepilot.cmd --repair", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
