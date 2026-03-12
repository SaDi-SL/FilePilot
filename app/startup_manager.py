import sys
from pathlib import Path

import winshell
from win32com.client import Dispatch


def get_startup_shortcut_path() -> Path:
    startup_dir = Path(winshell.startup())
    return startup_dir / "FileAutomation.lnk"


def is_startup_enabled() -> bool:
    return get_startup_shortcut_path().exists()


def enable_startup() -> None:
    shortcut_path = get_startup_shortcut_path()

    shell = Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(shortcut_path))

    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        shortcut.Targetpath = str(exe_path)
        shortcut.Arguments = "--startup"
        shortcut.WorkingDirectory = str(exe_path.parent)
        shortcut.IconLocation = str(exe_path)
    else:
        python_exe = str(Path(sys.executable).resolve())
        project_root = Path(__file__).resolve().parent.parent
        run_py = str(project_root / "run.py")

        shortcut.Targetpath = python_exe
        shortcut.Arguments = f'"{run_py}" --startup'
        shortcut.WorkingDirectory = str(project_root)
        shortcut.IconLocation = python_exe

    shortcut.save()


def disable_startup() -> None:
    shortcut_path = get_startup_shortcut_path()
    if shortcut_path.exists():
        shortcut_path.unlink()


def launched_from_startup() -> bool:
    return "--startup" in sys.argv