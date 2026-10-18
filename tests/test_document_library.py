import tempfile
import unittest
from dataclasses import replace
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.library_catalog import IndexCancelled, IndexControl
from app.local_rag import LocalRAGError, LocalRAGService
from app.product_search import ProductSearch
from app.search_index import SearchIndex, SearchIndexError
from app.embedding_service import EmbeddingResponse
from tests.test_local_rag import FakeLocalProvider


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.organized = self.root / "organized"
        self.external = self.root / "project"
        self.organized.mkdir(); self.external.mkdir()
        self.index = SearchIndex(self.root / "catalog.sqlite3")
        self.search = ProductSearch(self.index)
        self.source = self.external / "report.txt"
        self.source.write_text("Integration report. Requirement ABC passed. Action: review results.", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_extra_folders_are_persistent_read_only_and_removed_from_catalog_only(self):
        original = self.source.read_bytes()
        self.search.catalog.add_root(self.external)
        self.search.refresh(self.organized)
        self.assertEqual(self.search.search("Integration")[0].path, self.source)
        rebuilt = ProductSearch(SearchIndex(self.index.database_path))
        self.assertEqual(rebuilt.catalog.roots(), (self.external,))
        rebuilt.catalog.remove_root(self.external)
        rebuilt.refresh(self.organized)
        self.assertEqual(rebuilt.search("Integration"), ())
        self.assertEqual(self.source.read_bytes(), original)

    def test_unavailable_root_keeps_old_index_and_records_actionable_issue(self):
        self.search.catalog.add_root(self.external)
        self.search.refresh(self.organized)
        self.external.rename(self.root / "offline")
        result = self.search.refresh(self.organized)
        self.assertEqual(result.failed, 1)
        self.assertIsNotNone(self.index.get_file(self.source))
        snapshot = self.search.catalog.snapshot()
        self.assertTrue(any(f.status == "unavailable" for f in snapshot.files))
        self.assertTrue(any("unavailable" in f.issue.lower() for f in snapshot.files))

    def test_cancel_never_removes_unvisited_files_and_next_run_resumes(self):
        self.search.catalog.add_root(self.external)
        self.search.refresh(self.organized)
        new = self.external / "new.txt"
        new.write_text("new text", encoding="utf-8")
        control = IndexControl()
        control.cancelled.set()
        result = self.search.refresh(self.organized, control=control)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.removed, 0)
        self.assertIsNotNone(self.index.get_file(self.source))
        resumed = self.search.refresh(self.organized, control=IndexControl())
        self.assertEqual(resumed.indexed, 1)
        self.assertEqual(resumed.unchanged, 1)

    def test_scanning_error_does_not_prune_records_or_other_roots(self):
        self.search.catalog.add_root(self.external)
        self.search.refresh(self.organized)
        def partial(root):
            if root == self.external:
                raise SearchIndexError("permission denied")
            return iter(())
        with patch.object(self.search, "_iter_regular_files", side_effect=partial):
            result = self.search.refresh(self.organized)
        self.assertEqual(result.failed, 1)
        self.assertIsNotNone(self.index.get_file(self.source))

    def test_embedding_cache_makes_zero_model_calls_on_second_refresh(self):
        self.search.catalog.add_root(self.external)
        self.search.refresh(self.organized)
        class Provider:
            model = "test"
            provider_name = "ollama"
            def __init__(self): self.calls = 0
            def is_ready(self, **kw): return True
            def embed(self, text, **kw):
                self.calls += 1
                return EmbeddingResponse((1.0, 0.0), "ollama", self.model)
        provider = Provider()
        self.search._embedding_provider = provider
        first = self.search.refresh_semantic_embeddings()
        count = provider.calls
        second = self.search.refresh_semantic_embeddings()
        self.assertEqual(first.embedded, 1)
        self.assertEqual(second.unchanged, 1)
        self.assertEqual(provider.calls, count)
        self.source.write_text("Changed content", encoding="utf-8")
        self.search.refresh(self.organized)
        self.search.refresh_semantic_embeddings()
        self.assertGreater(provider.calls, count)

    def test_summary_uses_only_selected_file_and_refuses_stale_content(self):
        self.search.catalog.add_root(self.external)
        self.search.refresh(self.organized)
        provider = FakeLocalProvider(text="Requirement ABC passed [S1].")
        result = LocalRAGService(self.search, provider).summarize([self.source], language="Arabic")
        self.assertEqual(result.sources[0].path, self.source)
        self.assertIn("Arabic", provider.chat_calls[0][0])
        self.assertIn("excerpt-based", provider.chat_calls[0][0])
        self.source.write_text("Changed", encoding="utf-8")
        with self.assertRaisesRegex(SearchIndexError, "changed since"):
            LocalRAGService(self.search, provider).summarize([self.source])
        self.assertEqual(self.search.catalog.snapshot().files[0].status, "stale")

    def test_comparison_binds_two_files_and_rejects_unknown_citations(self):
        other = self.external / "other.txt"
        other.write_text("Requirement ABC failed", encoding="utf-8")
        self.search.catalog.add_root(self.external)
        self.search.refresh(self.organized)
        provider = FakeLocalProvider(text="Different outcomes [S1, S2].")
        result = LocalRAGService(self.search, provider).summarize([self.source, other], compare=True)
        self.assertEqual({s.path for s in result.sources}, {self.source, other})
        provider.text = "Imaginary [S99]"
        with self.assertRaises(LocalRAGError):
            LocalRAGService(self.search, provider).summarize([self.source])

    def test_organization_suggestion_enforces_categories_and_safe_name_without_file_changes(self):
        self.search.catalog.add_root(self.external)
        self.search.refresh(self.organized)
        before = self.source.read_bytes()
        provider = FakeLocalProvider(text="CATEGORY: reports\nNAME: integration-results.txt\nIntegration results [S1].")
        service = LocalRAGService(self.search, provider)
        result = service.propose_organization([self.source], ["reports"])
        self.assertIn("integration-results.txt", result.answer)
        self.assertEqual(self.source.read_bytes(), before)
        provider.text = "CATEGORY: unknown\nNAME: report.txt\nTest [S1]"
        with self.assertRaises(LocalRAGError): service.propose_organization([self.source], ["reports"])
        provider.text = "CATEGORY: reports\nNAME: ../report.txt\nTest [S1]"
        with self.assertRaises(LocalRAGError): service.propose_organization([self.source], ["reports"])

    def test_no_text_and_failure_are_explained_without_sending_to_model(self):
        self.search.catalog.add_root(self.external)
        with patch("app.search_index.extract_file_content_result", return_value=type("Extraction", (), {"text": "", "status": "ocr_unavailable"})()):
            self.search.refresh(self.organized)
        record = self.search.catalog.snapshot().files[0]
        self.assertIn("OCR", record.issue)
        provider = FakeLocalProvider()
        with self.assertRaisesRegex(SearchIndexError, "no readable"):
            LocalRAGService(self.search, provider).summarize([self.source])
        self.assertEqual(provider.chat_calls, [])

    def test_long_document_sampling_includes_late_text_and_stays_bounded(self):
        self.source.write_text("Beginning facts. " + "middle text " * 7000 + "Important end facts.", encoding="utf-8")
        self.search.catalog.add_root(self.external)
        self.search.refresh(self.organized)
        contexts = self.search.document_context([self.source])
        self.assertLessEqual(sum(len(c.text) for c in contexts), 16000)
        self.assertIn("Beginning", contexts[0].text)
        self.assertIn("Important end facts", contexts[-1].text)

    def test_failed_unindexed_file_issue_survives_job_finish(self):
        self.search.catalog.add_root(self.external)
        with patch.object(self.index, "index_file", side_effect=SearchIndexError("cannot read")):
            result = self.search.refresh(self.organized)
        self.search.catalog.finish(result)
        self.assertTrue(any("cannot read" in f.issue for f in self.search.catalog.snapshot().files))
        self.search.catalog.remove_root(self.external)
        self.search.refresh(self.organized)
        self.assertFalse(self.search.catalog.snapshot().files)
        self.assertTrue(self.source.exists())

    def test_existing_legacy_search_database_upgrades_without_losing_text(self):
        self.index.index_file(self.source)
        with closing(self.index._connect()) as db, db:
            for table in ("library_roots", "library_issues", "library_state"):
                db.execute(f"DROP TABLE {table}")
        restored = ProductSearch(SearchIndex(self.index.database_path))
        self.assertEqual(restored.search("Integration")[0].path, self.source)
        self.assertEqual(restored.catalog.roots(), ())

    def test_catalog_never_indexes_its_own_database_when_parent_folder_is_added(self):
        self.search.catalog.add_root(self.root)
        self.search.refresh(self.organized)
        self.assertNotIn(self.index.database_path, self.index.indexed_paths())
