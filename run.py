"""
run.py — Entry point for FilePilot.

Modes:
    python run.py               Normal GUI mode
    python run.py --headless    Headless mode (tray only, no window)
    python run.py --help        Show usage
"""
import sys


def main() -> None:
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)

    if "--headless" in args:
        from app.headless import run_headless
        run_headless()
    else:
        from app.gui import launch_gui
        launch_gui()


if __name__ == "__main__":
    main()