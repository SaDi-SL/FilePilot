import errno
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app import hash_manager, mover
from app.operation_journal import (
    EffectType,
    OperationJournal,
    OperationStatus,
    PhysicalPhase,
)
from app.recovery import reconcile_incomplete_operations
from app.undo import UndoStatus, undo_operation


class SimulatedCrash(BaseException):
    pass


class UndoTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.incoming = self.root / "incoming"
        self.organized = self.root / "organized"
        self.documents = self.organized / "documents"
        self.incoming.mkdir()
        self.journal = OperationJournal(self.root / "data" / "operations.sqlite3")
        self.hash_db = self.root / "data" / "hashes.json"
        self.history = self.root / "data" / "history.csv"
        self.stats = self.root / "data" / "stats.json"
        with hash_manager._cache_lock:
            self.old_cache = hash_manager._cache
            self.old_cache_path = hash_manager._cache_path
            hash_manager._cache = None
            hash_manager._cache_path = None

    def tearDown(self):
        with hash_manager._cache_lock:
            hash_manager._cache = self.old_cache
            hash_manager._cache_path = self.old_cache_path
        self.temp_dir.cleanup()

    def _completed_move(self, content="undo content"):
        source = self.incoming / "report.txt"
        source.write_text(content, encoding="utf-8")
        result = mover.move_file_with_retries(
            source,
            {"documents": str(self.documents), "others": str(self.organized / "others")},
            {".txt": "documents"},
            str(self.stats),
            str(self.history),
            str(self.hash_db),
            False,
            {"documents": [".txt"]},
            self.organized,
            retries=1,
            delay=0,
            category_override="documents",
            journal=self.journal,
        )
        self.assertIs(result.status, mover.MoveStatus.MOVED)
        return source, result.destination, result.operation_id

    def _undo(self, operation_id):
        return undo_operation(
            self.journal,
            operation_id,
            hash_db_file=str(self.hash_db),
        )

    @contextmanager
    def _cross_device_restore(self, restore_source):
        primitive = "rename" if os.name == "nt" else "link"
        real = getattr(mover.os, primitive)

        def fail(path, destination, *args, **kwargs):
            if Path(path) == restore_source and Path(destination).is_relative_to(self.incoming):
                raise OSError(errno.EXDEV, "simulated cross-volume restore")
            return real(path, destination, *args, **kwargs)

        with patch.object(mover.os, primitive, side_effect=fail):
            yield

    @contextmanager
    def _crash_before_recording(self, phase):
        real = self.journal.transition_phase

        def transition(operation_id, expected_phase, new_phase, **kwargs):
            if new_phase is phase:
                raise SimulatedCrash(phase.value)
            return real(operation_id, expected_phase, new_phase, **kwargs)

        with patch.object(self.journal, "transition_phase", side_effect=transition):
            yield

    def test_successful_same_volume_undo(self):
        source, destination, operation_id = self._completed_move()
        result = self._undo(operation_id)
        self.assertIs(result.status, UndoStatus.SUCCESS)
        self.assertTrue(source.is_file())
        self.assertFalse(destination.exists())

    def test_successful_cross_volume_undo(self):
        source, destination, operation_id = self._completed_move()
        with self._cross_device_restore(destination):
            result = self._undo(operation_id)
        self.assertIs(result.status, UndoStatus.SUCCESS)
        self.assertEqual(source.read_text(encoding="utf-8"), "undo content")
        self.assertFalse(destination.exists())

    def test_original_source_occupied_never_overwrites(self):
        source, destination, operation_id = self._completed_move()
        source.write_text("new user data", encoding="utf-8")
        result = self._undo(operation_id)
        self.assertIs(result.status, UndoStatus.CONFLICT)
        self.assertEqual(source.read_text(encoding="utf-8"), "new user data")
        self.assertTrue(destination.exists())

    def test_replaced_destination_is_not_undone(self):
        source, destination, operation_id = self._completed_move()
        destination.unlink()
        destination.write_text("replacement", encoding="utf-8")
        result = self._undo(operation_id)
        self.assertIs(result.status, UndoStatus.NEEDS_REVIEW)
        self.assertEqual(destination.read_text(encoding="utf-8"), "replacement")
        self.assertFalse(source.exists())

    def test_missing_destination_is_safe_refusal(self):
        source, destination, operation_id = self._completed_move()
        destination.unlink()
        result = self._undo(operation_id)
        self.assertIs(result.status, UndoStatus.NOT_ELIGIBLE)
        self.assertFalse(source.exists())

    def test_incomplete_operation_is_not_eligible(self):
        operation = self.journal.create_operation(
            source_path=self.incoming / "x.txt",
            organized_root=self.organized,
            intended_destination=self.documents / "x.txt",
        )
        self.assertIs(self._undo(operation.operation_id).status, UndoStatus.NOT_ELIGIBLE)

    def test_needs_review_operation_is_not_eligible(self):
        operation = self.journal.create_operation(
            source_path=self.incoming / "x.txt",
            organized_root=self.organized,
            intended_destination=self.documents / "x.txt",
        )
        self.journal.mark_needs_review(
            operation.operation_id,
            expected_phase=PhysicalPhase.PREPARED,
            error_code="TEST_REVIEW",
            error_message="ambiguous",
        )
        self.assertIs(self._undo(operation.operation_id).status, UndoStatus.NOT_ELIGIBLE)

    def test_undo_twice_is_idempotent(self):
        source, _, operation_id = self._completed_move()
        first = self._undo(operation_id)
        second = self._undo(operation_id)
        self.assertIs(first.status, UndoStatus.SUCCESS)
        self.assertIs(second.status, UndoStatus.ALREADY_UNDONE)
        self.assertTrue(source.exists())

    def test_concurrent_undo_requests_restore_at_most_once(self):
        source, _, operation_id = self._completed_move()
        barrier = threading.Barrier(3)
        results = []

        def run():
            barrier.wait()
            results.append(self._undo(operation_id))

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(10)
        self.assertEqual(sum(r.status is UndoStatus.SUCCESS for r in results), 1)
        self.assertTrue(source.exists())
        inverses = [
            op for op in [self.journal.get_inverse_operation(operation_id)] if op is not None
        ]
        self.assertEqual(len(inverses), 1)

    def test_source_collision_appearing_during_undo_is_preserved(self):
        source, destination, operation_id = self._completed_move()
        real = mover._move_to_exact_destination

        def collide(*args, **kwargs):
            source.write_text("late user data", encoding="utf-8")
            return real(*args, **kwargs)

        with patch.object(mover, "_move_to_exact_destination", side_effect=collide):
            result = self._undo(operation_id)
        self.assertIs(result.status, UndoStatus.CONFLICT)
        self.assertEqual(source.read_text(encoding="utf-8"), "late user data")
        self.assertTrue(destination.exists())

    def test_destination_replacement_during_undo_is_preserved(self):
        source, destination, operation_id = self._completed_move()
        real = mover._move_to_exact_destination

        def replace(*args, **kwargs):
            destination.unlink()
            destination.write_text("late replacement", encoding="utf-8")
            return real(*args, **kwargs)

        with patch.object(mover, "_move_to_exact_destination", side_effect=replace):
            result = self._undo(operation_id)
        self.assertIs(result.status, UndoStatus.NEEDS_REVIEW)
        self.assertEqual(destination.read_text(encoding="utf-8"), "late replacement")
        self.assertFalse(source.exists())

    def test_same_content_replacement_during_same_volume_restore_is_rejected(self):
        source, destination, operation_id = self._completed_move()
        real = mover._move_no_clobber
        replaced = False

        def replace_before_move(path, target):
            nonlocal replaced
            if not replaced and Path(path) == destination and Path(target) == source:
                replaced = True
                content = destination.read_bytes()
                destination.unlink()
                destination.write_bytes(content)
            return real(path, target)

        with patch.object(mover, "_move_no_clobber", side_effect=replace_before_move):
            result = self._undo(operation_id)
        self.assertIs(result.status, UndoStatus.NEEDS_REVIEW)
        self.assertTrue(destination.exists())
        self.assertFalse(source.exists())

    def test_same_content_temporary_replacement_is_not_adopted(self):
        source, destination, operation_id = self._completed_move()
        real = mover._move_no_clobber
        replaced = False

        def replace_temp(path, target):
            nonlocal replaced
            path = Path(path)
            if not replaced and path.name.startswith(".filepilot-"):
                replaced = True
                content = path.read_bytes()
                path.unlink()
                path.write_bytes(content)
            return real(path, target)

        with self._cross_device_restore(destination), patch.object(
            mover, "_move_no_clobber", side_effect=replace_temp
        ):
            result = self._undo(operation_id)
        self.assertIs(result.status, UndoStatus.NEEDS_REVIEW)
        self.assertTrue(source.exists())
        self.assertTrue(destination.exists())

    def test_crash_before_physical_action_recovers_without_move(self):
        source, destination, operation_id = self._completed_move()
        with self._crash_before_recording(PhysicalPhase.RENAME_INTENT):
            with self.assertRaises(SimulatedCrash):
                self._undo(operation_id)
        reconcile_incomplete_operations(self.journal, hash_db_file=str(self.hash_db))
        self.assertFalse(source.exists())
        self.assertTrue(destination.exists())

    def test_crash_after_physical_action_is_recovered(self):
        source, destination, operation_id = self._completed_move()
        with self._crash_before_recording(PhysicalPhase.PHYSICAL_COMMITTED):
            with self.assertRaises(SimulatedCrash):
                self._undo(operation_id)
        self.assertTrue(source.exists())
        self.assertFalse(destination.exists())
        reconcile_incomplete_operations(self.journal, hash_db_file=str(self.hash_db))
        self.assertEqual(
            self.journal.get_inverse_operation(operation_id).operation_status,
            OperationStatus.COMPLETE,
        )

    def test_cross_volume_crash_after_restore_publication(self):
        source, destination, operation_id = self._completed_move()
        with self._cross_device_restore(destination), self._crash_before_recording(
            PhysicalPhase.DESTINATION_PUBLISHED
        ):
            with self.assertRaises(SimulatedCrash):
                self._undo(operation_id)
        self.assertTrue(source.exists())
        self.assertTrue(destination.exists())
        reconcile_incomplete_operations(self.journal, hash_db_file=str(self.hash_db))
        self.assertFalse(destination.exists())

    def test_cross_volume_crash_after_old_destination_staging(self):
        source, destination, operation_id = self._completed_move()
        with self._cross_device_restore(destination), self._crash_before_recording(
            PhysicalPhase.SOURCE_STAGED
        ):
            with self.assertRaises(SimulatedCrash):
                self._undo(operation_id)
        reconcile_incomplete_operations(self.journal, hash_db_file=str(self.hash_db))
        self.assertTrue(source.exists())
        self.assertFalse(destination.exists())

    def test_recovery_after_interrupted_undo_preserves_relationship(self):
        _, _, operation_id = self._completed_move()
        with self._crash_before_recording(PhysicalPhase.PHYSICAL_COMMITTED):
            with self.assertRaises(SimulatedCrash):
                self._undo(operation_id)
        reconcile_incomplete_operations(self.journal, hash_db_file=str(self.hash_db))
        inverse = self.journal.get_inverse_operation(operation_id)
        self.assertEqual(inverse.inverse_of_operation_id, operation_id)
        self.assertEqual(inverse.parent_operation_id, operation_id)

    def test_recovery_run_twice_after_undo_is_safe(self):
        source, _, operation_id = self._completed_move()
        with self._crash_before_recording(PhysicalPhase.PHYSICAL_COMMITTED):
            with self.assertRaises(SimulatedCrash):
                self._undo(operation_id)
        reconcile_incomplete_operations(self.journal, hash_db_file=str(self.hash_db))
        second = reconcile_incomplete_operations(
            self.journal, hash_db_file=str(self.hash_db)
        )
        self.assertEqual(second.reconciled_operation_ids, ())
        self.assertTrue(source.exists())

    def test_original_completed_move_record_is_preserved(self):
        _, _, operation_id = self._completed_move()
        original_before = self.journal.get_operation(operation_id)
        result = self._undo(operation_id)
        original_after = self.journal.get_operation(operation_id)
        self.assertIs(result.status, UndoStatus.SUCCESS)
        self.assertEqual(original_before, original_after)

    def test_undo_records_only_idempotent_hash_metadata(self):
        _, _, operation_id = self._completed_move()
        result = self._undo(operation_id)
        effects = self.journal.get_effects(result.undo_operation_id)
        self.assertEqual([effect.effect_type for effect in effects], [EffectType.HASH_INDEX])

    def test_hash_index_points_to_restored_source(self):
        source, _, operation_id = self._completed_move()
        result = self._undo(operation_id)
        self.assertIs(result.status, UndoStatus.SUCCESS)
        digest = hash_manager.calculate_file_hash(source)
        self.assertEqual(hash_manager.get_verified_file_path(digest, str(self.hash_db)), str(source))


if __name__ == "__main__":
    unittest.main()
