import tempfile
import unittest
from pathlib import Path

from app.application_paths import ApplicationPaths
from app.ocr_runtime import discover_ocr_runtime


class OCRRuntimeTests(unittest.TestCase):
    def _paths(self, root: Path, *, installed: bool) -> ApplicationPaths:
        return ApplicationPaths(
            installed=installed,
            install_root=root / "install",
            resource_root=root / "resources",
            user_data_root=root / "user",
        )

    def test_installed_mode_prefers_owned_bundled_runtime_and_languages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            paths = self._paths(root, installed=True)
            paths.ocr_runtime_dir.mkdir(parents=True)
            paths.tesseract_executable.write_bytes(b"exe")
            paths.tesseract_data_dir.mkdir()
            (paths.tesseract_data_dir / "eng.traineddata").write_bytes(b"eng")
            (paths.tesseract_data_dir / "ara.traineddata").write_bytes(b"ara")

            runtime = discover_ocr_runtime(
                paths=paths,
                environment={"PATH": ""},
                which=lambda _name: None,
            )

        self.assertTrue(runtime.available)
        self.assertEqual(runtime.source, "bundled")
        self.assertEqual(runtime.executable, paths.tesseract_executable)
        self.assertEqual(runtime.languages, ("ara", "eng"))
        self.assertTrue(runtime.supports("ENG"))
        self.assertTrue(runtime.supports("ara"))

    def test_installed_mode_does_not_trust_random_path_runtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            paths = self._paths(root, installed=True)
            external = root / "external" / "tesseract.exe"
            external.parent.mkdir()
            external.write_bytes(b"exe")

            runtime = discover_ocr_runtime(
                paths=paths,
                environment={"PATH": str(external.parent)},
                which=lambda _name: str(external),
            )

        self.assertFalse(runtime.available)
        self.assertIsNone(runtime.executable)
        self.assertEqual(runtime.source, "unavailable")

    def test_source_mode_can_use_absolute_path_runtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            paths = self._paths(root, installed=False)
            external = root / "tools" / "tesseract.exe"
            tessdata = external.parent / "tessdata"
            tessdata.mkdir(parents=True)
            external.write_bytes(b"exe")
            (tessdata / "eng.traineddata").write_bytes(b"eng")

            runtime = discover_ocr_runtime(
                paths=paths,
                environment={"PATH": str(external.parent)},
                which=lambda _name: str(external),
            )

        self.assertTrue(runtime.available)
        self.assertEqual(runtime.source, "path")
        self.assertEqual(runtime.languages, ("eng",))

    def test_source_mode_refuses_relative_path_resolution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            paths = self._paths(root, installed=False)

            runtime = discover_ocr_runtime(
                paths=paths,
                environment={"PATH": "."},
                which=lambda _name: "tesseract.exe",
            )

        self.assertFalse(runtime.available)
        self.assertIsNone(runtime.executable)

    def test_missing_runtime_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            paths = self._paths(root, installed=False)

            runtime = discover_ocr_runtime(
                paths=paths,
                environment={"PATH": ""},
                which=lambda _name: None,
            )

        self.assertFalse(runtime.available)
        self.assertIn("not available", (runtime.detail or "").lower())


if __name__ == "__main__":
    unittest.main()
