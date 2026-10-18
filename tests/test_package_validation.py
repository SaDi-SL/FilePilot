import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.content_reader import ContentExtractionResult
from app.ocr_runtime import OCRRuntime
from app.package_validation import check_ocr


class PackageOCRCheckTests(unittest.TestCase):
    def test_report_uses_actual_extraction_and_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'scan.png'
            source.write_bytes(b'original')
            report = Path(directory) / 'report.json'
            runtime = OCRRuntime(True, None, None, 'bundled', ('eng', 'ara', 'swe'))
            result = ContentExtractionResult('Recognized text', 'extracted', 'image_ocr')
            with patch('app.content_reader.extract_file_content_result', return_value=result) as extract, patch('app.ocr_runtime.discover_ocr_runtime', return_value=runtime):
                self.assertEqual(check_ocr(str(source), str(report)), 0)
                self.assertEqual(json.loads(report.read_text())['text'], result.text)
                extract.assert_called_once_with(source.resolve(), lowercase=False, max_pdf_pages=1)
                with self.assertRaises(FileExistsError):
                    check_ocr(str(source), str(report))
            self.assertEqual(check_ocr(str(source), str(source)), 1)
            self.assertEqual(source.read_bytes(), b'original')

    def test_internal_check_failure_never_launches_gui(self):
        from app.packaged_entry import main
        with patch('sys.argv', ['FilePilot.exe', '--package-ocr-check', 'scan.png', 'out.json']), patch('app.package_validation.check_ocr', side_effect=OSError('failure')), patch('app.packaged_entry._report_startup_error') as dialog:
            self.assertEqual(main(), 1)
            dialog.assert_not_called()
