import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_build_module():
    spec = importlib.util.spec_from_file_location("filepilot_build_ocr", ROOT / "build.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class OCRPackagingContractTests(unittest.TestCase):
    def setUp(self):
        self.build = load_build_module()

    def test_development_manifest_is_explicitly_not_bundled(self):
        manifest, error = self.build._load_ocr_manifest()
        self.assertIsNone(error)
        self.assertFalse(manifest["bundled"])
        self.assertEqual(manifest["files"], [])
        self.assertEqual({item["code"] for item in manifest["languages"]}, {"eng", "ara"})

    def test_unbundled_manifest_does_not_require_local_binaries(self):
        self.assertEqual(self.build.validate_ocr_runtime_inputs(), [])

    def test_bundled_manifest_requires_verified_required_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            runtime = root / "runtime"
            (runtime / "tessdata").mkdir(parents=True)
            payloads = {
                "tesseract.exe": b"exe",
                "tessdata/eng.traineddata": b"eng",
                "tessdata/ara.traineddata": b"ara",
            }
            entries = []
            for relative, payload in payloads.items():
                path = runtime / relative
                path.write_bytes(payload)
                entries.append({
                    "path": relative,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "license": "Apache-2.0",
                })

            manifest = {
                "schema_version": 1,
                "bundled": True,
                "engine": {
                    "name": "Tesseract OCR",
                    "version": "test",
                    "license": "Apache-2.0",
                },
                "languages": [{"code": "eng"}, {"code": "ara"}],
                "files": entries,
            }

            with (
                patch.object(self.build, "OCR_SOURCE_DIR", runtime),
                patch.object(self.build, "_load_ocr_manifest", return_value=(manifest, None)),
            ):
                self.assertEqual(self.build.validate_ocr_runtime_inputs(), [])
                entries[0]["sha256"] = "0" * 64
                errors = self.build.validate_ocr_runtime_inputs()

        self.assertTrue(any("hash mismatch" in error for error in errors))

    def test_installer_declares_optional_staged_ocr_tree(self):
        source = (ROOT / "installer.iss").read_text(encoding="utf-8")
        self.assertIn('Source: "dist\\ocr\\*"', source)
        self.assertIn('DestDir: "{app}\\ocr"', source)
        self.assertIn("skipifsourcedoesntexist", source)


if __name__ == "__main__":
    unittest.main()
