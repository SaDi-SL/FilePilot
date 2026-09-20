import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


EXIT_CODES = {
    "same_volume_physical": 71,
    "cross_temp_partial": 72,
    "cross_temp_verified": 73,
    "cross_published": 74,
    "cross_verified": 75,
    "cross_source_staged": 76,
    "source_removed_pre_hash": 77,
    "hash_pre_history": 78,
    "history_pre_stats": 79,
    "stats_pre_callback": 80,
}

CONTENT = b"FilePilot recovery characterization content"
HISTORY_HEADER = (
    "timestamp,filename,category,status,classification_method,smart_source\n"
)


class RecoveryCrashStateCharacterizationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.repository_root = Path(__file__).resolve().parent.parent
        self.worker = Path(__file__).with_name("recovery_crash_worker.py")
        self.incoming = self.root / "incoming"
        self.organized = self.root / "organized"
        self.documents = self.organized / "documents"
        self.reports = self.root / "reports"
        self.source = self.incoming / "incoming.txt"
        self.destination = self.documents / self.source.name
        self.hash_db = self.reports / "hashes.json"
        self.history_file = self.reports / "history.csv"
        self.stats_file = self.reports / "stats.json"

        self.incoming.mkdir()
        self.reports.mkdir()
        self.source.write_bytes(CONTENT)
        self.hash_db.write_text("{}", encoding="utf-8")
        self.history_file.write_text(HISTORY_HEADER, encoding="utf-8")
        self.stats_file.write_text(
            json.dumps(
                {
                    "total_files": 0,
                    "failed": 0,
                    "documents": 0,
                    "others": 0,
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run_worker(self, mode, expected_returncode=0):
        environment = os.environ.copy()
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = os.pathsep.join(
            value
            for value in (str(self.repository_root), existing_pythonpath)
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
            expected_returncode,
            msg=(
                f"worker mode {mode!r} returned {completed.returncode}\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            ),
        )
        return completed

    def _crash(self, scenario):
        self._run_worker(scenario, EXIT_CODES[scenario])
        marker = json.loads(
            (self.root / "crash-marker.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            marker,
            {"scenario": scenario, "exit_code": EXIT_CODES[scenario]},
        )

    def _hashes(self):
        return json.loads(self.hash_db.read_text(encoding="utf-8"))

    def _history(self):
        with open(self.history_file, newline="", encoding="utf-8") as file:
            return list(csv.DictReader(file))

    def _stats(self):
        return json.loads(self.stats_file.read_text(encoding="utf-8"))

    def _assert_metadata_untouched(self):
        self.assertEqual(self._hashes(), {})
        self.assertEqual(self._history(), [])
        self.assertEqual(
            self._stats(),
            {
                "total_files": 0,
                "failed": 0,
                "documents": 0,
                "others": 0,
            },
        )

    def _temps(self):
        if not self.organized.exists():
            return []
        return list(self.organized.rglob(".filepilot-*.tmp"))

    def _staging_dirs(self):
        return list(self.incoming.glob(".filepilot-remove-*"))

    def _scan_startup(self):
        scan_result = self.root / "scan-result.json"
        if scan_result.exists():
            scan_result.unlink()
        self._run_worker("scan_startup")
        return json.loads(scan_result.read_text(encoding="utf-8"))

    def test_same_volume_crash_after_physical_move_precedes_all_metadata(self):
        self._crash("same_volume_physical")

        self.assertFalse(self.source.exists())
        self.assertEqual(self.destination.read_bytes(), CONTENT)
        self.assertEqual(self._temps(), [])
        self.assertEqual(self._staging_dirs(), [])
        self._assert_metadata_untouched()
        self.assertEqual(self._scan_startup(), [])
        self._assert_metadata_untouched()

    def test_cross_volume_crash_leaves_partial_private_temp_and_source(self):
        self._crash("cross_temp_partial")

        temps = self._temps()
        self.assertTrue(self.source.exists())
        self.assertFalse(self.destination.exists())
        self.assertEqual(len(temps), 1)
        self.assertEqual(temps[0].read_bytes(), CONTENT[:7])
        self._assert_metadata_untouched()
        self.assertEqual(
            self._scan_startup(),
            [{"path": str(self.source), "event": "startup_scan"}],
        )
        self.assertTrue(temps[0].exists())

    def test_cross_volume_crash_leaves_verified_temp_before_publication(self):
        self._crash("cross_temp_verified")

        temps = self._temps()
        self.assertTrue(self.source.exists())
        self.assertFalse(self.destination.exists())
        self.assertEqual(len(temps), 1)
        self.assertEqual(temps[0].read_bytes(), CONTENT)
        self.assertEqual(
            hashlib.sha256(temps[0].read_bytes()).hexdigest(),
            hashlib.sha256(CONTENT).hexdigest(),
        )
        self._assert_metadata_untouched()
        self.assertEqual(
            self._scan_startup(),
            [{"path": str(self.source), "event": "startup_scan"}],
        )
        self.assertTrue(temps[0].exists())

    def test_cross_volume_crash_after_publication_reprocesses_to_second_copy(self):
        self._crash("cross_published")

        self.assertTrue(self.source.exists())
        self.assertEqual(self.destination.read_bytes(), CONTENT)
        self.assertEqual(self._temps(), [])
        self.assertEqual(self._staging_dirs(), [])
        self._assert_metadata_untouched()

        self._run_worker("reprocess_cross")

        alternative = self.documents / "incoming(1).txt"
        result = json.loads(
            (self.root / "reprocess-result.json").read_text(encoding="utf-8")
        )
        expected_hash = hashlib.sha256(CONTENT).hexdigest()
        self.assertEqual(result["status"], "moved")
        self.assertEqual(Path(result["destination"]), alternative)
        self.assertFalse(self.source.exists())
        self.assertEqual(self.destination.read_bytes(), CONTENT)
        self.assertEqual(alternative.read_bytes(), CONTENT)
        self.assertEqual(self._hashes(), {expected_hash: str(alternative)})
        self.assertEqual([row["status"] for row in self._history()], ["moved"])
        self.assertEqual(self._stats()["total_files"], 1)

    def test_cross_volume_crash_after_verification_retains_both_files(self):
        self._crash("cross_verified")

        self.assertTrue(self.source.exists())
        self.assertEqual(self.destination.read_bytes(), CONTENT)
        self.assertEqual(self._temps(), [])
        self.assertEqual(self._staging_dirs(), [])
        self._assert_metadata_untouched()

    def test_cross_volume_crash_after_source_staging_hides_source_from_scan(self):
        self._crash("cross_source_staged")

        staging_dirs = self._staging_dirs()
        self.assertFalse(self.source.exists())
        self.assertEqual(self.destination.read_bytes(), CONTENT)
        self.assertEqual(len(staging_dirs), 1)
        staged_source = staging_dirs[0] / self.source.name
        self.assertEqual(staged_source.read_bytes(), CONTENT)
        self.assertEqual(
            hashlib.sha256(staged_source.read_bytes()).hexdigest(),
            hashlib.sha256(self.destination.read_bytes()).hexdigest(),
        )
        self._assert_metadata_untouched()
        self.assertEqual(self._scan_startup(), [])
        self.assertTrue(staged_source.exists())
        self._assert_metadata_untouched()

    def test_crash_after_source_removal_leaves_unindexed_destination(self):
        self._crash("source_removed_pre_hash")

        self.assertFalse(self.source.exists())
        self.assertEqual(self.destination.read_bytes(), CONTENT)
        self.assertEqual(self._temps(), [])
        self.assertEqual(self._staging_dirs(), [])
        self._assert_metadata_untouched()
        self.assertEqual(self._scan_startup(), [])
        self._assert_metadata_untouched()

    def test_crash_after_hash_registration_precedes_history_and_stats(self):
        self._crash("hash_pre_history")

        expected_hash = hashlib.sha256(CONTENT).hexdigest()
        self.assertFalse(self.source.exists())
        self.assertEqual(self.destination.read_bytes(), CONTENT)
        self.assertEqual(self._hashes(), {expected_hash: str(self.destination)})
        self.assertEqual(self._history(), [])
        self.assertEqual(self._stats()["total_files"], 0)

    def test_crash_after_history_precedes_statistics(self):
        self._crash("history_pre_stats")

        expected_hash = hashlib.sha256(CONTENT).hexdigest()
        self.assertFalse(self.source.exists())
        self.assertEqual(self.destination.read_bytes(), CONTENT)
        self.assertEqual(self._hashes(), {expected_hash: str(self.destination)})
        self.assertEqual([row["status"] for row in self._history()], ["moved"])
        self.assertEqual(self._stats()["total_files"], 0)
        self.assertEqual(self._stats()["documents"], 0)

    def test_crash_after_statistics_loses_result_and_callback_delivery(self):
        self._crash("stats_pre_callback")

        expected_hash = hashlib.sha256(CONTENT).hexdigest()
        self.assertFalse(self.source.exists())
        self.assertEqual(self.destination.read_bytes(), CONTENT)
        self.assertEqual(self._hashes(), {expected_hash: str(self.destination)})
        self.assertEqual([row["status"] for row in self._history()], ["moved"])
        self.assertEqual(self._stats()["total_files"], 1)
        self.assertEqual(self._stats()["documents"], 1)
        self.assertFalse((self.root / "callback-delivered.json").exists())
        self.assertFalse((self.root / "processing-returned.json").exists())


if __name__ == "__main__":
    unittest.main()
