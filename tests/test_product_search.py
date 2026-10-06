import tempfile
import unittest
from pathlib import Path

from app.product_search import ProductSearch
from app.search_index import SearchIndex, SearchIndexError


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

    def test_missing_organized_root_is_refused(self):
        missing = self.root / "missing"

        with self.assertRaisesRegex(SearchIndexError, "unavailable"):
            self.search.refresh(missing)


if __name__ == "__main__":
    unittest.main()
