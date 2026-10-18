import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import windows_package_smoke as smoke


class PackageSmokeGuardTests(unittest.TestCase):
    def test_installer_validation_refuses_non_disposable_windows_environments(self):
        for github, runner in (("", ""), ("true", "self-hosted"), ("", "github-hosted")):
            with self.subTest(github=github, runner=runner), patch.object(smoke.sys, "platform", "win32"), patch.dict(smoke.os.environ, {"GITHUB_ACTIONS": github, "RUNNER_ENVIRONMENT": runner}), patch.object(smoke, "run_installer") as install:
                with self.assertRaisesRegex(RuntimeError, "disposable GitHub-hosted"):
                    smoke.validate(Path("unused.exe"), {})
                install.assert_not_called()

    def test_checksum_mismatch_stops_before_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dist").mkdir()
            installer = root / "Setup.exe"
            installer.write_bytes(b"candidate")
            (root / "dist/release-manifest.json").write_text(json.dumps({"files": [{"name": "installer/Setup.exe", "sha256": "wrong"}]}))
            with patch.object(smoke.sys, "platform", "win32"), patch.dict(smoke.os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}), patch.object(smoke, "ROOT", root), patch.object(smoke, "run_installer") as install:
                with self.assertRaisesRegex(RuntimeError, "checksum"):
                    smoke.validate(installer, {})
                install.assert_not_called()
