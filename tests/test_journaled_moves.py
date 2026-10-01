import errno
import os
import tempfile
import threading
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from app import hash_manager, mover
from app.operation_journal import (
    EffectState,
    EffectType,
    JournalDatabaseError,
    MoveMode,
    OperationJournal,
    OperationStatus,
    PhysicalPhase,
)


class JournaledMoveTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.incoming = self.root / "incoming"
        self.organized = self.root / "organized"
        self.documents = self.organized / "documents"
        self.incoming.mkdir()
        self.journal = OperationJournal(self.root / "data" / "operations.sqlite3")
        self.hash_db = self.root / "reports" / "hashes.json"
        self.history = self.root / "reports" / "history.csv"
        self.stats = self.root / "reports" / "stats.json"
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

    def _source(self, name="report.txt", content="journaled content"):
        source = self.incoming / name
        source.write_text(content, encoding="utf-8")
        return source

    def _move(self, source, journal=None):
        return mover.move_file_with_retries(
            source_file=source,
            destination_folders={
                "documents": str(self.documents),
                "others": str(self.organized / "others"),
            },
            extension_lookup={".txt": "documents"},
            stats_file=str(self.stats),
            history_file=str(self.history),
            hash_db_file=str(self.hash_db),
            archive_by_date=False,
            rules={"documents": [".txt"]},
            organized_root=self.organized,
            retries=1,
            delay=0,
            category_override="documents",
            journal=self.journal if journal is None else journal,
        )

    @contextmanager
    def _cross_device(self, source):
        primitive_name = "rename" if os.name == "nt" else "link"
        real_primitive = getattr(mover.os, primitive_name)

        def fail_initial_move(path, destination, *args, **kwargs):
            if Path(path) == source and Path(destination).is_relative_to(self.organized):
                raise OSError(errno.EXDEV, "simulated cross-volume move")
            return real_primitive(path, destination, *args, **kwargs)

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(mover.os, primitive_name, side_effect=fail_initial_move)
            )
            yield

    def test_same_volume_move_records_complete_operation_and_effects(self):
        source = self._source()

        result = self._move(source)

        operation = self.journal.get_operation(result.operation_id)
        phases = [
            event.to_phase
            for event in self.journal.get_events(result.operation_id)
            if event.to_phase is not None
        ]
        effects = {
            effect.effect_type: effect.state
            for effect in self.journal.get_effects(result.operation_id)
        }
        contexts = self.journal.read_recent_operations().contexts
        self.assertIs(result.status, mover.MoveStatus.MOVED)
        self.assertFalse(source.exists())
        self.assertEqual(operation.operation_status, OperationStatus.COMPLETE)
        self.assertEqual(operation.physical_phase, PhysicalPhase.PHYSICAL_COMMITTED)
        self.assertEqual(operation.move_mode, MoveMode.SAME_VOLUME)
        self.assertEqual(
            phases[:3],
            [
                PhysicalPhase.PREPARED,
                PhysicalPhase.RENAME_INTENT,
                PhysicalPhase.PHYSICAL_COMMITTED,
            ],
        )
        self.assertEqual(
            effects,
            {
                EffectType.HASH_INDEX: EffectState.APPLIED,
                EffectType.LEGACY_CSV_HISTORY: EffectState.APPLIED,
                EffectType.LEGACY_STATISTICS: EffectState.APPLIED,
            },
        )
        self.assertEqual(self.journal.list_incomplete_operations(), [])
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0].operation_id, result.operation_id)
        self.assertEqual(contexts[0].category, "documents")
        self.assertEqual(contexts[0].classification_method, "extension")

    def test_cross_volume_move_records_every_physical_boundary(self):
        source = self._source(content="cross-volume journal")

        with self._cross_device(source):
            result = self._move(source)

        operation = self.journal.get_operation(result.operation_id)
        phases = [
            event.to_phase
            for event in self.journal.get_events(result.operation_id)
            if event.to_phase is not None
        ]
        self.assertIs(result.status, mover.MoveStatus.MOVED)
        self.assertEqual(operation.operation_status, OperationStatus.COMPLETE)
        self.assertEqual(operation.move_mode, MoveMode.CROSS_VOLUME)
        self.assertEqual(
            phases[:12],
            [
                PhysicalPhase.PREPARED,
                PhysicalPhase.RENAME_INTENT,
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
            ],
        )
        self.assertIsNotNone(operation.temp_path)
        self.assertIsNotNone(operation.temp_identity)
        self.assertIsNotNone(operation.staging_path)
        self.assertIsNotNone(operation.staging_identity)

    def test_journal_creation_failure_prevents_filesystem_move(self):
        source = self._source(content="must stay")
        destination = self.documents / source.name
        broken = unittest.mock.Mock(spec=OperationJournal)
        broken.create_operation.side_effect = JournalDatabaseError("journal unavailable")

        result = self._move(source, journal=broken)

        self.assertIs(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertIn("journal initialization failed", result.error.lower())
        self.assertEqual(source.read_text(encoding="utf-8"), "must stay")
        self.assertFalse(destination.exists())

    def test_post_rename_journal_failure_never_retries_destructive_move(self):
        source = self._source(content="committed bytes")
        original_transition = self.journal.transition_phase
        failed_once = False

        def fail_commit(operation_id, expected_phase, new_phase, **kwargs):
            nonlocal failed_once
            if new_phase is PhysicalPhase.PHYSICAL_COMMITTED and not failed_once:
                failed_once = True
                raise JournalDatabaseError("simulated post-rename failure")
            return original_transition(
                operation_id,
                expected_phase,
                new_phase,
                **kwargs,
            )

        with patch.object(self.journal, "transition_phase", side_effect=fail_commit):
            result = self._move(source)

        operation = self.journal.get_operation(result.operation_id)
        self.assertIs(result.status, mover.MoveStatus.MOVED)
        self.assertFalse(source.exists())
        self.assertEqual(result.destination.read_text(encoding="utf-8"), "committed bytes")
        self.assertEqual(operation.operation_status, OperationStatus.NEEDS_REVIEW)
        self.assertEqual(operation.physical_phase, PhysicalPhase.NEEDS_REVIEW)
        self.assertNotIn("(1)", result.destination.name)

    def test_transient_same_volume_failure_retries_with_fresh_intent(self):
        source = self._source(content="retry safely")
        original_move = mover._move_no_clobber
        attempts = 0

        def fail_once(path, destination):
            nonlocal attempts
            if Path(path) == source:
                attempts += 1
                if attempts == 1:
                    raise PermissionError("simulated transient lock")
            return original_move(path, destination)

        with (
            patch.object(mover, "_move_no_clobber", side_effect=fail_once),
            patch.object(mover.time, "sleep"),
        ):
            result = mover.move_file_with_retries(
                source_file=source,
                destination_folders={
                    "documents": str(self.documents),
                    "others": str(self.organized / "others"),
                },
                extension_lookup={".txt": "documents"},
                stats_file=str(self.stats),
                history_file=str(self.history),
                hash_db_file=str(self.hash_db),
                archive_by_date=False,
                rules={"documents": [".txt"]},
                organized_root=self.organized,
                retries=2,
                delay=0,
                category_override="documents",
                journal=self.journal,
            )

        phases = [
            event.to_phase
            for event in self.journal.get_events(result.operation_id)
            if event.to_phase is not None
        ]
        self.assertIs(result.status, mover.MoveStatus.MOVED)
        self.assertEqual(attempts, 2)
        self.assertEqual(
            phases[:4],
            [
                PhysicalPhase.PREPARED,
                PhysicalPhase.RENAME_INTENT,
                PhysicalPhase.RENAME_INTENT,
                PhysicalPhase.PHYSICAL_COMMITTED,
            ],
        )

    def test_concurrent_same_source_is_not_reported_as_retained_duplicate(self):
        source = self._source(content="one source instance")
        original_hash = mover.calculate_file_hash
        initial_hashes = threading.Barrier(2)
        results = []
        errors = []

        def synchronize_initial_hash(path):
            digest = original_hash(path)
            if Path(path) == source:
                initial_hashes.wait(timeout=5)
            return digest

        def run_move():
            try:
                results.append(self._move(source))
            except Exception as error:
                errors.append(error)

        with patch.object(
            mover,
            "calculate_file_hash",
            side_effect=synchronize_initial_hash,
        ):
            threads = [threading.Thread(target=run_move) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertCountEqual(
            [result.status for result in results],
            [mover.MoveStatus.MOVED, mover.MoveStatus.MOVE_FAILED],
        )
        failed = next(
            result for result in results
            if result.status is mover.MoveStatus.MOVE_FAILED
        )
        self.assertIn("disappeared before move", failed.error)
        self.assertIsNone(failed.operation_id)
        self.assertFalse(source.exists())

    def test_cross_volume_source_removal_failure_requires_review(self):
        source = self._source(content="preserve both")
        real_unlink = mover.os.unlink

        def fail_staged_unlink(path, *args, **kwargs):
            if Path(path).parent.name.startswith(".filepilot-remove-"):
                raise PermissionError("simulated locked source")
            return real_unlink(path, *args, **kwargs)

        with (
            self._cross_device(source),
            patch.object(mover.os, "unlink", side_effect=fail_staged_unlink),
        ):
            result = self._move(source)

        operation = self.journal.get_operation(result.operation_id)
        self.assertIs(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertTrue(source.exists())
        self.assertTrue(result.destination.exists())
        self.assertEqual(operation.operation_status, OperationStatus.NEEDS_REVIEW)

    def test_destination_change_after_delete_intent_restores_source(self):
        source = self._source(content="preserve after intent")
        original_transition = self.journal.transition_phase

        def replace_after_delete_intent(
            operation_id,
            expected_phase,
            new_phase,
            **kwargs,
        ):
            operation = original_transition(
                operation_id,
                expected_phase,
                new_phase,
                **kwargs,
            )
            if new_phase is PhysicalPhase.SOURCE_DELETE_INTENT:
                Path(operation.actual_destination).write_text(
                    "external replacement",
                    encoding="utf-8",
                )
            return operation

        with (
            self._cross_device(source),
            patch.object(
                self.journal,
                "transition_phase",
                side_effect=replace_after_delete_intent,
            ),
        ):
            result = self._move(source)

        operation = self.journal.get_operation(result.operation_id)
        self.assertIs(result.status, mover.MoveStatus.MOVE_FAILED)
        self.assertEqual(
            source.read_text(encoding="utf-8"),
            "preserve after intent",
        )
        self.assertEqual(
            result.destination.read_text(encoding="utf-8"),
            "external replacement",
        )
        self.assertEqual(operation.operation_status, OperationStatus.NEEDS_REVIEW)

    def test_metadata_failure_remains_visible_and_operation_stays_incomplete(self):
        source = self._source(content="physical commit survives")

        with patch("app.mover.append_history", side_effect=OSError("history offline")):
            result = self._move(source)

        operation = self.journal.get_operation(result.operation_id)
        effects = {
            effect.effect_type: effect.state
            for effect in self.journal.get_effects(result.operation_id)
        }
        self.assertIs(result.status, mover.MoveStatus.MOVED)
        self.assertEqual(operation.physical_phase, PhysicalPhase.PHYSICAL_COMMITTED)
        self.assertEqual(operation.operation_status, OperationStatus.OPEN)
        self.assertEqual(
            effects[EffectType.LEGACY_CSV_HISTORY],
            EffectState.FAILED,
        )
        self.assertIn("History update failed", result.metadata_error)
        self.assertEqual(
            [item.operation_id for item in self.journal.list_incomplete_operations()],
            [result.operation_id],
        )


if __name__ == "__main__":
    unittest.main()
