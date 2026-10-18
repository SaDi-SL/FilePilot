import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ocr_engine import run_image_ocr
from app.ocr_runtime import OCRRuntime


class OCREngineTests(unittest.TestCase):
    def _runtime(
        self,
        root: Path,
        *,
        languages=("eng", "ara"),
    ) -> OCRRuntime:
        executable = root / "ocr" / "tesseract.exe"
        tessdata = root / "ocr" / "tessdata"
        tessdata.mkdir(parents=True)
        executable.write_bytes(b"exe")
        for language in languages:
            (tessdata / f"{language}.traineddata").write_bytes(b"data")
        return OCRRuntime(
            available=True,
            executable=executable,
            tessdata_dir=tessdata,
            source="bundled",
            languages=tuple(languages),
        )

    def test_success_uses_no_shell_bounded_stdout_and_english_arabic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            source = root / "scan.png"
            source.write_bytes(b"image")
            runtime = self._runtime(root)
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="Hello مرحبا",
                stderr="",
            )

            with patch("app.ocr_engine.subprocess.run", return_value=completed) as run:
                result = run_image_ocr(source, runtime=runtime)

        self.assertEqual(result.status, "extracted")
        self.assertEqual(result.text, "Hello مرحبا")
        self.assertEqual(result.languages, ("ara", "eng"))
        command = run.call_args.args[0]
        self.assertEqual(command[0], str(runtime.executable))
        self.assertIn("ara+eng", command)
        self.assertIn("--tessdata-dir", command)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertLessEqual(run.call_args.kwargs["timeout"], 120)

    def test_missing_runtime_never_starts_process(self):
        runtime = OCRRuntime(
            available=False,
            executable=None,
            tessdata_dir=None,
            source="unavailable",
            languages=(),
            detail="Bundled OCR runtime is not installed",
        )
        with patch("app.ocr_engine.subprocess.run") as run:
            result = run_image_ocr("missing.png", runtime=runtime)

        self.assertEqual(result.status, "ocr_unavailable")
        run.assert_not_called()

    def test_missing_requested_language_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            source = root / "scan.png"
            source.write_bytes(b"image")
            runtime = self._runtime(root, languages=("swe",))

            with patch("app.ocr_engine.subprocess.run") as run:
                result = run_image_ocr(
                    source,
                    runtime=runtime,
                    languages=("eng", "ara"),
                )

        self.assertEqual(result.status, "ocr_unavailable")
        self.assertIn("language", (result.detail or "").lower())
        run.assert_not_called()

    def test_timeout_is_bounded_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            source = root / "scan.png"
            source.write_bytes(b"image")
            runtime = self._runtime(root)

            with patch(
                "app.ocr_engine.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["tesseract"], 1),
            ):
                result = run_image_ocr(
                    source,
                    runtime=runtime,
                    timeout_seconds=999,
                )

        self.assertEqual(result.status, "extraction_failed")
        self.assertEqual(result.detail, "OCR timed out")

    def test_nonzero_exit_is_failure_without_stderr_leak(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            source = root / "private-scan.png"
            source.write_bytes(b"image")
            runtime = self._runtime(root)
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=7,
                stdout="",
                stderr="C:\\Users\\Sensitive\\secret.png failed",
            )

            with patch("app.ocr_engine.subprocess.run", return_value=completed):
                result = run_image_ocr(source, runtime=runtime)

        self.assertEqual(result.status, "extraction_failed")
        self.assertEqual(result.detail, "OCR process failed with exit code 7")
        self.assertNotIn("Sensitive", result.detail or "")

    def test_empty_success_is_distinct_from_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            source = root / "blank.png"
            source.write_bytes(b"image")
            runtime = self._runtime(root)
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="   ",
                stderr="",
            )

            with patch("app.ocr_engine.subprocess.run", return_value=completed):
                result = run_image_ocr(source, runtime=runtime)

        self.assertEqual(result.status, "empty")
        self.assertEqual(result.text, "")

    def test_swedish_is_used_when_bundled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            runtime = self._runtime(root, languages=("eng", "ara", "swe"))
            source = root / "scan.png"
            source.write_bytes(b"image")
            with patch("app.ocr_engine.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "Svenska", "")) as run:
                result = run_image_ocr(source, runtime=runtime)
            self.assertEqual(result.languages, ("ara", "swe", "eng"))
            self.assertIn("ara+swe+eng", run.call_args.args[0])

    def test_phone_image_is_converted_without_changing_original_and_temp_is_cleaned(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            runtime = self._runtime(root)
            source = root / "phone.jpg"
            Image.new("RGB", (20, 10), "white").save(source)
            original = source.read_bytes()
            temporary = []
            def recognize(command, **kwargs):
                converted = Path(command[1])
                self.assertNotEqual(converted, source)
                self.assertTrue(converted.is_file())
                with Image.open(converted) as image:
                    self.assertEqual(image.format, "PNG")
                temporary.append(converted)
                raise subprocess.TimeoutExpired(command, 1)
            with patch("app.ocr_engine.subprocess.run", side_effect=recognize):
                result = run_image_ocr(source, runtime=runtime)
            self.assertEqual(result.detail, "OCR timed out")
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse(temporary[0].exists())

    def test_corrupt_photo_does_not_start_ocr_or_leak_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            runtime = self._runtime(root)
            source = root / "private-photo.jpg"
            source.write_bytes(b"not an image")
            with patch("app.ocr_engine.subprocess.run") as run:
                result = run_image_ocr(source, runtime=runtime)
            run.assert_not_called()
            self.assertEqual(result.status, "extraction_failed")
            self.assertEqual(result.detail, "Image could not be read")


if __name__ == "__main__":
    unittest.main()
