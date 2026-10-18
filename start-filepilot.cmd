@echo off
setlocal
cd /d "%~dp0"
echo FilePilot - preview latest project changes
if exist ".venv-dev\Scripts\python.exe" (
    ".venv-dev\Scripts\python.exe" scripts\run_windows_dev.py %*
    goto finished
)
py -3.12 --version >nul 2>nul
if not errorlevel 1 (
    py -3.12 scripts\run_windows_dev.py %*
    goto finished
)
python --version >nul 2>nul
if not errorlevel 1 (
    python scripts\run_windows_dev.py %*
    goto finished
)
echo Python was not found. Install Python 3.12, then open this file again.
pause
exit /b 1
:finished
if errorlevel 1 (
    echo FilePilot preview could not start. Read the message above.
    pause
    exit /b 1
)
exit /b 0
