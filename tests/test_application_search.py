import tempfile
import unittest
from unittest.mock import patch
from app.local_rag import RAGAnswer
from pathlib import Path

from app.application_service import FilePilotService, StartupResult, StartupStatus
from app.product_search import ProductSearch
from app.search_index import SearchIndex, SearchIndexError


class ApplicationSearchServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.organized = self.root / "organized"
        self.organized.mkdir()
        search = ProductSearch(SearchIndex(self.root / "data" / "search.sqlite3"))
        self.service = FilePilotService(
            config_path=self.root / "config.json",
            journal_path=self.root / "data" / "operations.sqlite3",
            product_search=search,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _ready(self):
        self.service._config = {
            "organized_base_folder": str(self.organized),
        }
        self.service._startup_result = StartupResult(
            StartupStatus.READY,
            config=self.service._config,
        )

    def test_answers_use_configured_local_model_even_when_cloud_is_selected(self):
        self._ready()
        self.service._config["ai"] = {
            "provider": "claude", "ollama_model": "gemma4:e4b-it-qat",
        }
        expected = RAGAnswer("Question", "Answer", (), "ollama", "no_evidence")
        with patch("app.application_service.LocalRAGService") as engine:
            engine.return_value.ask.return_value = expected
            self.assertIs(self.service.ask_files("Question"), expected)
            search, provider = engine.call_args.args
            self.assertIs(search, self.service._product_search)
            self.assertEqual(provider.model, "gemma4:e4b-it-qat")
            self.assertFalse(provider.is_cloud)
            engine.return_value.ask.assert_called_once_with("Question")

    def test_refresh_requires_ready_configuration(self):
        with self.assertRaisesRegex(SearchIndexError, "ready"):
            self.service.refresh_search_index()

    def test_refresh_and_search_are_exposed_through_service(self):
        self._ready()
        reports = self.organized / "reports"
        reports.mkdir()
        source = reports / "IR50.txt"
        source.write_text(
            "Alstom traction verification complete",
            encoding="utf-8",
        )

        refreshed = self.service.refresh_search_index()
        hits = self.service.search_files("traction")

        self.assertEqual(refreshed.indexed, 1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].path, source)
        self.assertEqual(hits[0].category, "reports")

    def test_search_is_read_only_and_available_after_refresh(self):
        self._ready()
        source = self.organized / "manual.txt"
        source.write_text("searchable content", encoding="utf-8")
        self.service.refresh_search_index()
        before = source.read_bytes()

        self.assertEqual(len(self.service.search_files("searchable")), 1)
        self.assertEqual(source.read_bytes(), before)

    def test_refresh_reconciles_deleted_file(self):
        self._ready()
        source = self.organized / "delete.txt"
        source.write_text("temporary content", encoding="utf-8")
        self.service.refresh_search_index()
        source.unlink()

        refreshed = self.service.refresh_search_index()

        self.assertEqual(refreshed.removed, 1)
        self.assertEqual(self.service.search_files("temporary"), ())


if __name__ == "__main__":
    unittest.main()
