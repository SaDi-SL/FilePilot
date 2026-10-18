"""Install an unsigned test candidate on a disposable Windows runner and verify it.

Runs only explicitly, outside the unit suite. Uses a temporary install/profile,
requires a real visible Qt window, and checks user data survives uninstallation.
"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def visible_app_windows(executable):
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    found = []

    @callback_type
    def visit(hwnd, _):
        if not user.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return True
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                if Path(buffer.value).resolve() == executable.resolve():
                    title = ctypes.create_unicode_buffer(1024)
                    user.GetWindowTextW(hwnd, title, len(title))
                    found.append((hwnd, title.value))
        finally:
            kernel.CloseHandle(handle)
        return True

    user.EnumWindows(visit, 0)
    return found


def run_installer(command, environment):
    print('Running installer operation', flush=True)
    result = subprocess.run(command, env=environment, timeout=180, check=False)
    if result.returncode:
        raise RuntimeError(f'Installer operation failed with exit code {result.returncode}')


def validate(installer, report):
    if sys.platform != 'win32':
        raise RuntimeError('Package smoke validation requires Windows')
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('Installer validation is restricted to disposable GitHub-hosted runners')
    manifest = json.loads((ROOT / 'dist/release-manifest.json').read_text(encoding='utf-8'))
    hashes = {item['name'].replace('\\', '/'): item['sha256'] for item in manifest['files']}
    if digest(installer) != hashes.get('installer/' + installer.name):
        raise RuntimeError('Installer checksum differs from the release manifest')
    logs = ROOT / 'build/package-validation'
    logs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='FilePilot-package-') as temporary:
        base = Path(temporary)
        destination = base / 'application'
        environment = dict(os.environ)
        environment['LOCALAPPDATA'] = str(base / 'profile')
        environment['QT_QPA_PLATFORM'] = 'windows'
        data = base / 'profile/FilePilot'
        run_installer([str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
                       f'/DIR={destination}', f'/LOG={logs / "install.log"}'], environment)
        executable = destination / 'FilePilot.exe'
        if not executable.is_file() or digest(executable) != hashes['FilePilot.exe']:
            raise RuntimeError('Installed executable checksum mismatch')
        process = subprocess.Popen([str(executable)], cwd=base, env=environment)
        try:
            print('Waiting for the installed Qt window', flush=True)
            deadline = time.monotonic() + 90
            hwnd = None
            startup_error = data / 'logs/startup-error.log'
            while time.monotonic() < deadline:
                if startup_error.exists():
                    raise RuntimeError('Installed application reported a startup error')
                hwnd = next((handle for handle, title in visible_app_windows(executable)
                             if title.startswith("FilePilot ")), None)
                if hwnd:
                    break
                if process.poll() is not None:
                    raise RuntimeError(f'Application exited before a window appeared: {process.returncode}')
                time.sleep(0.25)
            if not hwnd:
                raise RuntimeError('Installed application did not create a visible window within 90 seconds')
            time.sleep(3)
            if process.poll() is not None or startup_error.exists():
                raise RuntimeError('Installed application failed after creating its window')
            config = data / 'config/config.json'
            if not config.is_file():
                raise RuntimeError('Application did not create its external user configuration')
            user = ctypes.WinDLL('user32', use_last_error=True)
            user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            for handle, _title in visible_app_windows(executable):
                if handle != hwnd:
                    user.PostMessageW(handle, 0x0010, 0, 0)
            time.sleep(0.25)
            user.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE: use normal application shutdown
            process.wait(timeout=30)
            if process.returncode:
                raise RuntimeError(f'Application shutdown failed: {process.returncode}')
        finally:
            if process.poll() is None:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=False, timeout=15)
                process.wait(timeout=15)
        sentinel = data / 'package-preservation-check.txt'
        sentinel.write_text('User data must survive uninstallation', encoding='utf-8')
        config_hash = digest(config)
        uninstaller = destination / 'unins000.exe'
        if not uninstaller.is_file():
            raise RuntimeError('Uninstaller was not created')
        run_installer([str(uninstaller), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                       f'/LOG={logs / "uninstall.log"}'], environment)
        deadline = time.monotonic() + 15
        while executable.exists() and time.monotonic() < deadline:
            time.sleep(0.25)
        if executable.exists():
            raise RuntimeError('Uninstallation left the application executable behind')
        if not sentinel.is_file() or digest(config) != config_hash:
            raise RuntimeError('Uninstallation did not preserve the user-data checks')
    report.update(status='passed', version=manifest['version'],
                  source_fingerprint=manifest['source_fingerprint'],
                  installed_checksum_verified=True, visible_window_seen=True,
                  graceful_shutdown=True, executable_removed=True, user_data_preserved=True,
                  signed=manifest['signed_build_requested'],
                  ocr_bundled=json.loads((ROOT / "third_party/ocr/runtime-manifest.json").read_text())["bundled"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = {'status': 'failed', 'commit': os.environ.get('GITHUB_SHA', '')}
    try:
        validate(args.installer.resolve(), report)
    except Exception as error:
        report['error'] = str(error)
        print(f'Package validation failed: {error}', flush=True)
        return_code = 1
    else:
        print('Installation, visible Qt launch, shutdown and uninstall preservation: PASS', flush=True)
        return_code = 0
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return return_code


if __name__ == '__main__':
    raise SystemExit(main())
