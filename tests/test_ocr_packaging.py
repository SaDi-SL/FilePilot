import hashlib
import json
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

    def test_repository_manifest_has_required_language_contract(self):
        manifest, error = self.build._load_ocr_manifest()
        self.assertIsNone(error)
        self.assertIsInstance(manifest["bundled"], bool)
        self.assertEqual({item["code"] for item in manifest["languages"]}, {"eng", "ara"})
        if not manifest["bundled"]:
            self.assertEqual(manifest["files"], [])

    def test_unbundled_manifest_does_not_require_local_binaries(self):
        manifest = {
            "schema_version": 1,
            "bundled": False,
            "engine": {"name": "Tesseract OCR", "version": "test", "license": "Apache-2.0"},
            "languages": [{"code": "eng"}, {"code": "ara"}],
            "files": [],
        }
        with patch.object(self.build, "_load_ocr_manifest", return_value=(manifest, None)):
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

    def test_installer_requires_explicit_ocr_bundle_flag(self):
        source = (ROOT / "installer.iss").read_text(encoding="utf-8")
        self.assertIn("#ifndef OCRBundled", source)
        self.assertIn("#if OCRBundled", source)
        self.assertIn('Source: "dist\\ocr\\*"', source)
        self.assertIn('DestDir: "{app}\\ocr"', source)
        self.assertNotIn("skipifsourcedoesntexist", source)

    def test_staged_bundled_runtime_must_match_manifest_exactly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            source_runtime = root / "source"
            staged_runtime = root / "dist" / "ocr"
            (source_runtime / "tessdata").mkdir(parents=True)
            staged_runtime.mkdir(parents=True)

            payloads = {
                "tesseract.exe": b"exe",
                "tessdata/eng.traineddata": b"eng",
                "tessdata/ara.traineddata": b"ara",
            }
            entries = []
            for relative, payload in payloads.items():
                source_path = source_runtime / relative
                source_path.write_bytes(payload)
                staged_path = staged_runtime / relative
                staged_path.parent.mkdir(parents=True, exist_ok=True)
                staged_path.write_bytes(payload)
                entries.append({
                    "path": relative,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "license": "Apache-2.0",
                })

            manifest = {
                "schema_version": 1,
                "bundled": True,
                "engine": {"name": "Tesseract OCR", "version": "test", "license": "Apache-2.0"},
                "languages": [{"code": "eng"}, {"code": "ara"}],
                "files": entries,
            }
            manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
            manifest_path = root / "runtime-manifest.json"
            manifest_path.write_bytes(manifest_bytes)
            (staged_runtime / "runtime-manifest.json").write_bytes(manifest_bytes)

            with (
                patch.object(self.build, "OCR_SOURCE_DIR", source_runtime),
                patch.object(self.build, "OCR_DIST_DIR", staged_runtime),
                patch.object(self.build, "OCR_MANIFEST_FILE", manifest_path),
                patch.object(self.build, "_load_ocr_manifest", return_value=(manifest, None)),
            ):
                self.assertEqual(self.build.validate_staged_ocr_runtime(), [])
                (staged_runtime / "tesseract.exe").write_bytes(b"tampered")
                errors = self.build.validate_staged_ocr_runtime()

        self.assertTrue(any("hash mismatch" in error for error in errors))

    def test_staged_bundled_runtime_rejects_missing_and_undeclared_files(self):
        manifest = {
            "schema_version": 1,
            "bundled": True,
            "engine": {"name": "Tesseract OCR", "version": "test", "license": "Apache-2.0"},
            "languages": [{"code": "eng"}, {"code": "ara"}],
            "files": [{
                "path": "tesseract.exe",
                "sha256": hashlib.sha256(b"exe").hexdigest(),
                "license": "Apache-2.0",
            }],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            staged = root / "ocr"
            staged.mkdir()
            (staged / "extra.dll").write_bytes(b"extra")
            manifest_path = root / "runtime-manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            (staged / "runtime-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with (
                patch.object(self.build, "OCR_DIST_DIR", staged),
                patch.object(self.build, "OCR_MANIFEST_FILE", manifest_path),
                patch.object(self.build, "_load_ocr_manifest", return_value=(manifest, None)),
            ):
                errors = self.build.validate_staged_ocr_runtime()

        self.assertTrue(any("missing: tesseract.exe" in error for error in errors))
        self.assertTrue(any("undeclared file: extra.dll" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
