import csv
import hashlib
import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import hash_manager, mover
from app.main import build_destination_folders, ensure_directories
from app.stats import ensure_stats_file
from app.watcher import NewFileHandler


class DuplicateSafetyRegressionTests(unittest.TestCase):
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
            organized_root=self.root / "organized",
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

    def _run_synchronized_destination_race(self, sources):
        """Let every mover select its destination before any mover can commit."""
        self._initialize_stats()
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.history_file.write_text(
            "timestamp,filename,category,status,classification_method,smart_source\n",
            encoding="utf-8",
        )
        selection_barrier = threading.Barrier(len(sources))
        selected_destinations = []
        results = [None] * len(sources)
        errors = []
        result_lock = threading.Lock()
        selection_state = threading.local()
        real_generate = mover.generate_unique_destination

        def synchronized_generate(destination_path):
            selected = real_generate(destination_path)
            if not getattr(selection_state, "initial_selection_complete", False):
                selection_state.initial_selection_complete = True
                with result_lock:
                    selected_destinations.append(selected)
                selection_barrier.wait(timeout=5)
            return selected

        def move_source(index, source):
            try:
                result = self._move(source)
                with result_lock:
                    results[index] = result
            except Exception as error:
                with result_lock:
                    errors.append(error)

        with patch(
            "app.mover.generate_unique_destination",
            side_effect=synchronized_generate,
        ):
            threads = [
                threading.Thread(
                    target=move_source,
                    args=(index, source),
                    name=f"destination-race-{index}",
                )
                for index, source in enumerate(sources)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertTrue(all(result is not None for result in results))
        return results, selected_destinations

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
        indexed_path.parent.mkdir(parents=True)
        indexed_path.write_text("same content", encoding="utf-8")
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

    def test_stale_hash_is_not_duplicate_and_is_removed(self):
        source = self._source(content="only remaining copy")
        missing_path = self.documents / "missing.txt"
        file_hash = self._register_source_hash(source, missing_path)

        result = hash_manager.is_duplicate_file(source, str(self.hash_db_file))

        self.assertFalse(missing_path.exists())
        self.assertEqual(result, (False, file_hash))
        self.assertNotIn(
            file_hash,
            hash_manager.load_hash_db(str(self.hash_db_file)),
        )

    def test_changed_indexed_file_is_not_duplicate_and_is_removed(self):
        source = self._source(content="content A")
        indexed_path = self.documents / "indexed.txt"
        indexed_path.parent.mkdir(parents=True)
        indexed_path.write_text("content A", encoding="utf-8")
        file_hash = self._register_source_hash(source, indexed_path)
        indexed_path.write_text("content B", encoding="utf-8")

        result = hash_manager.is_duplicate_file(source, str(self.hash_db_file))

        self.assertEqual(result, (False, file_hash))
        self.assertNotEqual(
            hash_manager.calculate_file_hash(indexed_path),
            file_hash,
        )
        self.assertNotIn(
            file_hash,
            hash_manager.load_hash_db(str(self.hash_db_file)),
        )

    def test_unverifiable_indexed_file_is_not_duplicate_and_is_removed(self):
        source = self._source(content="content A")
        indexed_path = self.documents / "indexed.txt"
        indexed_path.parent.mkdir(parents=True)
        indexed_path.write_text("content A", encoding="utf-8")
        file_hash = self._register_source_hash(source, indexed_path)

        with patch(
            "app.hash_manager.calculate_file_hash",
            side_effect=PermissionError("simulated unreadable destination"),
        ):
            existing_path = hash_manager.get_verified_file_path(
                file_hash,
                str(self.hash_db_file),
            )

        self.assertIsNone(existing_path)
        self.assertNotIn(
            file_hash,
            hash_manager.load_hash_db(str(self.hash_db_file)),
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

    def test_successful_move_removes_source_preserves_content_and_returns_result(self):
        source = self._source(content="move me")

        result = self._move(source)

        destination = self.documents / source.name
        self.assertEqual(result.status, mover.MoveStatus.MOVED)
        self.assertEqual(result.source, source)
        self.assertEqual(result.destination, destination)
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

    def test_verified_duplicate_source_is_retained(self):
        source = self._source(content="duplicate content")
        indexed_path = self.documents / "existing.txt"
        indexed_path.parent.mkdir(parents=True)
        indexed_path.write_text("duplicate content", encoding="utf-8")
        file_hash = self._register_source_hash(source, indexed_path)
        initial_stats = self._initialize_stats()

        result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.DUPLICATE)
        self.assertEqual(result.duplicate_of, indexed_path)
        self.assertTrue(source.exists())
        self.assertEqual(source.read_text(encoding="utf-8"), "duplicate content")
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

    def test_stale_hash_missing_destination_moves_incoming_file(self):
        source = self._source(content="only valid copy")
        missing_path = self.documents / "missing.txt"
        file_hash = self._register_source_hash(source, missing_path)
        initial_stats = self._initialize_stats()

        result = self._move(source)

        destination = self.documents / source.name
        self.assertEqual(result.status, mover.MoveStatus.MOVED)
        self.assertEqual(result.destination, destination)
        self.assertFalse(source.exists())
        self.assertFalse(missing_path.exists())
        self.assertEqual(destination.read_text(encoding="utf-8"), "only valid copy")
        self.assertEqual(self._history()[0]["status"], "moved")
        self.assertEqual(self._stats()["total_files"], initial_stats["total_files"] + 1)
        self.assertEqual(
            hash_manager.get_existing_file_path(file_hash, str(self.hash_db_file)),
            str(destination),
        )

    def test_changed_indexed_destination_does_not_prove_duplication(self):
        source = self._source(content="content A")
        indexed_path = self.documents / "indexed.txt"
        indexed_path.parent.mkdir(parents=True)
        indexed_path.write_text("content A", encoding="utf-8")
        self._register_source_hash(source, indexed_path)
        indexed_path.write_text("content B", encoding="utf-8")
        initial_stats = self._initialize_stats()

        result = self._move(source)

        destination = self.documents / source.name
        self.assertEqual(result.status, mover.MoveStatus.MOVED)
        self.assertFalse(source.exists())
        self.assertEqual(indexed_path.read_text(encoding="utf-8"), "content B")
        self.assertEqual(destination.read_text(encoding="utf-8"), "content A")
        self.assertEqual(self._history()[0]["status"], "moved")
        self.assertEqual(self._stats()["total_files"], initial_stats["total_files"] + 1)

    def test_unverifiable_indexed_destination_does_not_prove_duplication(self):
        source = self._source(content="content A")
        indexed_path = self.documents / "indexed.txt"
        indexed_path.parent.mkdir(parents=True)
        indexed_path.write_text("content A", encoding="utf-8")
        self._register_source_hash(source, indexed_path)
        real_calculate_file_hash = hash_manager.calculate_file_hash

        def fail_for_indexed_path(file_path, chunk_size=65536):
            if Path(file_path) == indexed_path:
                raise PermissionError("simulated unreadable destination")
            return real_calculate_file_hash(file_path, chunk_size)

        with patch(
            "app.hash_manager.calculate_file_hash",
            side_effect=fail_for_indexed_path,
        ):
            result = self._move(source)

        destination = self.documents / source.name
        self.assertEqual(result.status, mover.MoveStatus.MOVED)
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_text(encoding="utf-8"), "content A")
        self.assertEqual(indexed_path.read_text(encoding="utf-8"), "content A")

    def test_hash_calculation_failure_preserves_source_and_returns_result(self):
        source = self._source(content="must remain")
        initial_stats = self._initialize_stats()

        with patch(
            "app.mover.calculate_file_hash",
            side_effect=OSError("simulated hash failure"),
        ):
            result = self._move(source)

        self.assertEqual(result.status, mover.MoveStatus.HASH_CHECK_FAILED)
        self.assertIn("simulated hash failure", result.error)
        self.assertTrue(source.exists())
        self.assertEqual(source.read_text(encoding="utf-8"), "must remain")
        self.assertEqual(self._history()[0]["status"], "hash_check_failed")
        self.assertEqual(self._stats(), initial_stats)

    def test_move_failure_preserves_source_records_failure_and_returns_result(self):
        source = self._source(content="must remain")

        with (
            patch(
                "app.mover._move_no_clobber",
                side_effect=OSError("simulated move failure"),
            ) as move_call,
            patch("app.mover.time.sleep"),
        ):
            result = self._move(source, retries=2)

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIn("simulated move failure", result.error)
        self.assertEqual(move_call.call_count, 2)
        self.assertTrue(source.exists())
        self.assertEqual(source.read_text(encoding="utf-8"), "must remain")
        self.assertEqual(self._history()[0]["status"], "failed")
        self.assertEqual(self._stats()["failed"], 1)
        self.assertEqual(
            hash_manager.load_hash_db(str(self.hash_db_file)),
            {},
        )

    # Patch 5C-B1: atomic no-clobber destination regression coverage

    def test_concurrent_different_content_same_name_commits_without_clobber(self):
        first_dir = self.root / "source-a"
        second_dir = self.root / "source-b"
        first_dir.mkdir()
        second_dir.mkdir()
        first = first_dir / "name.txt"
        second = second_dir / "name.txt"
        first.write_text("content A", encoding="utf-8")
        second.write_text("content B", encoding="utf-8")

        results, selected = self._run_synchronized_destination_race(
            [first, second]
        )

        destination = self.documents / "name.txt"
        self.assertEqual(selected, [destination, destination])
        self.assertEqual(
            [result.status for result in results],
            [mover.MoveStatus.MOVED, mover.MoveStatus.MOVED],
        )
        committed_destinations = [result.destination for result in results]
        self.assertEqual(len(set(committed_destinations)), 2)
        self.assertCountEqual(
            [path.name for path in committed_destinations],
            ["name.txt", "name(1).txt"],
        )
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertCountEqual(
            [path.name for path in self.documents.iterdir()],
            ["name.txt", "name(1).txt"],
        )
        self.assertEqual(
            results[0].destination.read_text(encoding="utf-8"),
            "content A",
        )
        self.assertEqual(
            results[1].destination.read_text(encoding="utf-8"),
            "content B",
        )

        first_hash = hashlib.sha256(b"content A").hexdigest()
        second_hash = hashlib.sha256(b"content B").hexdigest()
        hash_db = hash_manager.load_hash_db(str(self.hash_db_file))
        self.assertEqual(hash_db[first_hash], str(results[0].destination))
        self.assertEqual(hash_db[second_hash], str(results[1].destination))
        self.assertEqual(
            hash_manager.calculate_file_hash(Path(hash_db[first_hash])),
            first_hash,
        )
        self.assertEqual(
            hash_manager.calculate_file_hash(Path(hash_db[second_hash])),
            second_hash,
        )
        self.assertEqual(len(self._history()), 2)
        self.assertEqual(
            [entry["status"] for entry in self._history()],
            ["moved", "moved"],
        )
        self.assertEqual(self._stats()["total_files"], 2)

    def test_concurrent_alternative_name_collision_commits_distinct_files(self):
        self.documents.mkdir(parents=True)
        original = self.documents / "name.txt"
        original.write_text("pre-existing", encoding="utf-8")
        first_dir = self.root / "source-a"
        second_dir = self.root / "source-b"
        first_dir.mkdir()
        second_dir.mkdir()
        first = first_dir / "name.txt"
        second = second_dir / "name.txt"
        first.write_text("content A", encoding="utf-8")
        second.write_text("content B", encoding="utf-8")

        results, selected = self._run_synchronized_destination_race(
            [first, second]
        )

        alternative = self.documents / "name(1).txt"
        self.assertEqual(selected, [alternative, alternative])
        self.assertEqual(
            [result.status for result in results],
            [mover.MoveStatus.MOVED, mover.MoveStatus.MOVED],
        )
        committed_destinations = [result.destination for result in results]
        self.assertEqual(len(set(committed_destinations)), 2)
        self.assertCountEqual(
            [path.name for path in committed_destinations],
            ["name(1).txt", "name(2).txt"],
        )
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertEqual(original.read_text(encoding="utf-8"), "pre-existing")
        self.assertEqual(
            results[0].destination.read_text(encoding="utf-8"),
            "content A",
        )
        self.assertEqual(
            results[1].destination.read_text(encoding="utf-8"),
            "content B",
        )
        self.assertCountEqual(
            [path.name for path in self.documents.iterdir()],
            ["name.txt", "name(1).txt", "name(2).txt"],
        )
        first_hash = hashlib.sha256(b"content A").hexdigest()
        second_hash = hashlib.sha256(b"content B").hexdigest()
        hash_db = hash_manager.load_hash_db(str(self.hash_db_file))
        self.assertEqual(hash_db[first_hash], str(results[0].destination))
        self.assertEqual(hash_db[second_hash], str(results[1].destination))
        self.assertEqual(
            hash_manager.calculate_file_hash(Path(hash_db[first_hash])),
            first_hash,
        )
        self.assertEqual(
            hash_manager.calculate_file_hash(Path(hash_db[second_hash])),
            second_hash,
        )
        self.assertEqual(len(self._history()), 2)
        self.assertEqual(self._stats()["total_files"], 2)

    def test_external_destination_toctou_preserves_sentinel(self):
        source = self._source("name.txt", "incoming bytes")
        sentinel_bytes = b"external sentinel"
        selected_destination = []
        real_generate = mover.generate_unique_destination

        def create_sentinel_after_selection(destination_path):
            selected = real_generate(destination_path)
            if not selected_destination:
                self.assertFalse(selected.exists())
                selected.write_bytes(sentinel_bytes)
                selected_destination.append(selected)
            return selected

        with patch(
            "app.mover.generate_unique_destination",
            side_effect=create_sentinel_after_selection,
        ):
            result = self._move(source)

        destination = self.documents / "name.txt"
        committed_destination = self.documents / "name(1).txt"
        self.assertEqual(selected_destination, [destination])
        self.assertEqual(result.status, mover.MoveStatus.MOVED)
        self.assertEqual(result.destination, committed_destination)
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_bytes(), sentinel_bytes)
        self.assertEqual(committed_destination.read_bytes(), b"incoming bytes")
        incoming_hash = hashlib.sha256(b"incoming bytes").hexdigest()
        hash_db = hash_manager.load_hash_db(str(self.hash_db_file))
        self.assertEqual(hash_db, {incoming_hash: str(committed_destination)})
        self.assertEqual(
            hash_manager.calculate_file_hash(Path(hash_db[incoming_hash])),
            incoming_hash,
        )
        self.assertEqual(self._history()[0]["status"], "moved")
        self.assertEqual(self._stats()["total_files"], 1)

    def test_unsafe_current_behavior_hash_registration_failure_follows_committed_move(self):
        source = self._source(content="committed bytes")

        with patch(
            "app.mover.register_file_hash",
            side_effect=OSError("simulated registration failure"),
        ):
            result = self._move(source)

        destination = self.documents / source.name
        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertEqual(result.destination, destination)
        self.assertIn("simulated registration failure", result.error)
        self.assertFalse(source.exists())
        self.assertTrue(destination.exists())
        self.assertEqual(destination.read_text(encoding="utf-8"), "committed bytes")
        self.assertEqual(
            hash_manager.load_hash_db(str(self.hash_db_file)),
            {},
        )
        self.assertEqual(self._history()[0]["status"], "failed")
        self.assertEqual(self._stats()["total_files"], 0)
        self.assertEqual(self._stats()["failed"], 1)

    def test_unsafe_current_behavior_source_mutation_after_hash_registers_wrong_digest(self):
        source = self._source(content="content A")
        original_hash = hashlib.sha256(b"content A").hexdigest()
        replacement_hash = hashlib.sha256(b"content B").hexdigest()
        real_calculate = mover.calculate_file_hash

        def hash_then_mutate(file_path):
            calculated = real_calculate(file_path)
            Path(file_path).write_text("content B", encoding="utf-8")
            return calculated

        with patch(
            "app.mover.calculate_file_hash",
            side_effect=hash_then_mutate,
        ):
            result = self._move(source)

        destination = self.documents / source.name
        self.assertEqual(result.status, mover.MoveStatus.MOVED)
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_text(encoding="utf-8"), "content B")
        self.assertEqual(hash_manager.calculate_file_hash(destination), replacement_hash)
        hash_db = hash_manager.load_hash_db(str(self.hash_db_file))
        self.assertEqual(hash_db, {original_hash: str(destination)})
        self.assertNotEqual(original_hash, replacement_hash)

    def test_configured_parent_category_is_rejected(self):
        organized = self.root / "organized"
        rules = {"../escaped": [".txt"]}
        destination_folders = {
            category: str(Path(path).resolve())
            for category, path in build_destination_folders(
                str(organized),
                rules,
            ).items()
        }
        source = self._source("configured.txt", "configured escape")

        result = mover.move_file_with_retries(
            source_file=source,
            destination_folders=destination_folders,
            extension_lookup={".txt": "../escaped"},
            stats_file=str(self.stats_file),
            history_file=str(self.history_file),
            hash_db_file=str(self.hash_db_file),
            archive_by_date=False,
            rules=rules,
            organized_root=organized,
            retries=1,
            delay=0,
            category_override="../escaped",
        )

        escaped_destination = self.root / "escaped" / source.name
        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIsNone(result.destination)
        self.assertIn("Unsafe category path", result.error)
        self.assertTrue(source.exists())
        self.assertEqual(source.read_text(encoding="utf-8"), "configured escape")
        self.assertFalse(escaped_destination.exists())
        self.assertEqual(self._history()[0]["status"], "failed")
        self.assertEqual(self._stats()["total_files"], 0)
        self.assertEqual(self._stats()["failed"], 1)

    def test_dynamic_smart_parent_category_is_rejected(self):
        source = self._source("smart.txt", "smart escape")
        callback = MagicMock()
        handler = self._watcher(callback)

        with (
            patch.object(handler, "_wait_until_stable", return_value=True),
            patch("app.watcher.smart_classify", return_value="../smart-escaped"),
        ):
            handler._process_file_thread(str(source), "created")

        escaped_destination = self.root / "smart-escaped" / source.name
        self.assertTrue(source.exists())
        self.assertEqual(source.read_text(encoding="utf-8"), "smart escape")
        self.assertFalse(escaped_destination.exists())
        self.assertEqual(self._history()[0]["status"], "failed")
        self.assertEqual(self._stats()["total_files"], 0)
        self.assertEqual(self._stats()["failed"], 1)
        callback.assert_called_once_with(source.name, "../smart-escaped", "failed")

    def test_dynamic_absolute_plugin_category_is_rejected(self):
        source = self._source("plugin.txt", "plugin escape")
        absolute_destination = (self.root / "absolute-escaped").resolve()
        plugin_manager = MagicMock()
        plugin_manager.classify_with_plugins.return_value = str(absolute_destination)
        callback = MagicMock()
        handler = NewFileHandler(
            {
                "destination_folders": self.destination_folders,
                "rules": self.rules,
                "processing_wait_seconds": 0,
                "duplicate_event_window_seconds": 3,
                "archive_by_date": False,
                "stats_file": str(self.stats_file),
                "history_file": str(self.history_file),
                "hash_db_file": str(self.hash_db_file),
            },
            {".txt": "documents"},
            plugin_manager=plugin_manager,
            file_processed_callback=callback,
        )

        with (
            patch.object(handler, "_wait_until_stable", return_value=True),
            patch("app.watcher.smart_classify", return_value=None),
        ):
            handler._process_file_thread(str(source), "created")

        escaped_file = absolute_destination / source.name
        self.assertTrue(source.exists())
        self.assertEqual(source.read_text(encoding="utf-8"), "plugin escape")
        self.assertFalse(escaped_file.exists())
        self.assertEqual(self._history()[0]["status"], "failed")
        self.assertEqual(self._stats()["total_files"], 0)
        self.assertEqual(self._stats()["failed"], 1)
        callback.assert_called_once_with(
            source.name,
            str(absolute_destination),
            "failed",
        )

    def test_backslash_parent_category_is_rejected(self):
        organized = self.root / "organized"
        category = r"..\backslash-escaped"
        destination_folders = dict(self.destination_folders)
        destination_folders[category] = str(organized / category)
        source = self._source("backslash.txt", "backslash escape")

        result = mover.move_file_with_retries(
            source_file=source,
            destination_folders=destination_folders,
            extension_lookup={".txt": category},
            stats_file=str(self.stats_file),
            history_file=str(self.history_file),
            hash_db_file=str(self.hash_db_file),
            archive_by_date=False,
            rules={category: [".txt"]},
            organized_root=organized,
            retries=1,
            delay=0,
            category_override=category,
        )

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIn("Unsafe category path", result.error)
        self.assertTrue(source.exists())
        self.assertEqual(source.read_text(encoding="utf-8"), "backslash escape")
        self.assertFalse((self.root / "backslash-escaped").exists())

    def test_destination_mapping_outside_organized_root_is_rejected(self):
        organized = self.root / "organized"
        outside = self.root / "mapped-outside"
        source = self._source("mapped.txt", "mapped escape")
        destination_folders = {
            "documents": str(outside),
            "others": str(organized / "others"),
        }

        result = mover.move_file_with_retries(
            source_file=source,
            destination_folders=destination_folders,
            extension_lookup={".txt": "documents"},
            stats_file=str(self.stats_file),
            history_file=str(self.history_file),
            hash_db_file=str(self.hash_db_file),
            archive_by_date=False,
            rules=self.rules,
            organized_root=organized,
            retries=1,
            delay=0,
            category_override="documents",
        )

        self.assertEqual(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIn("Destination escapes organized root", result.error)
        self.assertTrue(source.exists())
        self.assertEqual(source.read_text(encoding="utf-8"), "mapped escape")
        self.assertFalse(outside.exists())

    def test_directory_initialization_rejects_outside_destination(self):
        organized = self.root / "organized"
        outside = self.root / "startup-escaped"
        config = {
            "organized_base_folder": str(organized),
            "watch_folders": [],
            "destination_folders": {
                "documents": str(outside),
                "others": str(organized / "others"),
            },
            "log_file": str(self.root / "reports" / "app.log"),
            "stats_file": str(self.stats_file),
            "history_file": str(self.history_file),
            "hash_db_file": str(self.hash_db_file),
        }

        with self.assertRaisesRegex(
            mover.UnsafeDestinationError,
            "Destination escapes organized root",
        ):
            ensure_directories(config)

        self.assertFalse(outside.exists())

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

    def _run_watcher_with_mover(self, move_result):
        source = self._source(content="watch me")
        callback = MagicMock()
        handler = self._watcher(callback)
        with (
            patch("app.watcher.smart_classify", return_value=None),
            patch("app.watcher.time.sleep"),
            patch(
                "app.watcher.move_file_with_retries",
                return_value=move_result,
            ),
        ):
            handler._process_file_thread(str(source), "created")
        return source, handler, callback

    def test_watcher_reports_successful_move_as_moved(self):
        source = self.incoming / "incoming.txt"
        move_result = mover.MoveResult(
            mover.MoveStatus.MOVED,
            source,
            destination=self.documents / source.name,
        )
        source, handler, callback = self._run_watcher_with_mover(move_result)

        self.assertEqual(handler.last_processed_file, source.name)
        callback.assert_called_once_with(source.name, "documents", "moved")

    def test_watcher_reports_duplicate_outcome_as_duplicate(self):
        source = self.incoming / "incoming.txt"
        move_result = mover.MoveResult(
            mover.MoveStatus.DUPLICATE,
            source,
            duplicate_of=self.documents / "existing.txt",
        )
        source, _, callback = self._run_watcher_with_mover(move_result)

        self.assertTrue(source.exists())
        callback.assert_called_once_with(source.name, "documents", "duplicate")

    def test_watcher_reports_hash_failure(self):
        source = self.incoming / "incoming.txt"
        move_result = mover.MoveResult(
            mover.MoveStatus.HASH_CHECK_FAILED,
            source,
            error="hash failed",
        )
        source, _, callback = self._run_watcher_with_mover(move_result)

        self.assertTrue(source.exists())
        callback.assert_called_once_with(source.name, "documents", "hash_check_failed")

    def test_watcher_reports_exhausted_move_failure_as_failed(self):
        source = self.incoming / "incoming.txt"
        move_result = mover.MoveResult(
            mover.MoveStatus.MOVE_FAILED,
            source,
            error="move failed",
        )
        source, _, callback = self._run_watcher_with_mover(move_result)

        self.assertTrue(source.exists())
        callback.assert_called_once_with(source.name, "documents", "failed")

    # Concurrency behavior

    def test_concurrent_equal_content_cannot_both_move(self):
        first = self._source("first.txt", "equal content")
        second = self._source("second.txt", "equal content")
        registration_started = threading.Event()
        release_registration = threading.Event()
        second_lock_attempted = threading.Event()
        second_verification_started = threading.Event()
        verification_count_lock = threading.Lock()
        verification_count = 0
        real_verify = mover.get_verified_file_path
        real_register = mover.register_file_hash
        real_hash_operation = mover.hash_operation
        errors = []
        results = []

        def observed_verify(file_hash, hash_db_file):
            nonlocal verification_count
            with verification_count_lock:
                verification_count += 1
                if verification_count == 2:
                    second_verification_started.set()
            return real_verify(file_hash, hash_db_file)

        def blocked_register(file_hash, stored_path, hash_db_file):
            with hash_manager._hash_operation_locks_guard:
                lock_entry = hash_manager._hash_operation_locks.get(file_hash)
                self.assertIsNotNone(lock_entry)
                self.assertTrue(lock_entry[0].locked())
            registration_started.set()
            if not release_registration.wait(timeout=5):
                raise TimeoutError("registration was not released")
            real_register(file_hash, stored_path, hash_db_file)

        @contextmanager
        def observed_hash_operation(file_hash):
            if threading.current_thread().name == "second-equal-content-move":
                second_lock_attempted.set()
            with real_hash_operation(file_hash):
                yield

        def move_in_thread(source):
            try:
                results.append(self._move(source))
            except Exception as error:
                errors.append(error)

        with (
            patch(
                "app.mover.get_verified_file_path",
                side_effect=observed_verify,
            ),
            patch(
                "app.mover.register_file_hash",
                side_effect=blocked_register,
            ),
            patch("app.mover.hash_operation", new=observed_hash_operation),
            patch("app.mover.append_history"),
            patch("app.mover.update_stats"),
        ):
            first_thread = threading.Thread(target=move_in_thread, args=(first,))
            second_thread = threading.Thread(target=move_in_thread, args=(second,))
            first_thread.start()
            self.assertTrue(registration_started.wait(timeout=5))
            second_thread.name = "second-equal-content-move"
            second_thread.start()

            try:
                self.assertTrue(second_lock_attempted.wait(timeout=5))
                self.assertFalse(second_verification_started.wait(timeout=0.2))
            finally:
                release_registration.set()
                first_thread.join(timeout=10)
                second_thread.join(timeout=10)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertTrue(second_verification_started.is_set())
        self.assertEqual(errors, [])
        self.assertCountEqual(
            [result.status for result in results],
            [mover.MoveStatus.MOVED, mover.MoveStatus.DUPLICATE],
        )
        moved_result = next(
            result for result in results if result.status is mover.MoveStatus.MOVED
        )
        duplicate_result = next(
            result for result in results if result.status is mover.MoveStatus.DUPLICATE
        )
        self.assertFalse(moved_result.source.exists())
        self.assertTrue(moved_result.destination.exists())
        self.assertTrue(duplicate_result.source.exists())
        self.assertEqual(duplicate_result.duplicate_of, moved_result.destination)
        file_hash = hashlib.sha256(b"equal content").hexdigest()
        self.assertEqual(
            Path(hash_manager.load_hash_db(str(self.hash_db_file))[file_hash]),
            moved_result.destination,
        )

    def test_hash_operation_releases_lock_after_exception(self):
        file_hash = hashlib.sha256(b"exception").hexdigest()

        with self.assertRaisesRegex(RuntimeError, "simulated failure"):
            with hash_manager.hash_operation(file_hash):
                raise RuntimeError("simulated failure")

        acquired = threading.Event()

        def acquire_again():
            with hash_manager.hash_operation(file_hash):
                acquired.set()

        thread = threading.Thread(target=acquire_again)
        thread.start()
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertTrue(acquired.is_set())
        with hash_manager._hash_operation_locks_guard:
            self.assertNotIn(file_hash, hash_manager._hash_operation_locks)

    def test_hash_operations_for_unrelated_hashes_do_not_serialize(self):
        first_acquired = threading.Event()
        release_first = threading.Event()
        second_acquired = threading.Event()

        def hold_first_hash():
            with hash_manager.hash_operation("first-hash"):
                first_acquired.set()
                release_first.wait(timeout=5)

        def acquire_second_hash():
            with hash_manager.hash_operation("second-hash"):
                second_acquired.set()

        first_thread = threading.Thread(target=hold_first_hash)
        second_thread = threading.Thread(target=acquire_second_hash)
        first_thread.start()
        self.assertTrue(first_acquired.wait(timeout=5))
        second_thread.start()

        try:
            self.assertTrue(second_acquired.wait(timeout=1))
        finally:
            release_first.set()
            first_thread.join(timeout=5)
            second_thread.join(timeout=5)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
