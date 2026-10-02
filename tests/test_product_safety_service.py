import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app import mover
from app.application_service import (
    FilePilotService,
    MonitorState,
    RecoveryAction,
    RecoveryActionStatus,
    SafetyDataState,
    StartupResult,
    StartupStatus,
)
from app.operation_journal import OperationJournal, OperationStatus
from app.recovery import RecoveryBlockedError, RecoveryReport, assess_operation
from app.undo import UndoStatus


class ProductSafetyServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.incoming = self.root / "incoming"
        self.organized = self.root / "organized"
        self.documents = self.organized / "documents"
        self.incoming.mkdir()
        self.organized.mkdir()
        self.journal_path = self.root / "data" / "operations.sqlite3"
        self.hash_db = self.root / "data" / "hashes.json"
        self.history = self.root / "data" / "history.csv"
        self.stats = self.root / "data" / "stats.json"
        self.journal = OperationJournal(self.journal_path)
        self.config = {
            "organized_base_folder": str(self.organized),
            "destination_folders": {
                "documents": str(self.documents),
                "others": str(self.organized / "others"),
            },
            "rules": {"documents": [".txt"]},
            "hash_db_file": str(self.hash_db),
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

    def tearDown(self):
        self.temp_dir.cleanup()

    def _completed_move(self):
        source = self.incoming / "report.txt"
        source.write_text("authoritative safety content", encoding="utf-8")
        result = mover.move_file_with_retries(
            source,
            self.config["destination_folders"],
            {".txt": "documents"},
            str(self.stats),
            str(self.history),
            str(self.hash_db),
            False,
            self.config["rules"],
            self.organized,
            retries=1,
            delay=0,
            category_override="documents",
            journal=self.journal,
        )
        self.assertIs(result.status, mover.MoveStatus.MOVED)
        return source, result.destination, result.operation_id

    def _interrupted_operation(self, source: Path):
        content = source.read_bytes()
        state = source.stat(follow_symlinks=False)
        return self.journal.create_operation(
            source_path=source,
            organized_root=self.organized,
            intended_destination=self.documents / source.name,
            source_identity=f"stat-v1:{state.st_dev}:{state.st_ino}",
            source_hash=hashlib.sha256(content).hexdigest(),
            source_size=state.st_size,
            source_mtime_ns=state.st_mtime_ns,
        )

    def test_preview_is_non_mutating_and_uses_runtime_plan(self):
        source = self.incoming / "preview.txt"
        source.write_text("preview content", encoding="utf-8")

        result = self.service.preview_file(source)

        self.assertIs(result.state, SafetyDataState.AVAILABLE)
        self.assertIs(result.status, mover.PreviewStatus.READY)
        self.assertEqual(result.proposed_destination, self.documents / source.name)
        self.assertTrue(result.non_mutating)
        self.assertTrue(source.is_file())
        self.assertFalse(self.documents.exists())
        self.assertFalse(self.hash_db.exists())

    def test_undo_availability_and_execution_revalidate_without_overwrite(self):
        source, destination, operation_id = self._completed_move()
        availability = self.service.get_undo_availability(operation_id)
        self.assertTrue(availability.eligible)
        self.assertEqual(availability.current_path, destination)
        self.assertEqual(availability.restore_path, source)

        source.write_text("new user data", encoding="utf-8")
        result = self.service.undo_operation(operation_id)

        self.assertIs(result.status, UndoStatus.CONFLICT)
        self.assertEqual(source.read_text(encoding="utf-8"), "new user data")
        self.assertTrue(destination.is_file())

    def test_undo_is_refused_while_monitoring_is_running(self):
        _, destination, operation_id = self._completed_move()
        self.service._monitor_state = MonitorState.RUNNING

        availability = self.service.get_undo_availability(operation_id)
        result = self.service.undo_operation(operation_id)

        self.assertFalse(availability.eligible)
        self.assertIs(result.status, UndoStatus.NOT_ELIGIBLE)
        self.assertTrue(destination.is_file())

    def test_recovery_inventory_exposes_only_core_approved_action(self):
        source = self.incoming / "interrupted.txt"
        source.write_text("still at source", encoding="utf-8")
        operation = self._interrupted_operation(source)

        snapshot = self.service.get_recovery_snapshot()

        self.assertIs(snapshot.state, SafetyDataState.AVAILABLE)
        self.assertEqual(snapshot.total_items, 1)
        self.assertEqual(snapshot.items[0].operation_id, operation.operation_id)
        self.assertEqual(
            snapshot.items[0].available_actions,
            (RecoveryAction.APPLY_SAFE_RECOMMENDATION,),
        )

    def test_recovery_action_reassesses_and_aborts_without_moving_source(self):
        source = self.incoming / "interrupted.txt"
        source.write_text("still at source", encoding="utf-8")
        operation = self._interrupted_operation(source)

        result = self.service.reconcile_recovery_item(
            operation.operation_id,
            RecoveryAction.APPLY_SAFE_RECOMMENDATION,
        )

        self.assertIs(result.status, RecoveryActionStatus.RECONCILED)
        self.assertTrue(source.is_file())
        self.assertFalse((self.documents / source.name).exists())
        self.assertIs(
            self.journal.get_operation(operation.operation_id).operation_status,
            OperationStatus.ABORTED,
        )

    def test_successful_blocked_recovery_rebuilds_ready_runtime(self):
        source = self.incoming / "blocked.txt"
        source.write_text("still at source", encoding="utf-8")
        operation = self._interrupted_operation(source)
        self.service._startup_result = StartupResult(
            StartupStatus.BLOCKED,
            config=self.config,
            blocking_operation_ids=(operation.operation_id,),
        )
        self.service._monitor_state = MonitorState.BLOCKED
        self.service._monitor_builder = lambda: (self.config, object())
        self.config["watch_folders"] = [
            {"path": str(self.incoming), "active": True}
        ]
        self.service._config_path.write_text(
            json.dumps(self.config),
            encoding="utf-8",
        )

        result = self.service.reconcile_recovery_item(
            operation.operation_id,
            RecoveryAction.APPLY_SAFE_RECOMMENDATION,
        )

        self.assertIs(result.status, RecoveryActionStatus.RECONCILED)
        self.assertIs(self.service.startup_status, StartupStatus.READY)
        self.assertIs(self.service.monitor_state, MonitorState.STOPPED)

    def test_start_reruns_recovery_if_new_incomplete_operation_exists(self):
        source = self.incoming / "late-open.txt"
        source.write_text("still at source", encoding="utf-8")
        operation = self._interrupted_operation(source)
        assessment = assess_operation(self.journal, operation)
        report = RecoveryReport(
            (assessment,),
            (),
            (operation.operation_id,),
        )
        self.config["watch_folders"] = [
            {"path": str(self.incoming), "active": True}
        ]
        self.service._config_path.write_text(
            json.dumps(self.config),
            encoding="utf-8",
        )

        def blocked_builder():
            raise RecoveryBlockedError(
                "Recovery requires review before monitoring can start",
                report,
                (operation.operation_id,),
            )

        self.service._monitor_builder = blocked_builder

        state = self.service.start()

        self.assertIs(state, MonitorState.BLOCKED)
        self.assertIs(self.service.startup_status, StartupStatus.BLOCKED)
        self.assertEqual(
            self.service.startup_result.blocking_operation_ids,
            (operation.operation_id,),
        )

    def test_start_folder_cannot_bypass_new_recovery_evidence(self):
        source = self.incoming / "folder-open.txt"
        source.write_text("still at source", encoding="utf-8")
        operation = self._interrupted_operation(source)
        assessment = assess_operation(self.journal, operation)
        report = RecoveryReport((assessment,), (), (operation.operation_id,))
        self.config["watch_folders"] = [
            {"path": str(self.incoming), "active": True}
        ]
        self.service._config_path.write_text(
            json.dumps(self.config),
            encoding="utf-8",
        )
        self.service._monitor = object()

        def blocked_builder():
            raise RecoveryBlockedError(
                "Recovery requires review before monitoring can start",
                report,
                (operation.operation_id,),
            )

        self.service._monitor_builder = blocked_builder

        state = self.service.start_folder(str(self.incoming))

        self.assertIs(state, MonitorState.BLOCKED)
        self.assertIs(self.service.startup_status, StartupStatus.BLOCKED)

    def test_missing_authoritative_journal_fails_closed_before_start(self):
        self.journal_path.unlink()

        state = self.service.start()

        self.assertIs(state, MonitorState.ERROR)
        self.assertIn("Recovery evidence is unavailable", self.service.last_error)

    def test_repeated_start_does_not_treat_live_operation_as_recovery(self):
        source = self.incoming / "live.txt"
        source.write_text("currently processing", encoding="utf-8")
        self._interrupted_operation(source)

        class RunningMonitor:
            is_running = True

        self.service._monitor = RunningMonitor()
        self.service._monitor_state = MonitorState.RUNNING
        self.service._monitor_builder = lambda: self.fail(
            "Repeated start must not rebuild a live monitor"
        )

        self.assertIs(self.service.start(), MonitorState.RUNNING)

    def test_start_folder_does_not_recover_other_live_operations(self):
        source = self.incoming / "live-folder.txt"
        source.write_text("currently processing", encoding="utf-8")
        self._interrupted_operation(source)

        class RunningMonitor:
            is_running = True

            def __init__(self, path):
                self.started = []
                self.running_folders = (path,)

            def set_folder_active(self, path, active):
                pass

            def start_folder(self, path):
                self.started.append(path)
                return True

            def folder_status(self, path):
                return "running"

        self.config["watch_folders"] = [
            {"path": str(self.incoming), "active": True}
        ]
        monitor = RunningMonitor(str(self.incoming))
        self.service._monitor = monitor
        self.service._monitor_state = MonitorState.RUNNING
        self.service._monitor_builder = lambda: self.fail(
            "Starting another live folder must not rebuild the monitor"
        )

        state = self.service.start_folder(str(self.incoming))

        self.assertIs(state, MonitorState.RUNNING)
        self.assertEqual(monitor.started, [str(self.incoming)])

    def test_ambiguous_recovery_has_no_action(self):
        source = self.incoming / "missing.txt"
        operation = self.journal.create_operation(
            source_path=source,
            organized_root=self.organized,
            intended_destination=self.documents / source.name,
        )

        snapshot = self.service.get_recovery_snapshot()
        item = snapshot.items[0]

        self.assertEqual(item.operation_id, operation.operation_id)
        self.assertTrue(item.manual_review_required)
        self.assertEqual(item.available_actions, ())

    def test_recovery_inventory_is_bounded_and_pageable(self):
        for index in range(3):
            source = self.incoming / f"missing-{index}.txt"
            self.journal.create_operation(
                source_path=source,
                organized_root=self.organized,
                intended_destination=self.documents / source.name,
            )

        snapshot = self.service.get_recovery_snapshot(limit=1, offset=1)

        self.assertEqual(len(snapshot.items), 1)
        self.assertEqual(snapshot.total_items, 3)
        self.assertEqual(snapshot.offset, 1)
        self.assertTrue(snapshot.has_more)


if __name__ == "__main__":
    unittest.main()
