import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.mover import MoveResult, MoveStatus
from app.multi_watcher import MultiFolderMonitor
from app.path_topology import UnsafePathTopologyError
from app.watcher import FileMonitor, NewFileHandler


class PathTopologyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _config(self, watch_root, organized_root, watch_folders=None):
        config = {
            "source_folder": str(watch_root),
            "organized_base_folder": str(organized_root),
            "destination_folders": {
                "documents": str(organized_root / "documents"),
                "others": str(organized_root / "others"),
            },
            "rules": {"documents": [".txt"]},
            "processing_wait_seconds": 0,
            "duplicate_event_window_seconds": 3,
            "archive_by_date": False,
            "stats_file": str(self.root / "reports" / "stats.json"),
            "history_file": str(self.root / "reports" / "history.csv"),
            "hash_db_file": str(self.root / "reports" / "hashes.json"),
        }
        if watch_folders is not None:
            config["watch_folders"] = watch_folders
        return config

    @staticmethod
    def _event(path):
        return SimpleNamespace(is_directory=False, src_path=str(path))

    def test_equal_roots_are_rejected(self):
        shared_root = self.root / "shared"
        shared_root.mkdir()
        config = self._config(shared_root, shared_root)

        with self.assertRaisesRegex(
            UnsafePathTopologyError,
            "must be different",
        ):
            MultiFolderMonitor(config, {".txt": "documents"})

    def test_organized_root_under_watch_is_rejected(self):
        watch_root = self.root / "incoming"
        organized_root = watch_root / "organized"
        watch_root.mkdir()
        organized_root.mkdir()
        config = self._config(watch_root, organized_root)

        with self.assertRaisesRegex(
            UnsafePathTopologyError,
            "Organized root cannot be inside watch root",
        ):
            MultiFolderMonitor(config, {".txt": "documents"})

    def test_watch_root_under_organized_is_rejected(self):
        organized_root = self.root / "organized"
        watch_root = organized_root / "incoming"
        watch_root.mkdir(parents=True)
        config = self._config(watch_root, organized_root)

        with self.assertRaisesRegex(
            UnsafePathTopologyError,
            "Watch root cannot be inside organized root",
        ):
            MultiFolderMonitor(config, {".txt": "documents"})

    def test_source_inside_organized_is_ignored_before_classification(self):
        watch_root = self.root / "incoming"
        organized_root = self.root / "organized"
        internal_source = organized_root / "documents" / "internal.txt"
        watch_root.mkdir()
        internal_source.parent.mkdir(parents=True)
        internal_source.write_text("internal", encoding="utf-8")
        callback = MagicMock()
        plugin_manager = MagicMock()
        plugin_manager.classify_with_plugins.return_value = None
        handler = NewFileHandler(
            self._config(watch_root, organized_root),
            {".txt": "documents"},
            plugin_manager=plugin_manager,
            file_processed_callback=callback,
        )

        with (
            patch.object(handler, "_wait_until_stable", return_value=True),
            patch("app.watcher.smart_classify", return_value=None) as smart,
            patch(
                "app.watcher.move_file_with_retries",
                return_value=MoveResult(
                    MoveStatus.MOVED,
                    internal_source,
                    destination=internal_source,
                ),
            ) as move_file,
        ):
            handler._process_file_thread(str(internal_source), "created")

        plugin_manager.classify_with_plugins.assert_not_called()
        smart.assert_not_called()
        move_file.assert_not_called()
        callback.assert_not_called()
        self.assertTrue(internal_source.exists())
        self.assertEqual(internal_source.read_text(encoding="utf-8"), "internal")

    def test_startup_scan_cannot_start_for_organized_root(self):
        organized_root = self.root / "organized"
        organized_root.mkdir()
        internal_source = organized_root / "internal.txt"
        internal_source.write_text("internal", encoding="utf-8")
        with (
            patch.object(FileMonitor, "scan_existing_files") as scan,
            self.assertRaises(UnsafePathTopologyError),
        ):
            FileMonitor(
                self._config(organized_root, organized_root),
                {".txt": "documents"},
            )

        scan.assert_not_called()
        self.assertTrue(internal_source.exists())

    def test_live_internal_event_is_ignored_before_submission(self):
        watch_root = self.root / "incoming"
        organized_root = self.root / "organized"
        watch_root.mkdir()
        internal_source = organized_root / "documents" / "internal.txt"
        internal_source.parent.mkdir(parents=True)
        internal_source.write_text("internal", encoding="utf-8")
        submit = MagicMock()
        handler = NewFileHandler(
            self._config(watch_root, organized_root),
            {".txt": "documents"},
            submit_file=submit,
        )

        handler.on_created(self._event(internal_source))

        submit.assert_not_called()
        self.assertTrue(internal_source.exists())

    def test_secondary_watch_cannot_overlap_organized(self):
        first_watch = self.root / "first"
        second_watch = self.root / "second"
        organized_root = second_watch / "organized"
        first_watch.mkdir()
        organized_root.mkdir(parents=True)
        folders = [
            {"path": str(first_watch), "label": "First", "active": True},
            {"path": str(second_watch), "label": "Second", "active": True},
        ]
        config = self._config(first_watch, organized_root, folders)

        with (
            patch("app.multi_watcher.FileMonitor") as monitor_type,
            self.assertRaisesRegex(
                UnsafePathTopologyError,
                "Organized root cannot be inside watch root",
            ),
        ):
            MultiFolderMonitor(config, {".txt": "documents"})

        monitor_type.assert_not_called()

    def test_sibling_prefix_paths_are_accepted(self):
        watch_root = self.root / "Data"
        organized_root = self.root / "Database"
        watch_root.mkdir()
        organized_root.mkdir()
        config = self._config(watch_root, organized_root)

        monitor = MultiFolderMonitor(config, {".txt": "documents"})

        self.assertEqual(list(monitor._monitors), [str(watch_root.resolve())])

    @unittest.skipUnless(os.name == "nt", "Windows path case semantics")
    def test_case_variant_equal_roots_are_rejected(self):
        organized_root = self.root / "Organized"
        organized_root.mkdir()
        watch_root = Path(str(organized_root).swapcase())
        config = self._config(watch_root, organized_root)

        with self.assertRaisesRegex(
            UnsafePathTopologyError,
            "must be different",
        ):
            MultiFolderMonitor(config, {".txt": "documents"})

    def test_existing_directory_symlink_alias_is_rejected(self):
        organized_root = self.root / "organized"
        organized_root.mkdir()
        watch_alias = self.root / "watch-alias"
        try:
            watch_alias.symlink_to(organized_root, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Directory symlinks unavailable: {error}")
        config = self._config(watch_alias, organized_root)

        with self.assertRaisesRegex(
            UnsafePathTopologyError,
            "must be different",
        ):
            MultiFolderMonitor(config, {".txt": "documents"})

    def test_normal_disjoint_topology_is_accepted(self):
        watch_root = self.root / "incoming"
        organized_root = self.root / "organized"
        watch_root.mkdir()
        organized_root.mkdir()
        config = self._config(watch_root, organized_root)

        monitor = MultiFolderMonitor(config, {".txt": "documents"})

        self.assertEqual(list(monitor._monitors), [str(watch_root.resolve())])


if __name__ == "__main__":
    unittest.main()
