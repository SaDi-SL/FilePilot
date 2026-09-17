import csv
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import hash_manager, mover
from app.stats import ensure_stats_file
from app.watcher import NewFileHandler


class DuplicateSafetyCharacterizationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.incoming = self.root / "incoming"
        self.documents = self.root / "organized" / "documents"
        self.incoming.mkdir()

        self.hash_db_file = self.root / "reports" / "hashes.json"
        self.stats_file = self.root / "reports" / "stats.json"
        self.history_file = self.root / "reports" / "history.csv"
        self.rules = {"documents": [".txt"]}
        self.destination_folders = {
            "documents": str(self.documents),
            "others": str(self.root / "organized" / "others"),
        }

        with hash_manager._cache_lock:
            self.original_cache = hash_manager._cache
            self.original_cache_path = hash_manager._cache_path
            hash_manager._cache = None
            hash_manager._cache_path = None

    def tearDown(self):
        with hash_manager._cache_lock:
            hash_manager._cache = self.original_cache
            hash_manager._cache_path = self.original_cache_path
        self.temp_dir.cleanup()

    def _source(self, name="incoming.txt", content="content A"):
        source = self.incoming / name
        source.write_text(content, encoding="utf-8")
        return source

    def _move(self, source, retries=1):
        return mover.move_file_with_retries(
            source_file=source,
            destination_folders=self.destination_folders,
            extension_lookup={".txt": "documents"},
            stats_file=str(self.stats_file),
            history_file=str(self.history_file),
            hash_db_file=str(self.hash_db_file),
            archive_by_date=False,
            rules=self.rules,
            retries=retries,
            delay=0,
            category_override="documents",
        )

    def _history(self):
        if not self.history_file.exists():
            return []
        with open(self.history_file, newline="", encoding="utf-8") as file:
            return list(csv.DictReader(file))

    def _stats(self):
        with open(self.stats_file, "r", encoding="utf-8") as file:
            return json.load(file)

    def _initialize_stats(self):
        ensure_stats_file(str(self.stats_file), self.rules)
        return self._stats()

    def _register_source_hash(self, source, indexed_path):
        file_hash = hash_manager.calculate_file_hash(source)
        hash_manager.register_file_hash(
            file_hash,
            str(indexed_path),
            str(self.hash_db_file),
        )
        return file_hash

    # Hash manager behavior

    def test_new_content_is_not_duplicate_and_returns_its_hash(self):
        source = self._source(content="new content")

        duplicate, file_hash = hash_manager.is_duplicate_file(
            source,
            str(self.hash_db_file),
        )

        self.assertFalse(duplicate)
        self.assertEqual(
            file_hash,
            hashlib.sha256(b"new content").hexdigest(),
        )

    def test_known_hash_is_duplicate_and_lookup_returns_recorded_path(self):
        source = self._source(content="same content")
        indexed_path = self.documents / "stored.txt"
        file_hash = self._register_source_hash(source, indexed_path)

        result = hash_manager.is_duplicate_file(source, str(self.hash_db_file))

        self.assertEqual(result, (True, file_hash))
        self.assertEqual(
            hash_manager.get_existing_file_path(
                file_hash,
                str(self.hash_db_file),
            ),
            str(indexed_path),
        )

    def test_current_stale_hash_is_treated_as_duplicate(self):
        """Legacy behavior: a missing indexed path is not revalidated."""
        source = self._source(content="only remaining copy")
        missing_path = self.documents / "missing.txt"
        file_hash = self._register_source_hash(source, missing_path)

        result = hash_manager.is_duplicate_file(source, str(self.hash_db_file))

        self.assertFalse(missing_path.exists())
        self.assertEqual(result, (True, file_hash))

    def test_current_changed_indexed_file_is_not_revalidated(self):
        """Legacy behavior: only the incoming hash and index key are checked."""
        source = self._source(content="content A")
        indexed_path = self.documents / "indexed.txt"
        indexed_path.parent.mkdir(parents=True)
        indexed_path.write_text("content A", encoding="utf-8")
        file_hash = self._register_source_hash(source, indexed_path)
        indexed_path.write_text("content B", encoding="utf-8")

        result = hash_manager.is_duplicate_file(source, str(self.hash_db_file))

        self.assertEqual(result, (True, file_hash))
        self.assertNotEqual(
            hash_manager.calculate_file_hash(indexed_path),
            file_hash,
        )

    def test_register_file_hash_updates_memory_and_disk(self):
        source = self._source(content="registered content")
        indexed_path = self.documents / "registered.txt"
        file_hash = hash_manager.calculate_file_hash(source)

        hash_manager.register_file_hash(
            file_hash,
            str(indexed_path),
            str(self.hash_db_file),
        )

        expected = {file_hash: str(indexed_path)}
        self.assertEqual(
            hash_manager.load_hash_db(str(self.hash_db_file)),
            expected,
        )
        with open(self.hash_db_file, "r", encoding="utf-8") as file:
            self.assertEqual(json.load(file), expected)

    def test_current_same_path_load_uses_cache_until_another_db_is_loaded(self):
        """There is no public reset; a path switch is what forces a disk reload."""
        source = self._source(content="cached content")
        indexed_path = self.documents / "cached.txt"
        file_hash = self._register_source_hash(source, indexed_path)
        externally_written = {"external": "changed-on-disk.txt"}
        self.hash_db_file.write_text(
            json.dumps(externally_written),
            encoding="utf-8",
        )

        cached = hash_manager.load_hash_db(str(self.hash_db_file))

        other_db = self.root / "reports" / "other-hashes.json"
        hash_manager.load_hash_db(str(other_db))
        reloaded = hash_manager.load_hash_db(str(self.hash_db_file))

        self.assertEqual(cached, {file_hash: str(indexed_path)})
        self.assertEqual(reloaded, externally_written)

    # Mover behavior

    def test_successful_move_removes_source_preserves_content_and_returns_none(self):
        source = self._source(content="move me")

        result = self._move(source)

        destination = self.documents / source.name
        self.assertIsNone(result)
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_text(encoding="utf-8"), "move me")
        self.assertEqual(self._history()[0]["status"], "moved")
        self.assertEqual(self._stats()["total_files"], 1)
        file_hash = hashlib.sha256(b"move me").hexdigest()
        self.assertEqual(
            hash_manager.get_existing_file_path(
                file_hash,
                str(self.hash_db_file),
            ),
            str(destination),
        )

    def test_current_verified_duplicate_source_is_permanently_deleted(self):
        """Legacy behavior: even a verified duplicate is handled by unlink."""
        source = self._source(content="duplicate content")
        indexed_path = self.documents / "existing.txt"
        indexed_path.parent.mkdir(parents=True)
        indexed_path.write_text("duplicate content", encoding="utf-8")
        file_hash = self._register_source_hash(source, indexed_path)
        initial_stats = self._initialize_stats()

        result = self._move(source)

        self.assertIsNone(result)
        self.assertFalse(source.exists())
        self.assertEqual(
            indexed_path.read_text(encoding="utf-8"),
            "duplicate content",
        )
        self.assertEqual(self._history()[0]["status"], "duplicate_skipped")
        self.assertEqual(self._stats(), initial_stats)
        self.assertEqual(
            hash_manager.get_existing_file_path(
                file_hash,
                str(self.hash_db_file),
            ),
            str(indexed_path),
        )

    def test_current_stale_hash_missing_destination_deletes_only_valid_copy(self):
        """Unsafe legacy behavior captured for replacement in Patch 4B."""
        source = self._source(content="only valid copy")
        missing_path = self.documents / "missing.txt"
        self._register_source_hash(source, missing_path)
        initial_stats = self._initialize_stats()

        result = self._move(source)

        self.assertIsNone(result)
        self.assertFalse(source.exists())
        self.assertFalse(missing_path.exists())
        self.assertEqual(self._history()[0]["status"], "duplicate_skipped")
        self.assertEqual(self._stats(), initial_stats)

    def test_current_changed_indexed_destination_deletes_incoming_original(self):
        """Unsafe legacy behavior: changed destination content is not verified."""
        source = self._source(content="content A")
        indexed_path = self.documents / "indexed.txt"
        indexed_path.parent.mkdir(parents=True)
        indexed_path.write_text("content A", encoding="utf-8")
        self._register_source_hash(source, indexed_path)
        indexed_path.write_text("content B", encoding="utf-8")
        initial_stats = self._initialize_stats()

        result = self._move(source)

        self.assertIsNone(result)
        self.assertFalse(source.exists())
        self.assertEqual(indexed_path.read_text(encoding="utf-8"), "content B")
        self.assertEqual(self._history()[0]["status"], "duplicate_skipped")
        self.assertEqual(self._stats(), initial_stats)

    def test_hash_calculation_failure_preserves_source_and_returns_none(self):
        source = self._source(content="must remain")
        initial_stats = self._initialize_stats()

        with patch(
            "app.mover.is_duplicate_file",
            side_effect=OSError("simulated hash failure"),
        ):
            result = self._move(source)

        self.assertIsNone(result)
        self.assertTrue(source.exists())
        self.assertEqual(source.read_text(encoding="utf-8"), "must remain")
        self.assertEqual(self._history()[0]["status"], "hash_check_failed")
        self.assertEqual(self._stats(), initial_stats)

    def test_move_failure_preserves_source_records_failure_and_returns_none(self):
        source = self._source(content="must remain")

        with (
            patch(
                "app.mover.shutil.move",
                side_effect=OSError("simulated move failure"),
            ) as move_call,
            patch("app.mover.time.sleep"),
        ):
            result = self._move(source, retries=2)

        self.assertIsNone(result)
        self.assertEqual(move_call.call_count, 2)
        self.assertTrue(source.exists())
        self.assertEqual(source.read_text(encoding="utf-8"), "must remain")
        self.assertEqual(self._history()[0]["status"], "failed")
        self.assertEqual(self._stats()["failed"], 1)
        self.assertEqual(
            hash_manager.load_hash_db(str(self.hash_db_file)),
            {},
        )

    # Watcher propagation behavior

    def _watcher(self, callback):
        config = {
            "destination_folders": self.destination_folders,
            "rules": self.rules,
            "processing_wait_seconds": 0,
            "duplicate_event_window_seconds": 3,
            "archive_by_date": False,
            "stats_file": str(self.stats_file),
            "history_file": str(self.history_file),
            "hash_db_file": str(self.hash_db_file),
        }
        return NewFileHandler(
            config,
            {".txt": "documents"},
            file_processed_callback=callback,
        )

    def _run_watcher_with_mover(self, mover_side_effect):
        source = self._source(content="watch me")
        callback = MagicMock()
        handler = self._watcher(callback)
        with (
            patch("app.watcher.smart_classify", return_value=None),
            patch("app.watcher.time.sleep"),
            patch(
                "app.watcher.move_file_with_retries",
                side_effect=mover_side_effect,
            ),
        ):
            handler._process_file_thread(str(source), "created")
        return source, handler, callback

    def test_watcher_reports_successful_move_as_moved(self):
        def successful_move(**kwargs):
            self.documents.mkdir(parents=True, exist_ok=True)
            kwargs["source_file"].replace(self.documents / kwargs["source_file"].name)

        source, handler, callback = self._run_watcher_with_mover(successful_move)

        self.assertFalse(source.exists())
        self.assertEqual(handler.last_processed_file, source.name)
        callback.assert_called_once_with(source.name, "documents", "moved")

    def test_current_watcher_reports_duplicate_outcome_as_moved(self):
        """Legacy behavior: the mover's duplicate None return is reported as moved."""
        def duplicate_outcome(**kwargs):
            kwargs["source_file"].unlink()

        source, _, callback = self._run_watcher_with_mover(duplicate_outcome)

        self.assertFalse(source.exists())
        callback.assert_called_once_with(source.name, "documents", "moved")

    def test_current_watcher_reports_exhausted_move_failure_as_moved(self):
        """Legacy behavior: the mover's failure None return is reported as moved."""
        source, _, callback = self._run_watcher_with_mover(lambda **kwargs: None)

        self.assertTrue(source.exists())
        callback.assert_called_once_with(source.name, "documents", "moved")

    # Concurrency behavior

    def test_current_concurrent_equal_content_can_both_move_before_registration(self):
        """A barrier exposes the non-atomic duplicate-check/register sequence."""
        first = self._source("first.txt", "equal content")
        second = self._source("second.txt", "equal content")
        barrier = threading.Barrier(2)
        real_duplicate_check = hash_manager.is_duplicate_file
        errors = []

        def synchronized_duplicate_check(file_path, hash_db_file):
            result = real_duplicate_check(file_path, hash_db_file)
            barrier.wait(timeout=5)
            return result

        def move_in_thread(source):
            try:
                self._move(source)
            except Exception as error:
                errors.append(error)

        with (
            patch(
                "app.mover.is_duplicate_file",
                side_effect=synchronized_duplicate_check,
            ),
            patch("app.mover.append_history"),
            patch("app.mover.update_stats"),
        ):
            threads = [
                threading.Thread(target=move_in_thread, args=(source,))
                for source in (first, second)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        destinations = {
            self.documents / first.name,
            self.documents / second.name,
        }
        self.assertTrue(all(path.exists() for path in destinations))
        file_hash = hashlib.sha256(b"equal content").hexdigest()
        self.assertIn(
            Path(hash_manager.load_hash_db(str(self.hash_db_file))[file_hash]),
            destinations,
        )


if __name__ == "__main__":
    unittest.main()
