import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.product_support import check_updates, diagnostics, version_tuple
from app.release_signing import sign_binary, signing_command


class SupportTests(unittest.TestCase):
    def test_versions_are_strict_and_release_checks_are_explicit(self):
        self.assertEqual(version_tuple("V1.1.0"), (1, 1, 0))
        self.assertEqual(version_tuple("1.1.0+development"), (1, 1, 0))
        self.assertIsNone(version_tuple("2.0.0-preview"))
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "tag_name": "v2.0.0", "html_url": "https://github.com/SaDi-SL/FilePilot/releases/tag/v2.0.0"}).encode()
        with patch("app.product_support.request.urlopen", return_value=response) as transport:
            result = check_updates()
        self.assertEqual(result.status, "available")
        self.assertEqual(transport.call_args.kwargs["timeout"], 5)

    def test_release_links_cannot_redirect_to_an_unrelated_site(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "tag_name": "v2.0.0", "html_url": "https://evil.example/installer.exe"}).encode()
        with patch("app.product_support.request.urlopen", return_value=response):
            self.assertEqual(check_updates().status, "error")
        with patch("app.product_support.request.urlopen", side_effect=TimeoutError()):
            self.assertEqual(check_updates().status, "error")

    def test_report_excludes_source_paths_and_content_and_credentials(self):
        service = SimpleNamespace(monitor_state=SimpleNamespace(value="stopped"),
            startup_status=SimpleNamespace(value="ready"), config={"secret": "key"},
            library_snapshot=lambda: SimpleNamespace(total=3, searchable=2, semantic=1,
                roots=(Path("/private/records"),), files=("private document contents",)))
        with patch("app.product_support.discover_ocr_runtime", return_value=SimpleNamespace(available=True, languages=("eng",))):
            result = diagnostics(service)
        encoded = json.dumps(result)
        self.assertNotIn("/private", encoded)
        self.assertNotIn("private document", encoded)
        self.assertNotIn('"key"', encoded)
        self.assertEqual(result["files_with_text"], 2)

    def test_signing_requires_real_store_identity_and_verifies_the_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / "signtool.exe"
            tool.write_bytes(b"fake tool")
            windows = SimpleNamespace(name="nt", environ={"FILEPILOT_SIGNTOOL": str(tool), "FILEPILOT_CERT_THUMBPRINT": "A" * 40})
            with patch("app.release_signing.os", windows), patch("app.release_signing.subprocess.run") as run:
                sign_binary(Path(tmp) / "app.exe")
                self.assertEqual(run.call_count, 2)
                self.assertIn("SHA256", run.call_args_list[0].args[0])
                self.assertIn("/pa", run.call_args_list[1].args[0])
                self.assertNotIn("shell", run.call_args.kwargs)
            windows.environ.pop("FILEPILOT_CERT_THUMBPRINT")
            with patch("app.release_signing.os", windows):
                with self.assertRaisesRegex(ValueError, "certificate"):
                    signing_command("app.exe")
