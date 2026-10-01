import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import hash_manager
from app.operation_journal import (
    EffectState,
    EffectType,
    JournalDatabaseError,
    OperationJournal,
    OperationStatus,
    PhysicalPhase,
    TransitionEvidence,
)
from app.recovery import (
    RecoveryAssessment,
    RecoveryReport,
    RecoveryDecision,
    assess_operation,
    reconcile_incomplete_operations,
)


CONTENT = b"FilePilot safe recovery content"


def _identity(path: Path) -> str:
    state = path.stat(follow_symlinks=False)
    return f"stat-v1:{state.st_dev}:{state.st_ino}"


class RecoveryTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.incoming = self.root / "incoming"
        self.organized = self.root / "organized"
        self.documents = self.organized / "documents"
        self.source = self.incoming / "report.txt"
        self.destination = self.documents / "report.txt"
        self.hash_db = self.root / "reports" / "hashes.json"
        self.incoming.mkdir()
        self.documents.mkdir(parents=True)
        self.source.write_bytes(CONTENT)
        self.expected_hash = hashlib.sha256(CONTENT).hexdigest()
        self.journal = OperationJournal(self.root / "data" / "operations.sqlite3")
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

    def _operation(self, *, effects=False, effects_applied=False):
        source_state = self.source.stat()
        operation = self.journal.create_operation(
            source_path=self.source,
            organized_root=self.organized,
            intended_destination=self.destination,
            source_identity=_identity(self.source),
            source_hash=self.expected_hash,
            source_size=source_state.st_size,
            source_mtime_ns=source_state.st_mtime_ns,
        )
        if effects:
            for effect_type in (
                EffectType.HASH_INDEX,
                EffectType.LEGACY_CSV_HISTORY,
                EffectType.LEGACY_STATISTICS,
            ):
                self.journal.initialize_effect(operation.operation_id, effect_type)
                if effects_applied:
                    self.journal.transition_effect(
                        operation.operation_id,
                        effect_type,
                        EffectState.NOT_STARTED,
                        EffectState.PENDING,
                    )
                    self.journal.transition_effect(
                        operation.operation_id,
                        effect_type,
                        EffectState.PENDING,
                        EffectState.APPLIED,
                    )
        return self.journal.get_operation(operation.operation_id)

    def _rename_intent(self, operation):
        return self.journal.transition_phase(
            operation.operation_id,
            operation.physical_phase,
            PhysicalPhase.RENAME_INTENT,
            evidence=TransitionEvidence(actual_destination=self.destination),
        )

    def _temp_created(self, operation, *, verified=False):
        operation = self._rename_intent(operation)
        operation = self.journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.RENAME_INTENT,
            PhysicalPhase.TEMP_CREATE_INTENT,
        )
        temporary = self.documents / ".filepilot-recovery.tmp"
        temporary.write_bytes(CONTENT if verified else CONTENT[:7])
        operation = self.journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.TEMP_CREATE_INTENT,
            PhysicalPhase.TEMP_CREATED,
            evidence=TransitionEvidence(
                temp_path=temporary,
                temp_identity=_identity(temporary),
            ),
        )
        if verified:
            operation = self.journal.transition_phase(
                operation.operation_id,
                PhysicalPhase.TEMP_CREATED,
                PhysicalPhase.TEMP_VERIFIED,
                evidence=TransitionEvidence(
                    temp_path=temporary,
                    temp_identity=_identity(temporary),
                ),
            )
        return operation, temporary

    def _publish_intent(self, operation):
        operation, temporary = self._temp_created(operation, verified=True)
        operation = self.journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.TEMP_VERIFIED,
            PhysicalPhase.PUBLISH_INTENT,
            evidence=TransitionEvidence(
                actual_destination=self.destination,
                temp_path=temporary,
                temp_identity=_identity(temporary),
            ),
        )
        os.rename(temporary, self.destination)
        return operation, temporary

    def _destination_verified(self, operation):
        operation, _ = self._publish_intent(operation)
        destination_state = self.destination.stat(follow_symlinks=False)
        evidence = TransitionEvidence(
            actual_destination=self.destination,
            destination_identity=_identity(self.destination),
            destination_hash=self.expected_hash,
            destination_size=destination_state.st_size,
        )
        operation = self.journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.PUBLISH_INTENT,
            PhysicalPhase.DESTINATION_PUBLISHED,
            evidence=evidence,
        )
        operation = self.journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.DESTINATION_PUBLISHED,
            PhysicalPhase.DESTINATION_VERIFIED,
            evidence=evidence,
        )
        return operation

    def _source_staged(self, operation, *, observed=True):
        operation = self._destination_verified(operation)
        staging_dir = self.incoming / ".filepilot-remove-recovery"
        staging_dir.mkdir()
        staged = staging_dir / self.source.name
        operation = self.journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.DESTINATION_VERIFIED,
            PhysicalPhase.SOURCE_STAGE_INTENT,
            evidence=TransitionEvidence(
                actual_destination=self.destination,
                staging_path=staged,
            ),
        )
        os.rename(self.source, staged)
        if observed:
            operation = self.journal.transition_phase(
                operation.operation_id,
                PhysicalPhase.SOURCE_STAGE_INTENT,
                PhysicalPhase.SOURCE_STAGED,
                evidence=TransitionEvidence(
                    actual_destination=self.destination,
                    staging_path=staged,
                    staging_identity=_identity(staged),
                ),
            )
        return operation, staged

    def _delete_intent(self, operation):
        operation, staged = self._source_staged(operation)
        destination_state = self.destination.stat(follow_symlinks=False)
        operation = self.journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.SOURCE_STAGED,
            PhysicalPhase.SOURCE_DELETE_INTENT,
            evidence=TransitionEvidence(
                actual_destination=self.destination,
                destination_identity=_identity(self.destination),
                destination_hash=self.expected_hash,
                destination_size=destination_state.st_size,
                staging_path=staged,
                staging_identity=_identity(staged),
            ),
        )
        return operation, staged

    def _assessment(self, operation):
        return assess_operation(
            self.journal,
            self.journal.get_operation(operation.operation_id),
        )

    def _recover(self):
        return reconcile_incomplete_operations(
            self.journal,
            hash_db_file=str(self.hash_db),
        )


class RecoveryAssessmentTests(RecoveryTestCase):
    def test_same_volume_intent_before_rename_is_safe_to_abort_read_only(self):
        operation = self._rename_intent(self._operation())
        before = self.journal.get_operation(operation.operation_id)

        assessment = self._assessment(operation)

        self.assertIs(assessment.decision, RecoveryDecision.SAFE_ABORT)
        self.assertEqual(self.source.read_bytes(), CONTENT)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.journal.get_operation(operation.operation_id), before)

    def test_same_volume_rename_ahead_of_journal_is_safe_to_finalize(self):
        operation = self._rename_intent(self._operation())
        os.rename(self.source, self.destination)

        assessment = self._assessment(operation)

        self.assertIs(
            assessment.decision,
            RecoveryDecision.SAFE_ADVANCE_PHYSICAL,
        )

    def test_owned_cross_volume_temp_is_safe_to_clean(self):
        operation, temporary = self._temp_created(self._operation())

        assessment = self._assessment(operation)

        self.assertIs(
            assessment.decision,
            RecoveryDecision.SAFE_CLEANUP_TEMP,
        )
        self.assertTrue(temporary.exists())
        self.assertEqual(temporary.read_bytes(), CONTENT[:7])

    def test_published_destination_and_source_are_safe_to_resume(self):
        operation, _ = self._publish_intent(self._operation())

        assessment = self._assessment(operation)

        self.assertIs(
            assessment.decision,
            RecoveryDecision.SAFE_RESUME_SOURCE_REMOVAL,
        )

    def test_replaced_published_destination_requires_review(self):
        operation = self._destination_verified(self._operation())
        self.destination.unlink()
        self.destination.write_bytes(b"external replacement")

        assessment = self._assessment(operation)

        self.assertIs(assessment.decision, RecoveryDecision.NEEDS_REVIEW)
        self.assertIn("destination", assessment.reason)

    def test_unchanged_staged_source_is_safe_to_finish(self):
        operation, staged = self._source_staged(self._operation())

        assessment = self._assessment(operation)

        self.assertIs(
            assessment.decision,
            RecoveryDecision.SAFE_FINISH_STAGED_SOURCE,
        )
        self.assertEqual(staged.read_bytes(), CONTENT)

    def test_replaced_staged_source_requires_review(self):
        operation, staged = self._source_staged(self._operation())
        staged.unlink()
        staged.write_bytes(b"replacement staged bytes")

        assessment = self._assessment(operation)

        self.assertIs(assessment.decision, RecoveryDecision.NEEDS_REVIEW)
        self.assertIn("staging", assessment.reason)

    def test_source_removed_while_delete_intent_remains_is_safe_to_finalize(self):
        operation, staged = self._delete_intent(self._operation())
        staged.unlink()

        assessment = self._assessment(operation)

        self.assertIs(
            assessment.decision,
            RecoveryDecision.SAFE_ADVANCE_PHYSICAL,
        )

    def test_source_path_reuse_after_same_volume_rename_requires_review(self):
        operation = self._rename_intent(self._operation())
        os.rename(self.source, self.destination)
        self.source.write_bytes(b"new source object")

        assessment = self._assessment(operation)

        self.assertIs(assessment.decision, RecoveryDecision.NEEDS_REVIEW)
        self.assertIn("source path", assessment.reason)

    def test_neither_source_nor_destination_requires_review(self):
        operation = self._rename_intent(self._operation())
        self.source.unlink()

        assessment = self._assessment(operation)

        self.assertIs(assessment.decision, RecoveryDecision.NEEDS_REVIEW)

    def test_replaced_temporary_object_requires_review(self):
        operation, temporary = self._temp_created(self._operation())
        temporary.unlink()
        temporary.write_bytes(b"unrelated replacement")

        assessment = self._assessment(operation)

        self.assertIs(assessment.decision, RecoveryDecision.NEEDS_REVIEW)
        self.assertIn("temporary", assessment.reason)


class RecoveryReconciliationTests(RecoveryTestCase):
    def test_same_volume_no_action_aborts_and_preserves_source(self):
        operation = self._rename_intent(self._operation())

        self._recover()

        recovered = self.journal.get_operation(operation.operation_id)
        self.assertEqual(recovered.operation_status, OperationStatus.ABORTED)
        self.assertEqual(self.source.read_bytes(), CONTENT)
        self.assertFalse(self.destination.exists())

    def test_same_volume_completed_rename_is_finalized(self):
        operation = self._rename_intent(
            self._operation(effects=True, effects_applied=True)
        )
        os.rename(self.source, self.destination)

        self._recover()

        recovered = self.journal.get_operation(operation.operation_id)
        self.assertEqual(recovered.physical_phase, PhysicalPhase.PHYSICAL_COMMITTED)
        self.assertEqual(recovered.operation_status, OperationStatus.COMPLETE)
        self.assertEqual(self.destination.read_bytes(), CONTENT)

    def test_owned_temp_is_removed_without_touching_source(self):
        operation, temporary = self._temp_created(self._operation())

        self._recover()

        recovered = self.journal.get_operation(operation.operation_id)
        self.assertEqual(recovered.operation_status, OperationStatus.ABORTED)
        self.assertFalse(temporary.exists())
        self.assertEqual(self.source.read_bytes(), CONTENT)

    def test_published_destination_resumes_without_second_publication(self):
        operation, _ = self._publish_intent(
            self._operation(effects=True, effects_applied=True)
        )

        self._recover()

        recovered = self.journal.get_operation(operation.operation_id)
        self.assertEqual(recovered.operation_status, OperationStatus.COMPLETE)
        self.assertFalse(self.source.exists())
        self.assertEqual(self.destination.read_bytes(), CONTENT)
        self.assertEqual(list(self.documents.glob("report(*).txt")), [])

    def test_same_content_source_replacement_before_removal_is_preserved(self):
        operation = self._destination_verified(
            self._operation(effects=True, effects_applied=True)
        )
        original_assess = assess_operation
        replaced = False

        def assess_then_replace(journal, current_operation):
            nonlocal replaced
            assessment = original_assess(journal, current_operation)
            if (
                not replaced
                and assessment.decision
                is RecoveryDecision.SAFE_RESUME_SOURCE_REMOVAL
                and current_operation.physical_phase
                is PhysicalPhase.DESTINATION_VERIFIED
            ):
                replaced = True
                self.source.unlink()
                self.source.write_bytes(CONTENT)
            return assessment

        with patch("app.recovery.assess_operation", side_effect=assess_then_replace):
            self._recover()

        recovered = self.journal.get_operation(operation.operation_id)
        self.assertEqual(recovered.operation_status, OperationStatus.NEEDS_REVIEW)
        self.assertEqual(self.source.read_bytes(), CONTENT)
        self.assertEqual(self.destination.read_bytes(), CONTENT)

    def test_unchanged_staged_source_is_safely_removed(self):
        operation, staged = self._source_staged(
            self._operation(effects=True, effects_applied=True)
        )

        self._recover()

        recovered = self.journal.get_operation(operation.operation_id)
        self.assertEqual(recovered.operation_status, OperationStatus.COMPLETE)
        self.assertFalse(staged.exists())
        self.assertFalse(self.source.exists())
        self.assertEqual(self.destination.read_bytes(), CONTENT)

    def test_removed_staged_source_advances_without_repeating_delete(self):
        operation, staged = self._delete_intent(
            self._operation(effects=True, effects_applied=True)
        )
        staged.unlink()

        self._recover()

        recovered = self.journal.get_operation(operation.operation_id)
        self.assertEqual(recovered.operation_status, OperationStatus.COMPLETE)
        self.assertEqual(recovered.physical_phase, PhysicalPhase.PHYSICAL_COMMITTED)

    def test_recovery_run_twice_is_idempotent(self):
        operation = self._rename_intent(self._operation())

        first = self._recover()
        second = self._recover()

        self.assertIn(operation.operation_id, first.reconciled_operation_ids)
        self.assertEqual(second.reconciled_operation_ids, ())
        self.assertEqual(self.source.read_bytes(), CONTENT)

    def test_crash_after_recovery_staging_is_safe_on_next_run(self):
        operation, staged = self._source_staged(
            self._operation(effects=True, effects_applied=True),
            observed=False,
        )
        original_transition = self.journal.transition_phase
        failed = False

        def fail_observation(operation_id, expected_phase, new_phase, **kwargs):
            nonlocal failed
            if new_phase is PhysicalPhase.SOURCE_STAGED and not failed:
                failed = True
                raise JournalDatabaseError("simulated recovery crash")
            return original_transition(
                operation_id,
                expected_phase,
                new_phase,
                **kwargs,
            )

        with patch.object(
            self.journal,
            "transition_phase",
            side_effect=fail_observation,
        ):
            with self.assertRaises(JournalDatabaseError):
                self._recover()

        self.assertFalse(self.source.exists())
        self.assertEqual(staged.read_bytes(), CONTENT)

        self._recover()

        recovered = self.journal.get_operation(operation.operation_id)
        self.assertEqual(recovered.operation_status, OperationStatus.COMPLETE)
        self.assertFalse(staged.exists())

    def test_metadata_recovery_only_retries_idempotent_hash_index(self):
        operation = self._rename_intent(self._operation(effects=True))
        os.rename(self.source, self.destination)
        destination_state = self.destination.stat(follow_symlinks=False)
        operation = self.journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.RENAME_INTENT,
            PhysicalPhase.PHYSICAL_COMMITTED,
            evidence=TransitionEvidence(
                actual_destination=self.destination,
                destination_identity=_identity(self.destination),
                destination_hash=self.expected_hash,
                destination_size=destination_state.st_size,
            ),
        )

        report = self._recover()

        recovered = self.journal.get_operation(operation.operation_id)
        effects = {
            effect.effect_type: effect.state
            for effect in self.journal.get_effects(operation.operation_id)
        }
        self.assertEqual(recovered.operation_status, OperationStatus.NEEDS_REVIEW)
        self.assertEqual(effects[EffectType.HASH_INDEX], EffectState.APPLIED)
        self.assertEqual(
            effects[EffectType.LEGACY_CSV_HISTORY],
            EffectState.NOT_STARTED,
        )
        self.assertEqual(
            effects[EffectType.LEGACY_STATISTICS],
            EffectState.NOT_STARTED,
        )
        self.assertEqual(
            hash_manager.load_hash_db(str(self.hash_db)),
            {self.expected_hash: str(self.destination)},
        )
        self.assertEqual(report.needs_review_operation_ids, (operation.operation_id,))

    def test_ambiguous_evidence_is_persisted_without_file_mutation(self):
        operation = self._destination_verified(self._operation())
        self.destination.unlink()
        self.destination.write_bytes(b"replacement destination")
        source_before = self.source.read_bytes()
        destination_before = self.destination.read_bytes()

        self._recover()

        recovered = self.journal.get_operation(operation.operation_id)
        self.assertEqual(recovered.operation_status, OperationStatus.NEEDS_REVIEW)
        self.assertEqual(recovered.physical_phase, PhysicalPhase.DESTINATION_VERIFIED)
        self.assertEqual(self.source.read_bytes(), source_before)
        self.assertEqual(self.destination.read_bytes(), destination_before)


class StartupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.config = {
            "organized_base_folder": str(self.root / "organized"),
            "watch_folders": [
                {
                    "path": str(self.root / "incoming"),
                    "active": True,
                    "label": "Incoming",
                }
            ],
            "rules": {"documents": [".txt"]},
            "log_file": str(self.root / "logs" / "filepilot.log"),
            "stats_file": str(self.root / "reports" / "stats.json"),
            "history_file": str(self.root / "reports" / "history.csv"),
            "hash_db_file": str(self.root / "reports" / "hashes.json"),
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def _build_patches(self, order, journal, monitor):
        empty_report = RecoveryReport((), (), ())

        def recover(*_args, **_kwargs):
            order.append("recovery")
            return empty_report

        def construct_monitor(*_args, **_kwargs):
            order.append("monitor")
            return monitor

        plugin_manager = unittest.mock.Mock()
        plugin_manager.get_plugins_info.return_value = []
        plugin_manager.get_failed_plugins.return_value = []
        return (
            patch("app.main.load_config", return_value=dict(self.config)),
            patch("app.main.OperationJournal", return_value=journal),
            patch(
                "app.main.reconcile_incomplete_operations",
                side_effect=recover,
            ),
            patch("app.main.MultiFolderMonitor", side_effect=construct_monitor),
            patch("app.main.PluginManager", return_value=plugin_manager),
            patch("app.main.setup_logging"),
        )

    def test_startup_recovery_precedes_monitor_construction(self):
        from app.main import build_monitor

        order = []
        journal = unittest.mock.Mock(spec=OperationJournal)
        monitor = unittest.mock.Mock()
        patches = self._build_patches(order, journal, monitor)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            _, built_monitor = build_monitor()

        self.assertEqual(order, ["recovery", "monitor"])
        self.assertIs(built_monitor, monitor)
        self.assertEqual(built_monitor.recovery_report, RecoveryReport((), (), ()))

    def test_recovery_initialization_failure_prevents_monitor_construction(self):
        from app.main import build_monitor

        monitor_constructor = unittest.mock.Mock()
        with (
            patch("app.main.load_config", return_value=dict(self.config)),
            patch(
                "app.main.OperationJournal",
                side_effect=JournalDatabaseError("journal unavailable"),
            ),
            patch("app.main.MultiFolderMonitor", monitor_constructor),
        ):
            with self.assertRaises(JournalDatabaseError):
                build_monitor()

        monitor_constructor.assert_not_called()

    def test_reconciliation_failure_prevents_monitor_construction(self):
        from app.main import build_monitor

        journal = unittest.mock.Mock(spec=OperationJournal)
        monitor_constructor = unittest.mock.Mock()
        with (
            patch("app.main.load_config", return_value=dict(self.config)),
            patch("app.main.OperationJournal", return_value=journal),
            patch(
                "app.main.reconcile_incomplete_operations",
                side_effect=JournalDatabaseError("recovery unavailable"),
            ),
            patch("app.main.MultiFolderMonitor", monitor_constructor),
            patch("app.main.setup_logging"),
        ):
            with self.assertRaises(JournalDatabaseError):
                build_monitor()

        monitor_constructor.assert_not_called()

    def test_unresolved_live_source_prevents_monitor_construction(self):
        from app.main import build_monitor

        source = self.root / "incoming" / "report.txt"
        source.parent.mkdir(parents=True)
        source.write_bytes(CONTENT)
        operation = unittest.mock.Mock()
        operation.operation_id = "review-operation"
        evidence = unittest.mock.Mock()
        evidence.exists = True
        assessment = unittest.mock.Mock(spec=RecoveryAssessment)
        assessment.operation = operation
        assessment.reason = "ambiguous destination"
        assessment.source = evidence
        report = RecoveryReport((assessment,), (), ("review-operation",))
        monitor_constructor = unittest.mock.Mock()
        plugin_manager = unittest.mock.Mock()

        with (
            patch("app.main.load_config", return_value=dict(self.config)),
            patch("app.main.OperationJournal", return_value=unittest.mock.Mock()),
            patch(
                "app.main.reconcile_incomplete_operations",
                return_value=report,
            ),
            patch("app.main.MultiFolderMonitor", monitor_constructor),
            patch("app.main.PluginManager", return_value=plugin_manager),
            patch("app.main.setup_logging"),
        ):
            with self.assertRaisesRegex(RuntimeError, "requires review"):
                build_monitor()

        monitor_constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
