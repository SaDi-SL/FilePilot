import concurrent.futures
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app import operation_journal
from app.operation_journal import (
    APPLICATION_ID,
    SCHEMA_VERSION,
    EffectState,
    EffectType,
    JournalConflictError,
    JournalCorruptionError,
    JournalIncompatibleError,
    JournalPathError,
    JournalSchemaError,
    JournalValidationError,
    OperationJournal,
    OperationStatus,
    PhysicalPhase,
    TransitionEvidence,
    resolve_default_journal_path,
)


class OperationJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.database_path = self.root / "data" / "operations.sqlite3"
        self.repository_root = Path(__file__).resolve().parent.parent
        self.worker = Path(__file__).with_name("operation_journal_crash_worker.py")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _journal(self):
        return OperationJournal(self.database_path)

    def _create(self, journal=None, **overrides):
        journal = journal or self._journal()
        arguments = {
            "source_path": self.root / "incoming" / "report.txt",
            "organized_root": self.root / "organized",
            "intended_destination": (
                self.root / "organized" / "documents" / "report.txt"
            ),
        }
        arguments.update(overrides)
        return journal.create_operation(**arguments)

    def _sqlite_value(self, pragma):
        with closing(sqlite3.connect(self.database_path)) as connection:
            return connection.execute(f"PRAGMA {pragma}").fetchone()[0]

    def _run_worker(self, mode, expected_returncode):
        environment = os.environ.copy()
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = os.pathsep.join(
            value
            for value in (str(self.repository_root), existing_pythonpath)
            if value
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(self.worker),
                mode,
                str(self.database_path),
                str(self.root),
            ],
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
                f"worker returned {completed.returncode}\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            ),
        )

    def test_default_path_uses_injected_local_application_data(self):
        local_data = self.root / "LocalAppData"
        path = resolve_default_journal_path({"LOCALAPPDATA": str(local_data)})

        self.assertEqual(
            path,
            local_data / "FilePilot" / "data" / "operations.sqlite3",
        )
        self.assertFalse(path.exists())

    def test_default_path_rejects_missing_relative_and_network_locations(self):
        with self.assertRaises(JournalPathError):
            resolve_default_journal_path({})
        with self.assertRaises(JournalPathError):
            resolve_default_journal_path({"LOCALAPPDATA": "relative-data"})
        with self.assertRaises(JournalPathError):
            resolve_default_journal_path(
                {"LOCALAPPDATA": r"\\server\users\person\AppData\Local"}
            )

    def test_explicit_path_must_be_absolute(self):
        with self.assertRaises(JournalPathError):
            OperationJournal(Path("operations.sqlite3"))

    def test_explicit_network_path_is_rejected(self):
        with self.assertRaises(JournalPathError):
            OperationJournal(r"\\server\share\operations.sqlite3")

    def test_mapped_network_drive_is_detected(self):
        with patch.object(operation_journal, "_windows_drive_type", return_value=4):
            self.assertTrue(operation_journal._is_network_path(r"Z:\operations.sqlite3"))

    def test_explicit_temporary_path_initializes_database(self):
        journal = self._journal()

        self.assertEqual(journal.database_path, self.database_path)
        self.assertTrue(self.database_path.is_file())
        self.assertFalse(
            (self.repository_root / "operations.sqlite3").exists()
        )

    def test_database_identity_and_version_are_set(self):
        self._journal()

        self.assertEqual(self._sqlite_value("application_id"), APPLICATION_ID)
        self.assertEqual(self._sqlite_value("user_version"), SCHEMA_VERSION)

    def test_required_schema_and_indexes_exist(self):
        self._journal()
        with closing(sqlite3.connect(self.database_path)) as connection:
            objects = {
                (row[0], row[1])
                for row in connection.execute(
                    """
                    SELECT type, name
                    FROM sqlite_master
                    WHERE name NOT LIKE 'sqlite_%'
                    """
                )
            }

        self.assertTrue(
            {
                ("table", "operations"),
                ("table", "operation_events"),
                ("table", "operation_effects"),
                ("index", "idx_operations_status_updated"),
                ("index", "idx_operations_parent"),
                ("index", "idx_operations_inverse"),
                ("index", "idx_operation_events_operation_sequence"),
                ("index", "idx_operation_effects_state"),
            }.issubset(objects)
        )

    def test_requested_sqlite_pragmas_are_active(self):
        journal = self._journal()

        settings = journal.database_settings()

        self.assertEqual(settings["journal_mode"], "wal")
        self.assertEqual(settings["synchronous"], 2)
        self.assertEqual(settings["foreign_keys"], 1)
        self.assertEqual(settings["busy_timeout"], 5000)
        self.assertEqual(settings["locking_mode"], "normal")
        self.assertEqual(settings["quick_check"], "ok")

    def test_operation_creation_retrieval_and_initial_event(self):
        journal = self._journal()
        operation = self._create(
            journal,
            source_identity="volume:file-id",
            source_hash="sha256:abc",
            source_size=42,
            source_mtime_ns=123456789,
            application_version="0.6b-test",
        )

        loaded = journal.get_operation(operation.operation_id)
        events = journal.get_events(operation.operation_id)

        self.assertEqual(loaded, operation)
        self.assertEqual(loaded.physical_phase, PhysicalPhase.PREPARED)
        self.assertEqual(loaded.operation_status, OperationStatus.OPEN)
        self.assertEqual(loaded.source_size, 42)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].sequence_number, 1)
        self.assertEqual(events[0].to_phase, PhysicalPhase.PREPARED)

    def test_created_operation_ids_are_unique_uuids(self):
        journal = self._journal()
        identifiers = {
            self._create(
                journal,
                source_path=self.root / "incoming" / f"{number}.txt",
                intended_destination=(
                    self.root / "organized" / "documents" / f"{number}.txt"
                ),
            ).operation_id
            for number in range(25)
        }

        self.assertEqual(len(identifiers), 25)
        for identifier in identifiers:
            self.assertRegex(
                identifier,
                r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
                r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            )

    def test_incomplete_query_includes_physically_committed_operation(self):
        journal = self._journal()
        operation = self._create(journal)
        journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.PREPARED,
            PhysicalPhase.RENAME_INTENT,
        )
        journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.RENAME_INTENT,
            PhysicalPhase.PHYSICAL_COMMITTED,
        )

        incomplete = journal.list_incomplete_operations()

        self.assertEqual([item.operation_id for item in incomplete], [operation.operation_id])
        self.assertEqual(incomplete[0].operation_status, OperationStatus.OPEN)
        self.assertIsNotNone(incomplete[0].physically_committed_at_utc)
        self.assertIsNone(incomplete[0].completed_at_utc)

    def test_completion_requires_physical_commit_and_applied_required_effects(self):
        journal = self._journal()
        operation = self._create(journal)
        journal.initialize_effect(operation.operation_id, EffectType.HASH_INDEX)

        with self.assertRaises(JournalConflictError):
            journal.complete_operation(operation.operation_id)

        journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.PREPARED,
            PhysicalPhase.RENAME_INTENT,
        )
        journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.RENAME_INTENT,
            PhysicalPhase.PHYSICAL_COMMITTED,
        )
        with self.assertRaises(JournalConflictError):
            journal.complete_operation(operation.operation_id)

        journal.transition_effect(
            operation.operation_id,
            EffectType.HASH_INDEX,
            EffectState.NOT_STARTED,
            EffectState.PENDING,
        )
        journal.transition_effect(
            operation.operation_id,
            EffectType.HASH_INDEX,
            EffectState.PENDING,
            EffectState.APPLIED,
        )
        completed = journal.complete_operation(operation.operation_id)

        self.assertEqual(completed.operation_status, OperationStatus.COMPLETE)
        self.assertIsNotNone(completed.completed_at_utc)
        self.assertEqual(journal.list_incomplete_operations(), [])

    def test_same_volume_transition_path_is_legal_and_ordered(self):
        journal = self._journal()
        operation = self._create(journal)

        journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.PREPARED,
            PhysicalPhase.RENAME_INTENT,
        )
        committed = journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.RENAME_INTENT,
            PhysicalPhase.PHYSICAL_COMMITTED,
            evidence=TransitionEvidence(
                actual_destination=self.root / "organized" / "documents" / "report.txt",
                destination_identity="volume:destination-id",
                destination_hash="sha256:def",
                destination_size=42,
            ),
        )
        events = journal.get_events(operation.operation_id)

        self.assertEqual(committed.physical_phase, PhysicalPhase.PHYSICAL_COMMITTED)
        self.assertEqual(committed.move_mode.value, "same_volume")
        self.assertEqual([event.sequence_number for event in events], [1, 2, 3])
        self.assertEqual(
            [event.to_phase for event in events],
            [
                PhysicalPhase.PREPARED,
                PhysicalPhase.RENAME_INTENT,
                PhysicalPhase.PHYSICAL_COMMITTED,
            ],
        )

    def test_cross_volume_transition_path_is_legal(self):
        journal = self._journal()
        operation = self._create(journal)
        path = [
            PhysicalPhase.TEMP_CREATE_INTENT,
            PhysicalPhase.TEMP_CREATED,
            PhysicalPhase.TEMP_VERIFIED,
            PhysicalPhase.PUBLISH_INTENT,
            PhysicalPhase.DESTINATION_PUBLISHED,
            PhysicalPhase.DESTINATION_VERIFIED,
            PhysicalPhase.SOURCE_STAGE_INTENT,
            PhysicalPhase.SOURCE_STAGED,
            PhysicalPhase.SOURCE_DELETE_INTENT,
            PhysicalPhase.PHYSICAL_COMMITTED,
        ]
        current = PhysicalPhase.PREPARED

        for new_phase in path:
            journal.transition_phase(
                operation.operation_id,
                current,
                new_phase,
            )
            current = new_phase

        loaded = journal.get_operation(operation.operation_id)
        self.assertEqual(loaded.physical_phase, PhysicalPhase.PHYSICAL_COMMITTED)
        self.assertEqual(loaded.move_mode.value, "cross_volume")
        self.assertIsNotNone(loaded.published_at_utc)

    def test_illegal_and_untyped_phase_transitions_are_rejected(self):
        journal = self._journal()
        operation = self._create(journal)

        with self.assertRaises(JournalValidationError):
            journal.transition_phase(
                operation.operation_id,
                PhysicalPhase.PREPARED,
                PhysicalPhase.PHYSICAL_COMMITTED,
            )
        with self.assertRaises(JournalValidationError):
            journal.transition_phase(
                operation.operation_id,
                "prepared",
                PhysicalPhase.RENAME_INTENT,
            )

        self.assertEqual(
            journal.get_operation(operation.operation_id).physical_phase,
            PhysicalPhase.PREPARED,
        )

    def test_expected_phase_conflict_does_not_append_event(self):
        journal = self._journal()
        operation = self._create(journal)
        journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.PREPARED,
            PhysicalPhase.RENAME_INTENT,
        )

        with self.assertRaises(JournalConflictError):
            journal.transition_phase(
                operation.operation_id,
                PhysicalPhase.PREPARED,
                PhysicalPhase.TEMP_CREATE_INTENT,
            )

        self.assertEqual(len(journal.get_events(operation.operation_id)), 2)

    def test_transition_and_event_roll_back_together(self):
        journal = self._journal()
        operation = self._create(journal)
        with patch.object(
            journal,
            "_append_event",
            side_effect=sqlite3.IntegrityError("event rejected"),
        ):
            with self.assertRaises(operation_journal.JournalDatabaseError):
                journal.transition_phase(
                    operation.operation_id,
                    PhysicalPhase.PREPARED,
                    PhysicalPhase.RENAME_INTENT,
                )

        self.assertEqual(
            journal.get_operation(operation.operation_id).physical_phase,
            PhysicalPhase.PREPARED,
        )
        self.assertEqual(len(journal.get_events(operation.operation_id)), 1)

    def test_abort_and_needs_review_are_terminal(self):
        for terminal in (PhysicalPhase.ABORTED, PhysicalPhase.NEEDS_REVIEW):
            with self.subTest(terminal=terminal):
                journal = self._journal()
                operation = self._create(
                    journal,
                    source_path=self.root / "incoming" / f"{terminal.value}.txt",
                    intended_destination=(
                        self.root / "organized" / f"{terminal.value}.txt"
                    ),
                )
                result = journal.transition_phase(
                    operation.operation_id,
                    PhysicalPhase.PREPARED,
                    terminal,
                    error_code="MOVE_STOPPED",
                    error_message="controlled failure",
                )
                with self.assertRaises(JournalValidationError):
                    journal.transition_phase(
                        operation.operation_id,
                        terminal,
                        PhysicalPhase.RENAME_INTENT,
                    )
                expected_status = (
                    OperationStatus.ABORTED
                    if terminal is PhysicalPhase.ABORTED
                    else OperationStatus.NEEDS_REVIEW
                )
                self.assertEqual(result.operation_status, expected_status)

    def test_duplicate_is_an_outcome_not_a_physical_phase(self):
        journal = self._journal()
        operation = self._create(journal)
        duplicate_path = self.root / "organized" / "existing.txt"

        result = journal.mark_duplicate(
            operation.operation_id,
            expected_phase=PhysicalPhase.PREPARED,
            duplicate_of_path=duplicate_path,
        )

        self.assertEqual(result.physical_phase, PhysicalPhase.PREPARED)
        self.assertEqual(result.operation_status, OperationStatus.DUPLICATE)
        self.assertEqual(result.duplicate_of_path, str(duplicate_path))
        self.assertEqual(journal.list_incomplete_operations(), [])

    def test_effect_is_unique_and_has_validated_transitions(self):
        journal = self._journal()
        operation = self._create(journal)
        effect = journal.initialize_effect(
            operation.operation_id,
            EffectType.HASH_INDEX,
            required=True,
        )

        self.assertEqual(effect.state, EffectState.NOT_STARTED)
        with self.assertRaises(JournalConflictError):
            journal.initialize_effect(
                operation.operation_id,
                EffectType.HASH_INDEX,
            )

        pending = journal.transition_effect(
            operation.operation_id,
            EffectType.HASH_INDEX,
            EffectState.NOT_STARTED,
            EffectState.PENDING,
        )
        applied = journal.transition_effect(
            operation.operation_id,
            EffectType.HASH_INDEX,
            EffectState.PENDING,
            EffectState.APPLIED,
        )

        self.assertEqual(pending.state, EffectState.PENDING)
        self.assertEqual(applied.state, EffectState.APPLIED)
        with self.assertRaises(JournalValidationError):
            journal.transition_effect(
                operation.operation_id,
                EffectType.HASH_INDEX,
                EffectState.APPLIED,
                EffectState.PENDING,
            )

    def test_effect_failure_uses_structured_sanitized_error(self):
        journal = self._journal()
        operation = self._create(journal)
        journal.initialize_effect(
            operation.operation_id,
            EffectType.LEGACY_STATISTICS,
            initial_state=EffectState.PENDING,
        )

        effect = journal.transition_effect(
            operation.operation_id,
            EffectType.LEGACY_STATISTICS,
            EffectState.PENDING,
            EffectState.FAILED,
            error_code="WRITE_FAILED",
            error_message="first line\nsecond\tline\x00secret",
        )

        self.assertEqual(effect.error_code, "WRITE_FAILED")
        self.assertEqual(effect.error_message, "first line second line secret")
        self.assertNotIn("\n", effect.error_message)
        self.assertNotIn("\x00", effect.error_message)

    def test_parent_and_inverse_relationships_are_preserved(self):
        journal = self._journal()
        parent = self._create(journal)
        child = self._create(
            journal,
            source_path=self.root / "incoming" / "child.txt",
            intended_destination=self.root / "organized" / "child.txt",
            parent_operation_id=parent.operation_id,
            inverse_of_operation_id=parent.operation_id,
        )

        loaded = journal.get_operation(child.operation_id)

        self.assertEqual(loaded.parent_operation_id, parent.operation_id)
        self.assertEqual(loaded.inverse_of_operation_id, parent.operation_id)

    def test_timestamps_are_fixed_width_utc_and_sortable(self):
        journal = self._journal()
        operation = self._create(journal)
        updated = journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.PREPARED,
            PhysicalPhase.RENAME_INTENT,
        )

        pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
        self.assertRegex(operation.started_at_utc, pattern)
        self.assertRegex(updated.updated_at_utc, pattern)
        self.assertLessEqual(operation.started_at_utc, updated.updated_at_utc)
        parsed = datetime.fromisoformat(updated.updated_at_utc.replace("Z", "+00:00"))
        self.assertIsNotNone(parsed.tzinfo)

    def test_sql_values_are_parameterized(self):
        journal = self._journal()
        hostile_name = "report'); DROP TABLE operations; --.txt"
        operation = self._create(
            journal,
            source_path=self.root / "incoming" / hostile_name,
            intended_destination=self.root / "organized" / hostile_name,
        )

        self.assertIn("DROP TABLE", journal.get_operation(operation.operation_id).source_path)
        self.assertEqual(len(journal.list_incomplete_operations()), 1)

    def test_concurrent_operation_creation_is_consistent(self):
        journal = self._journal()

        def create(number):
            return self._create(
                journal,
                source_path=self.root / "incoming" / f"thread-{number}.txt",
                intended_destination=self.root / "organized" / f"thread-{number}.txt",
            ).operation_id

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            identifiers = list(executor.map(create, range(40)))

        self.assertEqual(len(set(identifiers)), 40)
        self.assertEqual(len(journal.list_incomplete_operations()), 40)

    def test_only_one_competing_transition_writer_succeeds(self):
        journal = self._journal()
        operation = self._create(journal)
        barrier = threading.Barrier(2)

        def transition():
            barrier.wait(timeout=5)
            try:
                journal.transition_phase(
                    operation.operation_id,
                    PhysicalPhase.PREPARED,
                    PhysicalPhase.RENAME_INTENT,
                )
                return "success"
            except JournalConflictError:
                return "conflict"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _number: transition(), range(2)))

        self.assertCountEqual(results, ["success", "conflict"])
        self.assertEqual(len(journal.get_events(operation.operation_id)), 2)

    def test_reopen_after_normal_close_preserves_data(self):
        first = self._journal()
        operation = self._create(first)

        reopened = OperationJournal(self.database_path)

        self.assertEqual(reopened.get_operation(operation.operation_id), operation)

    def test_committed_data_survives_abrupt_subprocess_exit(self):
        self._journal()

        self._run_worker("committed", 83)

        operation_id = (self.root / "committed-operation-id.txt").read_text(
            encoding="ascii"
        )
        reopened = OperationJournal(self.database_path)
        operation = reopened.get_operation(operation_id)
        self.assertEqual(operation.physical_phase, PhysicalPhase.RENAME_INTENT)
        self.assertEqual(len(reopened.get_events(operation_id)), 2)
        self.assertEqual(reopened.database_settings()["quick_check"], "ok")

    def test_uncommitted_data_is_rolled_back_after_abrupt_exit(self):
        journal = self._journal()
        operation = self._create(journal)
        (self.root / "uncommitted-operation-id.txt").write_text(
            operation.operation_id,
            encoding="ascii",
        )

        self._run_worker("uncommitted", 84)

        self.assertTrue((self.root / "uncommitted-write-reached.txt").exists())
        reopened = OperationJournal(self.database_path)
        loaded = reopened.get_operation(operation.operation_id)
        self.assertEqual(loaded.physical_phase, PhysicalPhase.PREPARED)
        self.assertEqual(len(reopened.get_events(operation.operation_id)), 1)
        self.assertEqual(reopened.database_settings()["quick_check"], "ok")

    def test_unrelated_application_id_is_rejected_without_overwrite(self):
        self.database_path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("PRAGMA application_id = 12345")
            connection.execute("PRAGMA user_version = 1")
            connection.execute("CREATE TABLE unrelated (value TEXT)")

        with self.assertRaises(JournalIncompatibleError):
            OperationJournal(self.database_path)

        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA application_id").fetchone()[0],
                12345,
            )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'unrelated'"
                ).fetchone()
            )

    def test_newer_user_version_is_rejected_without_downgrade(self):
        self._journal()
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")

        with self.assertRaises(JournalIncompatibleError):
            OperationJournal(self.database_path)

        self.assertEqual(self._sqlite_value("user_version"), SCHEMA_VERSION + 1)

    def test_missing_current_schema_is_rejected_without_recreation(self):
        self._journal()
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("DROP TABLE operation_effects")

        with self.assertRaises(JournalSchemaError):
            OperationJournal(self.database_path)

        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertIsNone(
                connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name = 'operation_effects'
                    """
                ).fetchone()
            )

    def test_incompatible_index_definition_is_rejected(self):
        self._journal()
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("DROP INDEX idx_operations_status_updated")
            connection.execute(
                "CREATE INDEX idx_operations_status_updated ON operations(source_path)"
            )

        with self.assertRaises(JournalSchemaError):
            OperationJournal(self.database_path)

    def test_existing_instance_revalidates_identity_and_version(self):
        for pragma, value in (
            ("application_id", 12345),
            ("user_version", SCHEMA_VERSION + 1),
        ):
            with self.subTest(pragma=pragma):
                database_path = self.root / pragma / "operations.sqlite3"
                journal = OperationJournal(database_path)
                with closing(sqlite3.connect(database_path)) as connection:
                    connection.execute(f"PRAGMA {pragma} = {value}")

                with self.assertRaises(JournalIncompatibleError):
                    journal.list_incomplete_operations()

    def test_write_revalidates_identity_after_acquiring_lock(self):
        journal = self._journal()
        original_validator = journal._validate_current_identity
        validation_count = 0

        def replace_identity_after_connection_check(connection):
            nonlocal validation_count
            original_validator(connection)
            validation_count += 1
            if validation_count == 1:
                with closing(sqlite3.connect(self.database_path)) as other:
                    other.execute("PRAGMA application_id = 12345")

        with patch.object(
            journal,
            "_validate_current_identity",
            side_effect=replace_identity_after_connection_check,
        ):
            with self.assertRaises(JournalIncompatibleError):
                self._create(journal)

        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0],
                0,
            )

    def test_read_revalidates_identity_inside_snapshot(self):
        journal = self._journal()
        operation = self._create(journal)
        original_validator = journal._validate_current_identity
        validation_count = 0

        def replace_version_after_connection_check(connection):
            nonlocal validation_count
            original_validator(connection)
            validation_count += 1
            if validation_count == 1:
                with closing(sqlite3.connect(self.database_path)) as other:
                    other.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")

        with patch.object(
            journal,
            "_validate_current_identity",
            side_effect=replace_version_after_connection_check,
        ):
            with self.assertRaises(JournalIncompatibleError):
                journal.get_operation(operation.operation_id)

    def test_schema_validation_preserves_check_literal_case(self):
        self.database_path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.database_path)) as connection:
            for statement in operation_journal._SCHEMA_STATEMENTS:
                if "CREATE TABLE operations" in statement:
                    statement = statement.replace("'move'", "'MOVE'", 1)
                connection.execute(statement)
            connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        with self.assertRaises(JournalSchemaError):
            OperationJournal(self.database_path)

    def test_unexpected_trigger_is_rejected(self):
        self._journal()
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                CREATE TRIGGER alter_created_phase
                AFTER INSERT ON operations
                BEGIN
                    UPDATE operations
                    SET physical_phase = 'rename_intent'
                    WHERE operation_id = NEW.operation_id;
                END
                """
            )

        with self.assertRaises(JournalSchemaError):
            OperationJournal(self.database_path)

    def test_existing_instance_rejects_later_trigger_before_write(self):
        journal = self._journal()
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                CREATE TRIGGER alter_created_phase
                AFTER INSERT ON operations
                BEGIN
                    UPDATE operations
                    SET physical_phase = 'rename_intent'
                    WHERE operation_id = NEW.operation_id;
                END
                """
            )

        with self.assertRaises(JournalSchemaError):
            self._create(journal)

        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0],
                0,
            )

    def test_random_bytes_are_rejected_without_destructive_reset(self):
        self.database_path.parent.mkdir(parents=True)
        original = b"not a sqlite database\x00with preserved bytes"
        self.database_path.write_bytes(original)

        with self.assertRaises(JournalCorruptionError):
            OperationJournal(self.database_path)

        self.assertEqual(self.database_path.read_bytes(), original)

    def test_malformed_truncated_database_is_rejected_without_reset(self):
        self._journal()
        original = self.database_path.read_bytes()[:128]
        self.database_path.write_bytes(original)

        with self.assertRaises(JournalCorruptionError):
            OperationJournal(self.database_path)

        self.assertEqual(self.database_path.read_bytes(), original)

    def test_failed_initial_migration_does_not_advance_identity_or_version(self):
        def failing_migration(connection):
            connection.execute("CREATE TABLE partial_schema (value TEXT)")
            raise sqlite3.OperationalError("migration failed")

        with patch.dict(operation_journal._MIGRATIONS, {1: failing_migration}, clear=True):
            with self.assertRaises(operation_journal.JournalDatabaseError):
                OperationJournal(self.database_path)

        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA application_id").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                0,
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'partial_schema'"
                ).fetchone()
            )

    def test_invalid_initial_migration_is_rolled_back_before_commit(self):
        def incomplete_migration(connection):
            connection.execute("CREATE TABLE partial_schema (value TEXT)")

        with patch.dict(
            operation_journal._MIGRATIONS,
            {1: incomplete_migration},
            clear=True,
        ):
            with self.assertRaises(JournalSchemaError):
                OperationJournal(self.database_path)

        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'partial_schema'"
                ).fetchone()
            )


if __name__ == "__main__":
    unittest.main()
