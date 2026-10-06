import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_helper():
    spec = importlib.util.spec_from_file_location(
        "prepare_ocr_runtime",
        ROOT / "tools" / "prepare_ocr_runtime.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PrepareOCRRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.helper = load_helper()

    def test_stages_exe_dlls_and_only_required_languages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            source = root / "source"
            runtime = root / "runtime"
            manifest = root / "manifest.json"
            (source / "tessdata").mkdir(parents=True)
            (source / "tesseract.exe").write_bytes(b"exe")
            (source / "libtesseract.dll").write_bytes(b"dll")
            (source / "uninstall.exe").write_bytes(b"uninstaller")
            (source / "tesseract-uninstall.exe").write_bytes(b"uninstaller")
            (source / "lstmtraining.exe").write_bytes(b"training-tool")
            (source / "text2image.exe").write_bytes(b"training-tool")
            (source / "README.txt").write_text("docs", encoding="utf-8")
            (source / "tessdata" / "eng.traineddata").write_bytes(b"eng")
            (source / "tessdata" / "ara.traineddata").write_bytes(b"ara")
            (source / "tessdata" / "swe.traineddata").write_bytes(b"swe")
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "bundled": False,
                        "engine": {"name": "Tesseract OCR"},
                        "languages": [{"code": "eng"}, {"code": "ara"}],
                        "files": [],
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch.object(self.helper, "RUNTIME_ROOT", runtime),
                patch.object(self.helper, "OCR_ROOT", root),
                patch.object(self.helper, "MANIFEST_FILE", manifest),
            ):
                files = self.helper._copy_runtime_files(source)
                self.helper._write_manifest(files)

            document = json.loads(manifest.read_text(encoding="utf-8"))

        relative = {item["path"] for item in document["files"]}
        self.assertTrue(document["bundled"])
        self.assertIn("tesseract.exe", relative)
        self.assertIn("libtesseract.dll", relative)
        self.assertIn("tessdata/eng.traineddata", relative)
        self.assertIn("tessdata/ara.traineddata", relative)
        self.assertNotIn("tessdata/swe.traineddata", relative)
        self.assertNotIn("uninstall.exe", relative)
        self.assertNotIn("tesseract-uninstall.exe", relative)
        self.assertNotIn("lstmtraining.exe", relative)
        self.assertNotIn("text2image.exe", relative)
        self.assertNotIn("README.txt", relative)
        self.assertTrue(all(len(item["sha256"]) == 64 for item in document["files"]))
        licenses = {item["path"]: item["license"] for item in document["files"]}
        self.assertEqual(licenses["tesseract.exe"], "Apache-2.0")
        self.assertEqual(licenses["tessdata/eng.traineddata"], "Apache-2.0")
        self.assertEqual(licenses["tessdata/ara.traineddata"], "Apache-2.0")
        self.assertEqual(licenses["libtesseract.dll"], "REVIEW_REQUIRED")

    def test_missing_required_language_fails_before_runtime_is_cleared(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            source = root / "source"
            runtime = root / "runtime"
            (source / "tessdata").mkdir(parents=True)
            (source / "tesseract.exe").write_bytes(b"exe")
            (source / "tessdata" / "eng.traineddata").write_bytes(b"eng")
            runtime.mkdir()
            sentinel = runtime / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")

            with (
                patch.object(self.helper, "RUNTIME_ROOT", runtime),
                patch.object(self.helper, "OCR_ROOT", root),
            ):
                with self.assertRaisesRegex(ValueError, "ara"):
                    self.helper._copy_runtime_files(source)

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
