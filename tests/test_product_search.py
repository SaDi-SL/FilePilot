import tempfile
import unittest
from unittest import mock
from pathlib import Path

from app.embedding_service import EmbeddingResponse
from app.product_search import ProductSearch
from app.search_index import SearchIndex, SearchIndexError


class FakeEmbeddingProvider:
    def __init__(self, vectors, *, ready=True, model="embed-test"):
        self.vectors = dict(vectors)
        self.ready = ready
        self.model = model
        self.embed_calls = []
        self.ready_calls = []

    def is_ready(self, *, timeout=3.0):
        self.ready_calls.append(timeout)
        return self.ready

    def embed(self, text, *, timeout=30.0, task="document"):
        self.embed_calls.append((text, timeout, task))
        vector = self.vectors[text]
        return EmbeddingResponse(tuple(vector), "ollama", self.model)


class ProductSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.organized = self.root / "organized"
        self.organized.mkdir()
        self.index = SearchIndex(self.root / "data" / "search.sqlite3")
        self.search = ProductSearch(self.index)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_refresh_indexes_organized_tree_and_preserves_category(self):
        reports = self.organized / "reports" / "2026-10"
        reports.mkdir(parents=True)
        source = reports / "IR50.txt"
        source.write_text("traction verification complete", encoding="utf-8")

        result = self.search.refresh(self.organized)
        hits = self.search.search("traction")

        self.assertEqual(result.scanned, 1)
        self.assertEqual(result.indexed, 1)
        self.assertEqual(result.failed, 0)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].path, source)
        self.assertEqual(hits[0].category, "reports")

    def test_second_refresh_skips_unchanged_file(self):
        source = self.organized / "notes.txt"
        source.write_text("stable content", encoding="utf-8")
        self.search.refresh(self.organized)

        result = self.search.refresh(self.organized)

        self.assertEqual(result.scanned, 1)
        self.assertEqual(result.indexed, 0)
        self.assertEqual(result.unchanged, 1)

    def test_refresh_reindexes_unchanged_file_when_extraction_capability_changes(self):
        source = self.organized / "scan.pdf"
        source.write_bytes(b"%PDF-placeholder")

        with (
            mock.patch.object(
                self.index,
                "extraction_fingerprint",
                return_value="capability-a",
            ),
            mock.patch(
                "app.search_index.current_extraction_fingerprint",
                return_value="capability-a",
            ),
            mock.patch(
                "app.search_index.extract_file_content_result",
                return_value=type("Extraction", (), {"text": "", "status": "ocr_unavailable"})(),
            ),
        ):
            first = self.search.refresh(self.organized)

        self.assertEqual(first.indexed, 1)
        self.assertEqual(self.index.get_file(source).extraction_fingerprint, "capability-a")

        with (
            mock.patch.object(
                self.index,
                "extraction_fingerprint",
                return_value="capability-b",
            ),
            mock.patch(
                "app.search_index.current_extraction_fingerprint",
                return_value="capability-b",
            ),
            mock.patch(
                "app.search_index.extract_file_content_result",
                return_value=type("Extraction", (), {"text": "now searchable", "status": "extracted"})(),
            ),
        ):
            second = self.search.refresh(self.organized)

        self.assertEqual(second.indexed, 1)
        self.assertEqual(second.unchanged, 0)
        self.assertEqual(len(self.search.search("searchable")), 1)
        self.assertEqual(self.index.get_file(source).extraction_fingerprint, "capability-b")

    def test_refresh_reindexes_changed_file(self):
        source = self.organized / "notes.txt"
        source.write_text("old phrase", encoding="utf-8")
        self.search.refresh(self.organized)

        source.write_text("replacement phrase is longer", encoding="utf-8")
        result = self.search.refresh(self.organized)

        self.assertEqual(result.indexed, 1)
        self.assertEqual(self.search.search("old"), ())
        self.assertEqual(len(self.search.search("replacement")), 1)

    def test_refresh_removes_deleted_file_from_catalog(self):
        source = self.organized / "delete.txt"
        source.write_text("vanishing phrase", encoding="utf-8")
        self.search.refresh(self.organized)
        source.unlink()

        result = self.search.refresh(self.organized)

        self.assertEqual(result.removed, 1)
        self.assertEqual(self.search.search("vanishing"), ())

    def test_refresh_prunes_index_entries_outside_authoritative_root(self):
        outside = self.root / "outside.txt"
        outside.write_text("outside search term", encoding="utf-8")
        self.index.index_file(outside)

        result = self.search.refresh(self.organized)

        self.assertEqual(result.removed, 1)
        self.assertEqual(self.search.search("outside"), ())

    def test_semantic_refresh_embeds_indexed_documents_and_searches_by_meaning_vector(self):
        first = self.organized / "reports" / "train.txt"
        second = self.organized / "notes" / "cooking.txt"
        first.parent.mkdir(parents=True)
        second.parent.mkdir(parents=True)
        first.write_text("traction converter verification", encoding="utf-8")
        second.write_text("pasta recipe", encoding="utf-8")
        self.search.refresh(self.organized)

        first_document = self.index.semantic_document(first)
        second_document = self.index.semantic_document(second)
        provider = FakeEmbeddingProvider({
            first_document: (1.0, 0.0),
            second_document: (0.0, 1.0),
            "train testing": (0.9, 0.1),
        })
        semantic = ProductSearch(self.index, provider)

        refresh = semantic.refresh_semantic_embeddings()
        hits = semantic.semantic_search("train testing")

        self.assertEqual(refresh.scanned, 2)
        self.assertEqual(refresh.embedded, 2)
        self.assertEqual(refresh.failed, 0)
        self.assertEqual(hits[0].path, first)
        self.assertGreater(hits[0].score, hits[1].score)

    def test_semantic_refresh_requires_local_model_readiness(self):
        provider = FakeEmbeddingProvider({}, ready=False)
        semantic = ProductSearch(self.index, provider)

        with self.assertRaisesRegex(SearchIndexError, "unavailable"):
            semantic.refresh_semantic_embeddings()

    def test_semantic_refresh_skips_document_without_searchable_metadata_or_content(self):
        source = self.organized / "empty.txt"
        source.write_text("", encoding="utf-8")
        self.search.refresh(self.organized)

        document = self.index.semantic_document(source)
        provider = FakeEmbeddingProvider({document: (1.0, 0.0)})
        semantic = ProductSearch(self.index, provider)

        result = semantic.refresh_semantic_embeddings()

        self.assertEqual(result.scanned, 1)
        self.assertEqual(result.embedded, 1)
        self.assertEqual(result.skipped, 0)

    def test_semantic_refresh_reuses_matching_embedding_fingerprint(self):
        source = self.organized / "notes.txt"
        source.write_text("semantic stable document", encoding="utf-8")
        self.search.refresh(self.organized)
        document = self.index.semantic_document(source)
        provider = FakeEmbeddingProvider({
            document: (1.0, 0.0),
            "query": (1.0, 0.0),
        })
        semantic = ProductSearch(self.index, provider)

        first = semantic.refresh_semantic_embeddings()
        second = semantic.refresh_semantic_embeddings()

        self.assertEqual(first.embedded, 1)
        self.assertEqual(second.embedded, 0)
        self.assertEqual(second.unchanged, 1)

    def test_reindex_invalidates_existing_semantic_embedding(self):
        source = self.organized / "notes.txt"
        source.write_text("first version", encoding="utf-8")
        self.search.refresh(self.organized)
        document = self.index.semantic_document(source)
        provider = FakeEmbeddingProvider({document: (1.0, 0.0)})
        semantic = ProductSearch(self.index, provider)
        semantic.refresh_semantic_embeddings()
        self.assertIsNotNone(self.index.embedding_fingerprint_for_file(source))

        source.write_text("second version changed", encoding="utf-8")
        self.search.refresh(self.organized)

        self.assertIsNone(self.index.embedding_fingerprint_for_file(source))

    def test_semantic_search_empty_query_does_not_call_provider(self):
        provider = FakeEmbeddingProvider({})
        semantic = ProductSearch(self.index, provider)

        self.assertEqual(semantic.semantic_search("   "), ())
        self.assertEqual(provider.embed_calls, [])

    def test_missing_organized_root_is_refused(self):
        missing = self.root / "missing"

        with self.assertRaisesRegex(SearchIndexError, "unavailable"):
            self.search.refresh(missing)


if __name__ == "__main__":
    unittest.main()
