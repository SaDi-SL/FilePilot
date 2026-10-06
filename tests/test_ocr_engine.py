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
        self.assertEqual(result.languages, ("eng", "ara"))
        command = run.call_args.args[0]
        self.assertEqual(command[0], str(runtime.executable))
        self.assertIn("eng+ara", command)
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


if __name__ == "__main__":
    unittest.main()
