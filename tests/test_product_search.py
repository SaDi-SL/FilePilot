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

        first_document = self.index.semantic_document_chunks(first)[0]
        second_document = self.index.semantic_document_chunks(second)[0]
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

        document = self.index.semantic_document_chunks(source)[0]
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
        document = self.index.semantic_document_chunks(source)[0]
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
        document = self.index.semantic_document_chunks(source)[0]
        provider = FakeEmbeddingProvider({document: (1.0, 0.0)})
        semantic = ProductSearch(self.index, provider)
        semantic.refresh_semantic_embeddings()
        self.assertIsNotNone(self.index.embedding_fingerprint_for_file(source))

        source.write_text("second version changed", encoding="utf-8")
        self.search.refresh(self.organized)

        self.assertIsNone(self.index.embedding_fingerprint_for_file(source))

    def test_hybrid_search_combines_semantic_and_lexical_rankings(self):
        relevant = self.organized / "reports" / "railway.txt"
        generic = self.organized / "notes" / "generic.txt"
        relevant.parent.mkdir(parents=True)
        generic.parent.mkdir(parents=True)
        relevant.write_text(
            "railway system integration testing and verification",
            encoding="utf-8",
        )
        generic.write_text(
            "generic software test",
            encoding="utf-8",
        )
        self.search.refresh(self.organized)

        relevant_chunk = self.index.semantic_document_chunks(relevant)[0]
        generic_chunk = self.index.semantic_document_chunks(generic)[0]
        provider = FakeEmbeddingProvider({
            relevant_chunk: (0.8, 0.2),
            generic_chunk: (1.0, 0.0),
            "railway testing": (1.0, 0.0),
        })
        semantic = ProductSearch(self.index, provider)
        semantic.refresh_semantic_embeddings()

        results = semantic.hybrid_search("railway testing")

        self.assertEqual(results[0].path, relevant)
        self.assertIsNotNone(results[0].semantic_score)
        self.assertIsNotNone(results[0].lexical_rank)

    def test_hybrid_search_empty_query_is_safe(self):
        provider = FakeEmbeddingProvider({})
        semantic = ProductSearch(self.index, provider)

        self.assertEqual(semantic.hybrid_search("   "), ())
        self.assertEqual(provider.embed_calls, [])

    def test_semantic_query_removes_generic_command_words(self):
        self.assertEqual(
            ProductSearch._semantic_query_text("documents about train testing"),
            "train testing",
        )
        self.assertEqual(
            ProductSearch._semantic_query_text("please show me files about railway systems"),
            "railway systems",
        )

    def test_semantic_search_embeds_normalized_query(self):
        source = self.organized / "railway.txt"
        source.write_text("railway system testing", encoding="utf-8")
        self.search.refresh(self.organized)

        chunk = self.index.semantic_document_chunks(source)[0]
        provider = FakeEmbeddingProvider({
            chunk: (1.0, 0.0),
            "train testing": (1.0, 0.0),
        })
        semantic = ProductSearch(self.index, provider)
        semantic.refresh_semantic_embeddings()

        results = semantic.semantic_search("documents about train testing")

        self.assertEqual(results[0].path, source)
        self.assertIn(("train testing", 30.0, "query"), provider.embed_calls)
        self.assertNotIn(
            ("documents about train testing", 30.0, "query"),
            provider.embed_calls,
        )

    def test_bge_m3_semantic_search_filters_weak_matches(self):
        strong = self.organized / "strong.txt"
        weak = self.organized / "weak.txt"
        strong.write_text("relevant engineering content", encoding="utf-8")
        weak.write_text("unrelated content", encoding="utf-8")
        self.search.refresh(self.organized)

        strong_chunk = self.index.semantic_document_chunks(strong)[0]
        weak_chunk = self.index.semantic_document_chunks(weak)[0]
        provider = FakeEmbeddingProvider(
            {
                strong_chunk: (1.0, 0.0),
                weak_chunk: (0.35, 0.93675),
                "train testing": (1.0, 0.0),
            },
            model="bge-m3",
        )
        semantic = ProductSearch(self.index, provider)
        semantic.refresh_semantic_embeddings()

        results = semantic.semantic_search("train testing")

        self.assertEqual([item.path for item in results], [strong])
        self.assertGreaterEqual(results[0].score, 0.40)

    def test_non_bge_model_keeps_existing_semantic_behavior(self):
        source = self.organized / "legacy.txt"
        source.write_text("legacy embedding behavior", encoding="utf-8")
        self.search.refresh(self.organized)

        chunk = self.index.semantic_document_chunks(source)[0]
        provider = FakeEmbeddingProvider(
            {
                chunk: (0.35, 0.93675),
                "query": (1.0, 0.0),
            },
            model="embed-test",
        )
        semantic = ProductSearch(self.index, provider)
        semantic.refresh_semantic_embeddings()

        results = semantic.semantic_search("query")

        self.assertEqual(len(results), 1)
        self.assertLess(results[0].score, 0.40)

    def test_retrieve_context_is_bounded_and_source_grounded(self):
        first = self.organized / "reports" / "train.txt"
        second = self.organized / "notes" / "other.txt"
        first.parent.mkdir(parents=True)
        second.parent.mkdir(parents=True)
        first.write_text("traction verification " * 220, encoding="utf-8")
        second.write_text("unrelated notes " * 220, encoding="utf-8")
        self.search.refresh(self.organized)

        first_chunks = self.index.semantic_document_chunks(first)
        second_chunks = self.index.semantic_document_chunks(second)
        vectors = {}
        for index, chunk in enumerate(first_chunks):
            vectors[chunk] = (1.0, 0.0) if index < 3 else (0.9, 0.1)
        for chunk in second_chunks:
            vectors[chunk] = (0.2, 0.98)
        vectors["train testing"] = (1.0, 0.0)

        provider = FakeEmbeddingProvider(vectors, model="bge-m3")
        semantic = ProductSearch(self.index, provider)
        semantic.refresh_semantic_embeddings()

        contexts = semantic.retrieve_context(
            "documents about train testing",
            limit=6,
            max_chars=2500,
            max_chunks_per_file=2,
        )

        self.assertGreaterEqual(len(contexts), 1)
        self.assertTrue(all(item.path == first for item in contexts))
        self.assertLessEqual(len(contexts), 2)
        self.assertLessEqual(sum(len(item.text) for item in contexts), 2500)
        self.assertTrue(all(item.score >= 0.40 for item in contexts))
        self.assertIn("traction verification", contexts[0].text)

    def test_retrieve_context_returns_empty_when_no_strong_bge_match(self):
        source = self.organized / "notes.txt"
        source.write_text("generic notes", encoding="utf-8")
        self.search.refresh(self.organized)
        chunk = self.index.semantic_document_chunks(source)[0]
        provider = FakeEmbeddingProvider(
            {
                chunk: (0.3, 0.953939),
                "banana cake cooking recipe": (1.0, 0.0),
            },
            model="bge-m3",
        )
        semantic = ProductSearch(self.index, provider)
        semantic.refresh_semantic_embeddings()

        contexts = semantic.retrieve_context("banana cake cooking recipe")

        self.assertEqual(contexts, ())

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
