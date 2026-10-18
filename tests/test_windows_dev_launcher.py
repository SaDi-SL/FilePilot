"""Development launcher checks; installed data selection stays explicit."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.application_paths import ApplicationPathError, resolve_application_paths
from app.config_loader import ensure_external_config_exists
from app.ocr_runtime import discover_ocr_runtime
from scripts import run_windows_dev as dev


class DevelopmentLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.local = self.root / "Local App Data"
        self.env = {"LOCALAPPDATA": str(self.local), "PATH": "existing tools"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_preview_uses_existing_profile_and_leaves_resources_in_project(self):
        env, _ = dev.preview_environment(self.env, ())
        paths = resolve_application_paths(frozen=False, source_root=self.root / "project", environment=env)
        self.assertEqual(paths.user_data_root, self.local / "FilePilot")
        self.assertEqual(paths.resource_root, self.root / "project")
        self.assertFalse(paths.installed)
        paths.config_dir.mkdir(parents=True)
        original = b'{"existing": "settings must be retained"}'
        paths.config_file.write_bytes(original)
        with patch("app.config_loader.get_application_paths", return_value=paths):
            self.assertEqual(ensure_external_config_exists().read_bytes(), original)
        self.assertFalse((paths.resource_root / "data").exists())
        self.assertNotIn("FILEPILOT_DEV_PROFILE", self.env)

    def test_ordinary_source_paths_and_frozen_profile_are_unchanged(self):
        source = resolve_application_paths(frozen=False, source_root=self.root, environment={})
        self.assertEqual(source.user_data_root, self.root)
        env = dict(self.env, FILEPILOT_DEV_PROFILE=str(self.root / "custom"))
        installed = resolve_application_paths(frozen=True, executable=self.root / "install" / "FilePilot.exe", environment=env)
        self.assertEqual(installed.user_data_root, self.local / "FilePilot")
        with self.assertRaises(ApplicationPathError):
            resolve_application_paths(frozen=False, environment={"FILEPILOT_DEV_PROFILE": "relative"})
        with self.assertRaises(RuntimeError):
            dev.preview_environment({}, ())

    def test_reuses_complete_installed_ocr_read_only(self):
        install = self.root / "Custom Install"
        ocr = install / "ocr"
        tessdata = ocr / "tessdata"
        tessdata.mkdir(parents=True)
        (install / "FilePilot.exe").write_bytes(b"app")
        (ocr / "tesseract.exe").write_bytes(b"engine")
        for lang in ("ara", "eng", "swe"):
            (tessdata / f"{lang}.traineddata").write_bytes(lang.encode())
        before = {p: p.read_bytes() for p in install.rglob("*") if p.is_file()}
        env, chosen = dev.preview_environment(self.env, (install,))
        paths = resolve_application_paths(frozen=False, source_root=self.root / "project", environment=env)
        runtime = discover_ocr_runtime(paths=paths, environment=env, which=lambda _: str(ocr / "tesseract.exe"))
        self.assertEqual(chosen, ocr)
        self.assertEqual(runtime.languages, ("ara", "eng", "swe"))
        self.assertEqual(runtime.tessdata_dir, tessdata)
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(ocr))
        self.assertEqual(before, {p: p.read_bytes() for p in install.rglob("*") if p.is_file()})
        (tessdata / "swe.traineddata").unlink()
        env, chosen = dev.preview_environment(self.env, (install,))
        self.assertIsNone(chosen)
        self.assertNotIn("TESSDATA_PREFIX", env)

    def test_running_installed_app_blocks_preview_without_termination(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(stdout='"FilePilot.exe","1234","Console","1","30,000 K"\n')
        with self.assertRaisesRegex(RuntimeError, "Close the installed"):
            dev.ensure_installed_app_closed(run)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "tasklist")
        dev.ensure_installed_app_closed(lambda *a, **kw: SimpleNamespace(stdout="INFO: no matches"))

    def test_dependencies_prepare_once_and_retry_failed_setup(self):
        for name in ("requirements.txt", "requirements-qt.txt"):
            (self.root / name).write_text(name, encoding="ascii")
        python = self.root / ".venv-dev" / "Scripts" / "python.exe"
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if "venv" in args:
                python.parent.mkdir(parents=True)
                python.write_bytes(b"python")
            return SimpleNamespace(returncode=0)
        self.assertEqual(dev.prepare_python(self.root, run=run), python)
        self.assertEqual(sum("pip" in call for call in calls), 1)
        calls.clear()
        dev.prepare_python(self.root, run=run)
        self.assertEqual(len(calls), 1)  # imports only; no network on normal repeats
        self.assertIn(dev.IMPORT_CHECK, calls[0])
        (self.root / "requirements.txt").write_text("changed", encoding="ascii")
        def fail(*a, **kw):
            raise subprocess.CalledProcessError(1, a[0])
        with self.assertRaises(subprocess.CalledProcessError):
            dev.prepare_python(self.root, run=fail)
        stamp = self.root / ".venv-dev" / ".requirements.sha256"
        self.assertNotEqual(stamp.read_text(), dev.dependency_fingerprint(self.root))
        calls.clear()
        dev.prepare_python(self.root, run=run)
        self.assertEqual(sum("pip" in call for call in calls), 1)

    @unittest.skipUnless(os.name == "nt", "Native Windows file locking")
    def test_second_preview_is_blocked_and_lock_released_after_close(self):
        with dev.preview_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, "already"):
                with dev.preview_lock(self.root):
                    self.fail("Second preview acquired the profile")
        with dev.preview_lock(self.root):
            pass

    def test_qt_source_launch_uses_selected_profile_and_closes_cleanly(self):
        env = dict(os.environ, FILEPILOT_DEV_PROFILE=str(self.root / "preview"), QT_QPA_PLATFORM="offscreen")
        env.setdefault("PYSTRAY_BACKEND", "dummy")
        env.setdefault("LOCALAPPDATA", str(self.local))
        code = '''
from app.ui.qt.application import create_application, run_qt
from app.application_paths import get_application_paths
from PySide6.QtCore import QTimer
app = create_application([])
timer = QTimer()
timer.timeout.connect(app.closeAllWindows)
timer.start(1000)
print(get_application_paths().user_data_root, flush=True)
raise SystemExit(run_qt())
'''
        run = subprocess.run([sys.executable, "-c", code], cwd=dev.ROOT, env=env,
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn(str(self.root / "preview"), run.stdout)
        self.assertTrue((self.root / "preview" / "config" / "config.json").is_file())


if __name__ == "__main__":
    unittest.main()
