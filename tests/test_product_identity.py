import importlib
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from app.branding import APP_NAME, APP_VERSION
from app.product_identity import PRODUCT_IDENTITY


class ProductIdentityTests(unittest.TestCase):
    def test_one_authoritative_runtime_identity(self):
        self.assertEqual(APP_NAME, PRODUCT_IDENTITY.product_name)
        self.assertEqual(APP_VERSION, PRODUCT_IDENTITY.version)
        self.assertEqual(app.__version__, PRODUCT_IDENTITY.version)
        self.assertEqual(PRODUCT_IDENTITY.channel, "development")
        self.assertEqual(PRODUCT_IDENTITY.release_basis, "V1.1.0")

    def test_identity_loads_without_git_or_network(self):
        with patch.object(subprocess, "run", side_effect=AssertionError("git used")), patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("network used"),
        ):
            module = importlib.reload(importlib.import_module("app.product_identity"))
        self.assertEqual(module.PRODUCT_IDENTITY.version, "1.1.0+development")

    def test_qt_sources_do_not_contain_an_independent_semantic_version(self):
        qt_root = Path(__file__).resolve().parents[1] / "app" / "ui" / "qt"
        for path in qt_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn('"1.0.0"', source, path)
            self.assertNotIn('"1.1.0"', source, path)

    def test_build_script_imports_identity_and_installer_requires_define(self):
        root = Path(__file__).resolve().parents[1]
        build_source = (root / "build.py").read_text(encoding="utf-8")
        installer_source = (root / "installer.iss").read_text(encoding="utf-8")
        self.assertIn("from app.product_identity import PRODUCT_IDENTITY", build_source)
        self.assertIn("/DAppVersion=", build_source)
        self.assertNotIn('#define AppVersion   "', installer_source)


if __name__ == "__main__":
    unittest.main()
