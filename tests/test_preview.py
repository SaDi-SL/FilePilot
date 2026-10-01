import errno
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app import hash_manager, mover
from app.operation_journal import OperationJournal


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.incoming = self.root / "incoming"
        self.organized = self.root / "organized"
        self.documents = self.organized / "documents"
        self.incoming.mkdir()
        self.organized.mkdir()
        self.hash_db = self.root / "data" / "hashes.json"
        self.history = self.root / "data" / "history.csv"
        self.stats = self.root / "data" / "stats.json"
        self.journal = OperationJournal(self.root / "data" / "operations.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _source(self, content="preview content"):
        source = self.incoming / "report.txt"
        source.write_text(content, encoding="utf-8")
        return source

    def _preview(self, source, **overrides):
        arguments = {
            "source_file": source,
            "destination_folders": {
                "documents": str(self.documents),
                "others": str(self.organized / "others"),
            },
            "extension_lookup": {".txt": "documents"},
            "hash_db_file": str(self.hash_db),
            "archive_by_date": False,
            "organized_root": self.organized,
            "category_override": "documents",
            "classification_source": "extension",
        }
        arguments.update(overrides)
        return mover.preview_move(**arguments)

    def _move(self, source):
        return mover.move_file_with_retries(
            source,
            {"documents": str(self.documents), "others": str(self.organized / "others")},
            {".txt": "documents"},
            str(self.stats),
            str(self.history),
            str(self.hash_db),
            False,
            {"documents": [".txt"]},
            self.organized,
            retries=1,
            delay=0,
            category_override="documents",
            journal=self.journal,
        )

    @contextmanager
    def _cross_device(self, source):
        primitive = "rename" if os.name == "nt" else "link"
        real = getattr(mover.os, primitive)

        def fail(path, destination, *args, **kwargs):
            if Path(path) == source and Path(destination).is_relative_to(self.organized):
                raise OSError(errno.EXDEV, "simulated cross-volume")
            return real(path, destination, *args, **kwargs)

        with patch.object(mover.os, primitive, side_effect=fail):
            yield

    def test_normal_same_volume_preview(self):
        result = self._preview(self._source())
        self.assertIs(result.status, mover.PreviewStatus.READY)
        self.assertEqual(result.proposed_destination, self.documents / "report.txt")
        self.assertTrue(result.execution_possible)

    def test_normal_cross_volume_preview_is_storage_agnostic(self):
        source = self._source()
        with self._cross_device(source):
            result = self._preview(source)
        self.assertIs(result.status, mover.PreviewStatus.READY)
        self.assertEqual(result.proposed_destination, self.documents / "report.txt")

    def test_preview_uses_category_and_classification_source(self):
        result = self._preview(self._source())
        self.assertEqual(result.category, "documents")
        self.assertEqual(result.classification_source, "extension")

    def test_collision_is_advisory_and_uses_alternative_name(self):
        self.documents.mkdir(parents=True)
        (self.documents / "report.txt").write_text("occupied", encoding="utf-8")
        result = self._preview(self._source())
        self.assertTrue(result.destination_collision)
        self.assertTrue(result.alternative_name_required)
        self.assertEqual(result.proposed_destination, self.documents / "report(1).txt")

    def test_verified_duplicate_is_reported_without_index_mutation(self):
        source = self._source()
        existing = self.organized / "existing.txt"
        existing.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        self.hash_db.parent.mkdir(exist_ok=True)
        digest = hash_manager.calculate_file_hash(source)
        self.hash_db.write_text(json.dumps({digest: str(existing)}), encoding="utf-8")
        before = self.hash_db.read_bytes()
        result = self._preview(source)
        self.assertIs(result.status, mover.PreviewStatus.DUPLICATE)
        self.assertEqual(result.duplicate_of, existing)
        self.assertEqual(self.hash_db.read_bytes(), before)

    def test_unreadable_duplicate_evidence_is_explicit_and_non_mutating(self):
        source = self._source()
        self.hash_db.parent.mkdir(exist_ok=True)
        self.hash_db.write_text("not-json", encoding="utf-8")
        before = self.hash_db.read_bytes()
        result = self._preview(source)
        self.assertIs(result.status, mover.PreviewStatus.UNSAFE)
        self.assertIs(result.duplicate_status, mover.DuplicateStatus.UNKNOWN)
        self.assertFalse(result.execution_possible)
        self.assertEqual(self.hash_db.read_bytes(), before)

    def test_hash_failure_is_explicit_and_non_mutating(self):
        source = self._source()
        with patch.object(mover, "calculate_file_hash", side_effect=OSError("unreadable")):
            result = self._preview(source)
        self.assertIs(result.status, mover.PreviewStatus.HASH_FAILED)
        self.assertFalse(result.execution_possible)
        self.assertTrue(source.exists())

    def test_unsafe_category_is_rejected_without_creating_paths(self):
        result = self._preview(self._source(), category_override="../escape")
        self.assertIs(result.status, mover.PreviewStatus.UNSAFE)
        self.assertFalse((self.root / "escape").exists())

    def test_missing_source_is_explicit(self):
        result = self._preview(self.incoming / "missing.txt")
        self.assertIs(result.status, mover.PreviewStatus.SOURCE_MISSING)
        self.assertFalse(result.execution_possible)

    def test_preview_performs_zero_physical_mutations(self):
        source = self._source()
        before = source.read_bytes()
        self._preview(source)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(self.documents.exists())

    def test_preview_does_not_create_or_modify_metadata(self):
        self._preview(self._source())
        self.assertFalse(self.hash_db.exists())
        self.assertFalse(self.history.exists())
        self.assertFalse(self.stats.exists())

    def test_preview_creates_no_journal_operation(self):
        self._preview(self._source())
        self.assertEqual(self.journal.list_incomplete_operations(), [])

    def test_collision_after_preview_remains_no_clobber(self):
        source = self._source()
        preview = self._preview(source)
        self.documents.mkdir(parents=True)
        preview.proposed_destination.write_text("late collision", encoding="utf-8")
        result = self._move(source)
        self.assertEqual(preview.proposed_destination.read_text(encoding="utf-8"), "late collision")
        self.assertEqual(result.destination, self.documents / "report(1).txt")

    def test_preview_and_execution_share_planning_semantics(self):
        source = self._source()
        preview = self._preview(source)
        result = self._move(source)
        self.assertEqual(result.destination, preview.proposed_destination)


if __name__ == "__main__":
    unittest.main()
