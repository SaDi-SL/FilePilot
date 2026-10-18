@echo off
setlocal
cd /d "%~dp0"
echo FilePilot - Windows build
where py >nul 2>nul
if errorlevel 1 (
    echo Python launcher not found. Install Python 3.12 with the Windows launcher, then try again.
    exit /b 1
)
if not exist ".venv-build\Scripts\python.exe" (
    py -3.12 -m venv .venv-build
    if errorlevel 1 exit /b 1
)
".venv-build\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1
".venv-build\Scripts\python.exe" build.py %*
if errorlevel 1 (
    echo Build failed. See the error above; no successful installer is claimed.
    exit /b 1
)
echo Done. Build output is in dist. If Inno Setup 6 is installed, the installer is in dist\installer.
exit /b 0
