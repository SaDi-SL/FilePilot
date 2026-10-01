import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

from app.application_service import FilePilotService, MonitorState
from app.operation_journal import (
    MAX_RECENT_OPERATIONS,
    EffectState,
    EffectType,
    JournalValidationError,
    JournalError,
    OperationJournal,
    PhysicalPhase,
    TransitionEvidence,
)
from app.product_read_model import (
    ActivityStatus,
    ProductDataState,
    ProductReadModel,
)


class ProductReadModelTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.database_path = self.root / "data" / "operations.sqlite3"
        self.journal = OperationJournal(self.database_path)
        self.reader = ProductReadModel(self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create(self, name="report.txt", category="documents"):
        return self.journal.create_operation(
            source_path=self.root / "incoming" / name,
            organized_root=self.root / "organized",
            intended_destination=(
                self.root / "organized" / category / name
            ),
            category=category,
            classification_method="extension",
        )

    def _complete(self, name="report.txt", category="documents"):
        operation = self._create(name, category)
        destination = self.root / "organized" / category / name
        self.journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.PREPARED,
            PhysicalPhase.RENAME_INTENT,
        )
        self.journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.RENAME_INTENT,
            PhysicalPhase.PHYSICAL_COMMITTED,
            evidence=TransitionEvidence(actual_destination=destination),
        )
        self.journal.complete_operation(operation.operation_id)
        return self.journal.get_operation(operation.operation_id)

    def test_empty_journal_returns_truthful_zero_metrics_and_activity(self):
        snapshot = self.reader.read()

        self.assertIs(snapshot.state, ProductDataState.AVAILABLE)
        self.assertEqual(snapshot.activity, ())
        self.assertEqual(snapshot.metrics.total_processed, 0)
        self.assertEqual(snapshot.metrics.failed, 0)
        self.assertEqual(snapshot.metrics.duplicates, 0)
        self.assertEqual(snapshot.metrics.needs_review, 0)
        self.assertIsNone(snapshot.metrics.active_rules)

    def test_completed_operation_preserves_identity_destination_and_category(self):
        operation = self._complete("report.txt", "work/reports")

        record = self.reader.read().activity[0]

        self.assertEqual(record.operation_id, operation.operation_id)
        self.assertEqual(record.record_id, operation.operation_id)
        self.assertIs(record.status, ActivityStatus.COMPLETED)
        self.assertEqual(record.category, "work/reports")
        self.assertEqual(record.classification_method, "extension")
        self.assertEqual(
            record.actual_destination,
            self.root / "organized" / "work" / "reports" / "report.txt",
        )
        self.assertTrue(record.durable)

    def test_duplicate_maps_to_duplicate_target(self):
        operation = self._create("copy.txt")
        target = self.root / "organized" / "documents" / "original.txt"
        self.journal.mark_duplicate(
            operation.operation_id,
            expected_phase=PhysicalPhase.PREPARED,
            duplicate_of_path=target,
        )

        snapshot = self.reader.read()
        record = snapshot.activity[0]

        self.assertIs(record.status, ActivityStatus.DUPLICATE)
        self.assertEqual(record.duplicate_target, target)
        self.assertEqual(record.display_destination, target)
        self.assertEqual(snapshot.metrics.duplicates, 1)

    def test_aborted_and_needs_review_map_honestly(self):
        failed = self._create("failed.txt")
        self.journal.transition_phase(
            failed.operation_id,
            PhysicalPhase.PREPARED,
            PhysicalPhase.ABORTED,
            error_code="MOVE_FAILED",
            error_message="Destination unavailable",
        )
        reviewed = self._create("review.txt")
        self.journal.mark_needs_review(
            reviewed.operation_id,
            expected_phase=PhysicalPhase.PREPARED,
            error_code="RECOVERY_REVIEW",
            error_message="Evidence is ambiguous",
        )

        snapshot = self.reader.read()
        records = {item.operation_id: item for item in snapshot.activity}

        self.assertIs(records[failed.operation_id].status, ActivityStatus.FAILED)
        self.assertEqual(records[failed.operation_id].error, "Destination unavailable")
        self.assertIs(
            records[reviewed.operation_id].status,
            ActivityStatus.NEEDS_REVIEW,
        )
        self.assertEqual(snapshot.metrics.failed, 1)
        self.assertEqual(snapshot.metrics.needs_review, 1)

    def test_physically_committed_open_operation_is_warning_not_failure(self):
        operation = self._create("warning.txt")
        self.journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.PREPARED,
            PhysicalPhase.RENAME_INTENT,
        )
        self.journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.RENAME_INTENT,
            PhysicalPhase.PHYSICAL_COMMITTED,
        )

        record = self.reader.read().activity[0]

        self.assertIs(record.status, ActivityStatus.WARNING)
        self.assertIn("metadata", record.metadata_warning.lower())

    def test_failed_effect_becomes_metadata_warning(self):
        operation = self._create("metadata.txt")
        self.journal.initialize_effect(operation.operation_id, EffectType.HASH_INDEX)
        self.journal.transition_effect(
            operation.operation_id,
            EffectType.HASH_INDEX,
            EffectState.NOT_STARTED,
            EffectState.PENDING,
        )
        self.journal.transition_effect(
            operation.operation_id,
            EffectType.HASH_INDEX,
            EffectState.PENDING,
            EffectState.FAILED,
            error_code="INDEX_FAILED",
            error_message="Index unavailable",
        )

        record = self.reader.read().activity[0]

        self.assertEqual(record.metadata_warning, "Index unavailable")

    def test_missing_optional_fields_and_unprovable_category_do_not_crash(self):
        operation = self.journal.create_operation(
            source_path=self.root / "incoming" / "root.txt",
            organized_root=self.root / "organized",
            intended_destination=self.root / "outside" / "root.txt",
        )

        record = self.reader.read().activity[0]

        self.assertEqual(record.operation_id, operation.operation_id)
        self.assertIsNone(record.category)
        self.assertIsNone(record.actual_destination)
        self.assertIsNone(record.error)

    def test_recent_activity_is_newest_first_and_repeated_reads_are_deterministic(self):
        created = [self._create(f"{index}.txt") for index in range(4)]

        first = self.reader.read(limit=3)
        second = self.reader.read(limit=3)

        expected = [item.operation_id for item in reversed(created[-3:])]
        self.assertEqual([item.operation_id for item in first.activity], expected)
        self.assertEqual(first, second)
        self.assertTrue(first.has_more)

    def test_limit_is_hard_bounded_and_journal_rejects_unbounded_values(self):
        for index in range(MAX_RECENT_OPERATIONS + 5):
            self._create(f"{index}.txt")

        snapshot = self.reader.read(limit=MAX_RECENT_OPERATIONS + 1000)

        self.assertEqual(len(snapshot.activity), MAX_RECENT_OPERATIONS)
        with self.assertRaises(JournalValidationError):
            self.journal.read_recent_operations(MAX_RECENT_OPERATIONS + 1)

    def test_one_operation_produces_one_activity_record(self):
        operation = self._complete()

        records = [
            item
            for item in self.reader.read().activity
            if item.operation_id == operation.operation_id
        ]

        self.assertEqual(len(records), 1)

    def test_missing_journal_is_unavailable_without_creating_storage(self):
        missing = self.root / "missing" / "operations.sqlite3"

        snapshot = ProductReadModel(missing).read()

        self.assertIs(snapshot.state, ProductDataState.UNAVAILABLE)
        self.assertFalse(missing.exists())
        self.assertFalse(missing.parent.exists())
        self.assertIsNone(snapshot.metrics.total_processed)

    def test_corrupt_journal_fails_safely_without_replacing_bytes(self):
        corrupt = self.root / "corrupt.sqlite3"
        original = b"not a sqlite database"
        corrupt.write_bytes(original)

        snapshot = ProductReadModel(corrupt).read()

        self.assertIs(snapshot.state, ProductDataState.ERROR)
        self.assertEqual(corrupt.read_bytes(), original)
        self.assertEqual(snapshot.activity, ())

    def test_read_only_open_does_not_change_journal_rows(self):
        self._complete()
        before = self.journal.read_recent_operations()
        files_before = {path.name for path in self.database_path.parent.iterdir()}
        database_stat = self.database_path.stat()

        self.reader.read()

        after = self.journal.read_recent_operations()
        self.assertEqual(before, after)
        self.assertEqual(
            {path.name for path in self.database_path.parent.iterdir()},
            files_before,
        )
        self.assertEqual(self.database_path.stat().st_mtime_ns, database_stat.st_mtime_ns)

    def test_read_only_open_observes_active_wal_without_creating_sidecars(self):
        with closing(sqlite3.connect(self.database_path)) as writer:
            self.assertEqual(writer.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            operation = self._complete("active-wal.txt")
            self.assertTrue(Path(f"{self.database_path}-wal").exists())
            self.assertTrue(Path(f"{self.database_path}-shm").exists())

            snapshot = self.reader.read()

        self.assertIs(snapshot.state, ProductDataState.AVAILABLE)
        self.assertEqual(snapshot.activity[0].operation_id, operation.operation_id)

    def test_read_only_open_rejects_non_wal_database(self):
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0],
                "delete",
            )

        snapshot = self.reader.read()

        self.assertIs(snapshot.state, ProductDataState.ERROR)
        self.assertIn("WAL", snapshot.error)

    def test_read_only_open_rejects_inconsistent_sidecar_evidence(self):
        Path(f"{self.database_path}-shm").touch()

        snapshot = self.reader.read()

        self.assertIs(snapshot.state, ProductDataState.ERROR)
        self.assertIn("recovery", snapshot.error.lower())

    def test_category_is_persisted_not_inferred_from_archived_destination(self):
        operation = self.journal.create_operation(
            source_path=self.root / "incoming" / "archive.txt",
            organized_root=self.root / "organized",
            intended_destination=(
                self.root / "organized" / "documents" / "2026-10" / "archive.txt"
            ),
            category="documents",
            classification_method="smart",
            classification_source="content_or_filename",
        )

        record = self.reader.read().activity[0]

        self.assertEqual(record.operation_id, operation.operation_id)
        self.assertEqual(record.category, "documents")
        self.assertEqual(record.classification_method, "smart")
        self.assertEqual(record.classification_source, "content_or_filename")

    def test_service_reads_while_stopped_without_bootstrap_or_monitor_start(self):
        builder = Mock(side_effect=AssertionError("bootstrap must not run"))
        service = FilePilotService(
            config_path=self.root / "missing-config.json",
            monitor_builder=builder,
            journal_path=self.database_path,
        )

        snapshot = service.get_product_snapshot()

        self.assertIs(snapshot.state, ProductDataState.AVAILABLE)
        self.assertIs(service.monitor_state, MonitorState.STOPPED)
        self.assertIsNone(service.startup_result)
        builder.assert_not_called()

    def test_service_metrics_and_recent_activity_delegate_to_same_read_model(self):
        operation = self._complete()
        service = FilePilotService(journal_path=self.database_path)

        metrics = service.get_product_metrics()
        activity = service.get_recent_activity(limit=1)

        self.assertEqual(metrics.total_processed, 1)
        self.assertEqual(activity[0].operation_id, operation.operation_id)

    def test_inverse_operation_is_restored_and_not_counted_as_processed(self):
        original = self._complete("original.txt")
        inverse = self.journal.create_inverse_operation(
            original.operation_id,
            source_path=self.root / "organized" / "documents" / "original.txt",
            organized_root=self.root / "organized",
            intended_destination=self.root / "incoming" / "original.txt",
            source_identity="volume:file-id",
            source_hash="sha256:abc",
            source_size=12,
            source_mtime_ns=123,
        )
        self.journal.transition_phase(
            inverse.operation_id,
            PhysicalPhase.PREPARED,
            PhysicalPhase.RENAME_INTENT,
        )
        self.journal.transition_phase(
            inverse.operation_id,
            PhysicalPhase.RENAME_INTENT,
            PhysicalPhase.PHYSICAL_COMMITTED,
        )
        self.journal.complete_operation(inverse.operation_id)

        snapshot = self.reader.read()
        records = {record.operation_id: record for record in snapshot.activity}

        self.assertIs(records[inverse.operation_id].status, ActivityStatus.RESTORED)
        self.assertEqual(snapshot.metrics.total_processed, 1)

    def test_inverse_failures_and_review_operations_remain_visible_in_metrics(self):
        failed_original = self._complete("failed-original.txt")
        failed_inverse = self.journal.create_inverse_operation(
            failed_original.operation_id,
            source_path=self.root / "organized" / "documents" / "failed-original.txt",
            organized_root=self.root / "organized",
            intended_destination=self.root / "incoming" / "failed-original.txt",
            source_identity=None,
            source_hash=None,
            source_size=None,
            source_mtime_ns=None,
        )
        self.journal.transition_phase(
            failed_inverse.operation_id,
            PhysicalPhase.PREPARED,
            PhysicalPhase.ABORTED,
            error_code="UNDO_FAILED",
            error_message="Restore failed",
        )
        review_original = self._complete("review-original.txt")
        review_inverse = self.journal.create_inverse_operation(
            review_original.operation_id,
            source_path=self.root / "organized" / "documents" / "review-original.txt",
            organized_root=self.root / "organized",
            intended_destination=self.root / "incoming" / "review-original.txt",
            source_identity=None,
            source_hash=None,
            source_size=None,
            source_mtime_ns=None,
        )
        self.journal.mark_needs_review(
            review_inverse.operation_id,
            expected_phase=PhysicalPhase.PREPARED,
            error_code="UNDO_REVIEW",
            error_message="Restore evidence is ambiguous",
        )

        metrics = self.reader.read().metrics

        self.assertEqual(metrics.total_processed, 2)
        self.assertEqual(metrics.failed, 1)
        self.assertEqual(metrics.needs_review, 1)

    def test_reads_remain_available_when_startup_is_blocked(self):
        self._complete()
        config_path = self.root / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "organized_base_folder": str(self.root / "organized"),
                    "source_folder": str(self.root / "incoming"),
                }
            ),
            encoding="utf-8",
        )

        def blocked_builder():
            raise JournalError("recovery is blocked")

        service = FilePilotService(
            config_path=config_path,
            monitor_builder=blocked_builder,
            journal_path=self.database_path,
        )
        startup = service.bootstrap()

        self.assertEqual(startup.status.value, "blocked")
        self.assertIs(service.monitor_state, MonitorState.BLOCKED)
        self.assertEqual(service.get_product_metrics().total_processed, 1)


if __name__ == "__main__":
    unittest.main()
