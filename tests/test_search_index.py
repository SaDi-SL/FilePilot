import sqlite3
import tempfile
from contextlib import closing
import unittest
from pathlib import Path
from unittest.mock import patch

from app.search_index import (
    MAX_SEARCH_RESULTS,
    SEARCH_APPLICATION_ID,
    SEARCH_SCHEMA_VERSION,
    SearchIndex,
    SearchIndexError,
)


class SearchIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.database = self.root / "data" / "search.sqlite3"
        self.index = SearchIndex(self.database)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_database_identity_and_schema_version_are_set(self):
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA application_id").fetchone()[0],
                SEARCH_APPLICATION_ID,
            )
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                SEARCH_SCHEMA_VERSION,
            )

    def test_indexes_and_finds_filename_category_and_content(self):
        source = self.root / "IR50-report.txt"
        source.write_text(
            "Traction converter verification completed for the Alstom project.",
            encoding="utf-8",
        )

        indexed = self.index.index_file(source, category="reports")

        self.assertEqual(indexed.path, source)
        self.assertEqual(indexed.category, "reports")
        self.assertEqual(indexed.extraction_status, "indexed")
        self.assertEqual(self.index.search("IR50")[0].path, source)
        self.assertEqual(self.index.search("reports")[0].path, source)
        results = self.index.search("Alstom traction")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].path, source)
        self.assertIn("[", results[0].snippet)

    def test_reindex_replaces_stale_search_content(self):
        source = self.root / "notes.txt"
        source.write_text("old searchable phrase", encoding="utf-8")
        self.index.index_file(source)
        self.assertEqual(len(self.index.search("old")), 1)

        source.write_text("new replacement phrase", encoding="utf-8")
        self.index.index_file(source)

        self.assertEqual(self.index.search("old"), ())
        self.assertEqual(len(self.index.search("replacement")), 1)

    def test_remove_file_removes_full_text_entry(self):
        source = self.root / "remove-me.txt"
        source.write_text("temporary indexed content", encoding="utf-8")
        self.index.index_file(source)

        self.assertTrue(self.index.remove_file(source))
        self.assertEqual(self.index.search("temporary"), ())
        self.assertFalse(self.index.remove_file(source))

    def test_metadata_only_file_is_searchable_by_filename(self):
        source = self.root / "diagram-special.bin"
        source.write_bytes(b"\x00\x01")
        indexed = self.index.index_file(source, category="engineering")

        self.assertEqual(indexed.extraction_status, "metadata_only")
        self.assertEqual(self.index.search("diagram")[0].path, source)
        self.assertEqual(self.index.search("engineering")[0].path, source)

    def test_empty_or_punctuation_only_query_is_safe(self):
        self.assertEqual(self.index.search(""), ())
        self.assertEqual(self.index.search("!!! ---"), ())

    def test_result_limit_is_bounded(self):
        for number in range(3):
            source = self.root / f"bounded-{number}.txt"
            source.write_text("bounded result", encoding="utf-8")
            self.index.index_file(source)

        self.assertEqual(len(self.index.search("bounded", limit=2)), 2)
        self.assertEqual(
            len(self.index.search("bounded", limit=MAX_SEARCH_RESULTS + 500)),
            3,
        )

    def test_file_change_during_extraction_is_refused(self):
        source = self.root / "changing.txt"
        source.write_text("before", encoding="utf-8")

        def mutate(path, **_kwargs):
            path.write_text("after content is different", encoding="utf-8")
            return "before"

        with patch("app.search_index.extract_file_content", side_effect=mutate):
            with self.assertRaisesRegex(SearchIndexError, "changed while"):
                self.index.index_file(source)

        self.assertIsNone(self.index.get_file(source))

    def test_unrelated_sqlite_database_is_rejected_without_overwrite(self):
        other = self.root / "other.sqlite3"
        with closing(sqlite3.connect(other)) as connection:
            connection.execute("PRAGMA application_id = 123456")
            connection.execute("CREATE TABLE sentinel(value TEXT)")
            connection.execute("INSERT INTO sentinel VALUES ('keep')")
            connection.commit()

        with self.assertRaisesRegex(SearchIndexError, "another application"):
            SearchIndex(other)

        with closing(sqlite3.connect(other)) as connection:
            self.assertEqual(
                connection.execute("SELECT value FROM sentinel").fetchone()[0],
                "keep",
            )

    def test_relative_database_path_is_rejected(self):
        with self.assertRaisesRegex(SearchIndexError, "must be absolute"):
            SearchIndex(Path("relative-search.sqlite3"))


if __name__ == "__main__":
    unittest.main()
