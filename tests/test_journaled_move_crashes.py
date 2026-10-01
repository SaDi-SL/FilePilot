import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.operation_journal import OperationJournal, PhysicalPhase


EXIT_CODES = {
    "after_intent": 91,
    "after_same_rename": 92,
    "during_temp_copy": 93,
    "after_temp_verified": 94,
    "after_destination_published": 95,
    "after_destination_verified": 96,
    "after_source_staged": 97,
    "after_source_removed": 98,
    "before_metadata": 99,
}


class JournaledMoveCrashTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.incoming = self.root / "incoming"
        self.incoming.mkdir()
        self.source = self.incoming / "report.txt"
        self.source.write_bytes(b"journaled crash boundary content")
        self.destination = self.root / "organized" / "documents" / "report.txt"
        self.database = self.root / "data" / "operations.sqlite3"
        self.repository_root = Path(__file__).resolve().parent.parent
        self.worker = Path(__file__).with_name("journaled_move_crash_worker.py")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _crash(self, mode):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            value
            for value in (
                str(self.repository_root),
                environment.get("PYTHONPATH"),
            )
            if value
        )
        completed = subprocess.run(
            [sys.executable, "-B", str(self.worker), mode, str(self.root)],
            cwd=self.repository_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            EXIT_CODES[mode],
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        operations = OperationJournal(self.database).list_incomplete_operations()
        self.assertEqual(len(operations), 1)
        return operations[0]

    def test_crash_after_durable_intent_precedes_same_volume_rename(self):
        operation = self._crash("after_intent")
        self.assertEqual(operation.physical_phase, PhysicalPhase.RENAME_INTENT)
        self.assertTrue(self.source.exists())
        self.assertFalse(self.destination.exists())

    def test_crash_after_same_volume_rename_leaves_recoverable_intent(self):
        operation = self._crash("after_same_rename")
        self.assertEqual(operation.physical_phase, PhysicalPhase.RENAME_INTENT)
        self.assertFalse(self.source.exists())
        self.assertTrue(Path(operation.actual_destination).exists())

    def test_crash_during_temp_copy_records_owned_temp(self):
        operation = self._crash("during_temp_copy")
        self.assertEqual(operation.physical_phase, PhysicalPhase.TEMP_CREATED)
        self.assertTrue(self.source.exists())
        self.assertTrue(Path(operation.temp_path).exists())

    def test_crash_after_temp_verification_records_verified_temp(self):
        operation = self._crash("after_temp_verified")
        self.assertEqual(operation.physical_phase, PhysicalPhase.TEMP_VERIFIED)
        self.assertTrue(self.source.exists())
        self.assertTrue(Path(operation.temp_path).exists())

    def test_crash_after_destination_publication_preserves_source(self):
        operation = self._crash("after_destination_published")
        self.assertEqual(operation.physical_phase, PhysicalPhase.DESTINATION_PUBLISHED)
        self.assertTrue(self.source.exists())
        self.assertTrue(Path(operation.actual_destination).exists())

    def test_crash_after_destination_verification_preserves_source(self):
        operation = self._crash("after_destination_verified")
        self.assertEqual(operation.physical_phase, PhysicalPhase.DESTINATION_VERIFIED)
        self.assertTrue(self.source.exists())
        self.assertTrue(Path(operation.actual_destination).exists())

    def test_crash_after_source_staging_records_owned_staged_source(self):
        operation = self._crash("after_source_staged")
        self.assertEqual(operation.physical_phase, PhysicalPhase.SOURCE_STAGED)
        self.assertFalse(self.source.exists())
        self.assertTrue(Path(operation.staging_path).exists())
        self.assertTrue(Path(operation.actual_destination).exists())

    def test_crash_after_source_removal_retains_delete_intent(self):
        operation = self._crash("after_source_removed")
        self.assertEqual(operation.physical_phase, PhysicalPhase.SOURCE_DELETE_INTENT)
        self.assertFalse(self.source.exists())
        self.assertFalse(Path(operation.staging_path).exists())
        self.assertTrue(Path(operation.actual_destination).exists())

    def test_crash_before_metadata_leaves_physical_commit_open(self):
        operation = self._crash("before_metadata")
        self.assertEqual(operation.physical_phase, PhysicalPhase.PHYSICAL_COMMITTED)
        self.assertFalse(self.source.exists())
        self.assertTrue(Path(operation.actual_destination).exists())


if __name__ == "__main__":
    unittest.main()
