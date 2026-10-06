import tempfile
import unittest
from pathlib import Path

from app import mover
from app.application_service import (
    FilePilotService,
    MonitorState,
    StartupResult,
    StartupStatus,
)
from app.operation_journal import OperationJournal, OperationStatus


class ManualOrganizeServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.incoming = self.root / "incoming"
        self.organized = self.root / "organized"
        self.documents = self.organized / "documents"
        self.data = self.root / "data"
        self.incoming.mkdir()
        self.organized.mkdir()
        self.data.mkdir()

        self.journal_path = self.data / "operations.sqlite3"
        self.hash_db = self.data / "hashes.json"
        self.history = self.data / "history.csv"
        self.stats = self.data / "stats.json"
        OperationJournal(self.journal_path)

        self.config = {
            "source_folder": str(self.incoming),
            "organized_base_folder": str(self.organized),
            "destination_folders": {
                "documents": str(self.documents),
                "others": str(self.organized / "others"),
            },
            "rules": {
                "documents": [".txt"],
                "others": [],
            },
            "hash_db_file": str(self.hash_db),
            "history_file": str(self.history),
            "stats_file": str(self.stats),
            "archive_by_date": False,
        }
        self.service = FilePilotService(
            config_path=self.root / "config.json",
            journal_path=self.journal_path,
        )
        self.service._config = self.config
        self.service._startup_result = StartupResult(
            StartupStatus.READY,
            config=self.config,
        )
        self.service._monitor_state = MonitorState.STOPPED

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_ready_manual_organize_moves_journals_and_publishes_activity(self):
        source = self.incoming / "report.txt"
        source.write_text("manual organize content", encoding="utf-8")
        events = []
        self.service.subscribe_activity(events.append)

        result = self.service.organize_file(source)

        self.assertIs(result.status, mover.MoveStatus.MOVED)
        self.assertFalse(source.exists())
        self.assertEqual(result.destination, self.documents / source.name)
        self.assertEqual(
            result.destination.read_text(encoding="utf-8"),
            "manual organize content",
        )
        self.assertIsNotNone(result.operation_id)
        operation = OperationJournal(self.journal_path).get_operation(
            result.operation_id
        )
        self.assertIs(operation.operation_status, OperationStatus.COMPLETE)
        self.assertEqual(operation.category, "documents")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].status, "moved")
        self.assertEqual(events[0].operation_id, result.operation_id)

    def test_manual_organize_is_refused_while_monitoring_runs(self):
        source = self.incoming / "running.txt"
        source.write_text("keep me here", encoding="utf-8")
        self.service._monitor_state = MonitorState.RUNNING

        result = self.service.organize_file(source)

        self.assertIs(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIn("Stop automatic organization", result.error)
        self.assertTrue(source.is_file())
        self.assertFalse((self.documents / source.name).exists())

    def test_execution_time_duplicate_check_keeps_source(self):
        original = self.incoming / "original.txt"
        original.write_text("same bytes", encoding="utf-8")
        first = self.service.organize_file(original)
        self.assertIs(first.status, mover.MoveStatus.MOVED)

        duplicate = self.incoming / "copy.txt"
        duplicate.write_text("same bytes", encoding="utf-8")
        result = self.service.organize_file(duplicate)

        self.assertIs(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIn("verified matching content", result.error)
        self.assertTrue(duplicate.is_file())
        self.assertEqual(first.destination.read_text(encoding="utf-8"), "same bytes")

    def test_source_inside_organized_root_is_refused(self):
        self.documents.mkdir(parents=True, exist_ok=True)
        source = self.documents / "already-organized.txt"
        source.write_text("leave in place", encoding="utf-8")

        result = self.service.organize_file(source)

        self.assertIs(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertTrue(source.is_file())
        self.assertEqual(source.read_text(encoding="utf-8"), "leave in place")

    def test_collision_created_after_preview_is_revalidated_without_clobber(self):
        source = self.incoming / "collision.txt"
        source.write_text("new source", encoding="utf-8")
        preview = self.service.preview_file(source)
        self.assertIs(preview.status, mover.PreviewStatus.READY)
        self.assertEqual(preview.proposed_destination, self.documents / source.name)

        self.documents.mkdir(parents=True, exist_ok=True)
        occupied = self.documents / source.name
        occupied.write_text("existing user data", encoding="utf-8")

        result = self.service.organize_file(source)

        self.assertIs(result.status, mover.MoveStatus.MOVED)
        self.assertEqual(occupied.read_text(encoding="utf-8"), "existing user data")
        self.assertNotEqual(result.destination, occupied)
        self.assertEqual(result.destination.read_text(encoding="utf-8"), "new source")
        self.assertFalse(source.exists())


if __name__ == "__main__":
    unittest.main()
