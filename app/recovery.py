import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app import mover
from app.hash_manager import register_file_hash
from app.operation_journal import (
    EffectState,
    EffectType,
    JournalError,
    MoveMode,
    OperationEffect,
    OperationJournal,
    OperationRecord,
    OperationStatus,
    PhysicalPhase,
    SourceObjectType,
    TransitionEvidence,
)


class RecoveryDecision(str, Enum):
    SAFE_ABORT = "safe_abort"
    SAFE_CLEANUP_TEMP = "safe_cleanup_temp"
    SAFE_ADVANCE_PHYSICAL = "safe_advance_physical"
    SAFE_RESUME_SOURCE_REMOVAL = "safe_resume_source_removal"
    SAFE_RESUME_STAGING = "safe_resume_staging"
    SAFE_FINISH_STAGED_SOURCE = "safe_finish_staged_source"
    RECONCILE_METADATA = "reconcile_metadata"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class ObjectEvidence:
    path: Path | None
    exists: bool
    is_regular_file: bool
    identity: str | None
    size: int | None
    modified_ns: int | None
    changed_ns: int | None
    content_hash: str | None
    error: str | None = None


@dataclass(frozen=True)
class RecoveryAssessment:
    operation: OperationRecord
    decision: RecoveryDecision
    reason_code: str
    reason: str
    source: ObjectEvidence
    destination: ObjectEvidence
    temporary: ObjectEvidence
    staged_source: ObjectEvidence
    effects: tuple[OperationEffect, ...]


@dataclass(frozen=True)
class RecoveryReport:
    assessments: tuple[RecoveryAssessment, ...]
    reconciled_operation_ids: tuple[str, ...]
    needs_review_operation_ids: tuple[str, ...]


class RecoveryError(RuntimeError):
    pass


class RecoveryBlockedError(RecoveryError):
    """Recovery completed conservatively but unsafe operations still need review."""

    def __init__(
        self,
        message: str,
        report: RecoveryReport,
        blocking_operation_ids: tuple[str, ...],
    ) -> None:
        super().__init__(message)
        self.report = report
        self.blocking_operation_ids = blocking_operation_ids


def _identity(stat_result: os.stat_result) -> str:
    return f"stat-v1:{stat_result.st_dev}:{stat_result.st_ino}"


def _journaled_source_state(operation: OperationRecord) -> mover._FileState:
    if (
        operation.source_identity is None
        or operation.source_size is None
        or operation.source_mtime_ns is None
    ):
        raise RecoveryError("Journaled source identity is incomplete")
    parts = operation.source_identity.split(":")
    if len(parts) != 3 or parts[0] != "stat-v1":
        raise RecoveryError("Journaled source identity is invalid")
    try:
        device = int(parts[1])
        inode = int(parts[2])
    except ValueError as error:
        raise RecoveryError("Journaled source identity is invalid") from error
    return mover._FileState(
        device=device,
        inode=inode,
        size=operation.source_size,
        modified_ns=operation.source_mtime_ns,
        changed_ns=0,
    )


def _inspect(path_text: str | None, expected_hash: str | None) -> ObjectEvidence:
    if path_text is None:
        return ObjectEvidence(None, False, False, None, None, None, None, None)
    path = Path(path_text)
    try:
        stat_result = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return ObjectEvidence(path, False, False, None, None, None, None, None)
    except OSError as error:
        return ObjectEvidence(
            path,
            True,
            False,
            None,
            None,
            None,
            None,
            None,
            str(error),
        )

    is_regular = path.is_file() and not path.is_symlink()
    content_hash = None
    error_text = None
    if is_regular and expected_hash is not None:
        try:
            content_hash = mover.calculate_file_hash(path)
        except OSError as error:
            error_text = str(error)
    return ObjectEvidence(
        path=path,
        exists=True,
        is_regular_file=is_regular,
        identity=_identity(stat_result),
        size=stat_result.st_size,
        modified_ns=stat_result.st_mtime_ns,
        changed_ns=stat_result.st_ctime_ns,
        content_hash=content_hash,
        error=error_text,
    )


def _within(root: Path, path: Path | None) -> bool:
    if path is None:
        return True
    try:
        return path.resolve(strict=False).is_relative_to(root.resolve(strict=False))
    except OSError:
        return False


def _matches(
    evidence: ObjectEvidence,
    *,
    identity: str | None,
    content_hash: str | None,
    size: int | None,
    modified_ns: int | None = None,
) -> bool:
    return (
        evidence.exists
        and evidence.is_regular_file
        and evidence.error is None
        and identity is not None
        and evidence.identity == identity
        and content_hash is not None
        and evidence.content_hash == content_hash
        and size is not None
        and evidence.size == size
        and (modified_ns is None or evidence.modified_ns == modified_ns)
    )


def _identity_matches(evidence: ObjectEvidence, identity: str | None) -> bool:
    return (
        evidence.exists
        and evidence.error is None
        and identity is not None
        and evidence.identity == identity
    )


def _assessment(
    operation: OperationRecord,
    decision: RecoveryDecision,
    reason_code: str,
    reason: str,
    source: ObjectEvidence,
    destination: ObjectEvidence,
    temporary: ObjectEvidence,
    staged_source: ObjectEvidence,
    effects: tuple[OperationEffect, ...],
) -> RecoveryAssessment:
    return RecoveryAssessment(
        operation,
        decision,
        reason_code,
        reason,
        source,
        destination,
        temporary,
        staged_source,
        effects,
    )


def assess_operation(
    journal: OperationJournal,
    operation: OperationRecord,
) -> RecoveryAssessment:
    """Inspect one incomplete operation without mutating files or journal state."""
    effects = tuple(journal.get_effects(operation.operation_id))
    source = _inspect(operation.source_path, operation.source_hash)
    destination_path = operation.actual_destination or operation.intended_destination
    destination = _inspect(destination_path, operation.source_hash)
    temporary = _inspect(operation.temp_path, operation.source_hash)
    staged_source = _inspect(operation.staging_path, operation.source_hash)

    def result(decision, code, reason):
        return _assessment(
            operation,
            decision,
            code,
            reason,
            source,
            destination,
            temporary,
            staged_source,
            effects,
        )

    if operation.operation_status is OperationStatus.NEEDS_REVIEW:
        return result(
            RecoveryDecision.NEEDS_REVIEW,
            operation.error_code or "RECOVERY_PREVIOUS_REVIEW",
            operation.error_message or "The operation already requires review",
        )
    if operation.operation_status is not OperationStatus.OPEN:
        return result(
            RecoveryDecision.NEEDS_REVIEW,
            "RECOVERY_STATUS_CONTRADICTION",
            "An incomplete operation has an unexpected terminal status",
        )

    organized_root = Path(operation.organized_root)
    if not _within(organized_root, destination.path) or not _within(
        organized_root, temporary.path
    ):
        return result(
            RecoveryDecision.NEEDS_REVIEW,
            "RECOVERY_PATH_OUTSIDE_ROOT",
            "Recorded destination or temporary path is outside the organized root",
        )
    if operation.source_object_type is not SourceObjectType.FILE:
        return result(
            RecoveryDecision.NEEDS_REVIEW,
            "RECOVERY_UNSUPPORTED_SOURCE_TYPE",
            "Automatic recovery requires a regular-file source",
        )

    phase = operation.physical_phase
    source_matches = _matches(
        source,
        identity=operation.source_identity,
        content_hash=operation.source_hash,
        size=operation.source_size,
        modified_ns=operation.source_mtime_ns,
    )
    destination_identity = operation.destination_identity
    if destination_identity is None:
        if operation.physical_phase is PhysicalPhase.RENAME_INTENT:
            destination_identity = operation.source_identity
        elif operation.physical_phase is PhysicalPhase.PUBLISH_INTENT:
            destination_identity = operation.temp_identity
    destination_matches = _matches(
        destination,
        identity=destination_identity,
        content_hash=operation.destination_hash or operation.source_hash,
        size=(
            operation.destination_size
            if operation.destination_size is not None
            else operation.source_size
        ),
    )
    temporary_owned = _identity_matches(temporary, operation.temp_identity)
    temporary_verified = _matches(
        temporary,
        identity=operation.temp_identity,
        content_hash=operation.source_hash,
        size=operation.source_size,
    )
    staging_identity = operation.staging_identity
    if (
        staging_identity is None
        and phase is PhysicalPhase.SOURCE_STAGE_INTENT
    ):
        staging_identity = operation.source_identity
    staged_matches = _matches(
        staged_source,
        identity=staging_identity,
        content_hash=operation.source_hash,
        size=operation.source_size,
        modified_ns=operation.source_mtime_ns,
    )
    if phase is PhysicalPhase.PREPARED:
        if source_matches:
            return result(
                RecoveryDecision.SAFE_ABORT,
                "RECOVERY_PREPARED_NO_ACTION",
                "No physical intent was recorded and the original source is unchanged",
            )
    elif phase is PhysicalPhase.RENAME_INTENT:
        if source_matches and not destination.exists:
            return result(
                RecoveryDecision.SAFE_ABORT,
                "RECOVERY_RENAME_NOT_APPLIED",
                "The source is unchanged and the intended destination is absent",
            )
        if not source.exists and destination_matches:
            return result(
                RecoveryDecision.SAFE_ADVANCE_PHYSICAL,
                "RECOVERY_RENAME_APPLIED",
                "The destination is the recorded source object and the source is absent",
            )
    elif phase is PhysicalPhase.TEMP_CREATE_INTENT:
        if source_matches and not temporary.exists and not destination.exists:
            return result(
                RecoveryDecision.SAFE_ABORT,
                "RECOVERY_TEMP_NOT_CREATED",
                "The source is unchanged and no temporary or destination object exists",
            )
    elif phase in {PhysicalPhase.TEMP_CREATED, PhysicalPhase.TEMP_VERIFIED}:
        verified_enough = temporary_owned and (
            phase is PhysicalPhase.TEMP_CREATED or temporary_verified
        )
        if source_matches and not destination.exists and verified_enough:
            return result(
                RecoveryDecision.SAFE_CLEANUP_TEMP,
                "RECOVERY_OWNED_TEMP",
                "The source is unchanged and the private temporary object is owned",
            )
        if source_matches and not destination.exists and not temporary.exists:
            return result(
                RecoveryDecision.SAFE_ABORT,
                "RECOVERY_TEMP_ALREADY_CLEAN",
                "The source is unchanged and no temporary or destination object remains",
            )
    elif phase is PhysicalPhase.PUBLISH_INTENT:
        if source_matches and not destination.exists and temporary_owned:
            return result(
                RecoveryDecision.SAFE_CLEANUP_TEMP,
                "RECOVERY_PUBLISH_NOT_APPLIED",
                "Publication did not occur and the remaining temporary object is owned",
            )
        if source_matches and destination_matches and (
            not temporary.exists or temporary_owned
        ):
            return result(
                RecoveryDecision.SAFE_RESUME_SOURCE_REMOVAL,
                "RECOVERY_PUBLISH_APPLIED",
                "The published destination and unchanged source match the operation",
            )
    elif phase in {
        PhysicalPhase.DESTINATION_PUBLISHED,
        PhysicalPhase.DESTINATION_VERIFIED,
    }:
        if source_matches and destination_matches:
            return result(
                RecoveryDecision.SAFE_RESUME_SOURCE_REMOVAL,
                "RECOVERY_DESTINATION_VERIFIED",
                "The published destination and original source both match the operation",
            )
    elif phase is PhysicalPhase.SOURCE_STAGE_INTENT:
        if destination_matches and source_matches and not staged_source.exists:
            return result(
                RecoveryDecision.SAFE_RESUME_STAGING,
                "RECOVERY_STAGE_NOT_APPLIED",
                "The staging intent exists but the unchanged source was not staged",
            )
        if destination_matches and not source.exists and staged_matches:
            return result(
                RecoveryDecision.SAFE_FINISH_STAGED_SOURCE,
                "RECOVERY_STAGE_APPLIED",
                "The recorded staged source exists unchanged and the source path is absent",
            )
    elif phase is PhysicalPhase.SOURCE_STAGED:
        if destination_matches and not source.exists and staged_matches:
            return result(
                RecoveryDecision.SAFE_FINISH_STAGED_SOURCE,
                "RECOVERY_STAGED_SOURCE_VERIFIED",
                "The staged source and destination both match the operation",
            )
    elif phase is PhysicalPhase.SOURCE_DELETE_INTENT:
        if destination_matches and not source.exists and staged_matches:
            return result(
                RecoveryDecision.SAFE_FINISH_STAGED_SOURCE,
                "RECOVERY_DELETE_NOT_APPLIED",
                "The delete intent exists and the unchanged staged source remains",
            )
        if destination_matches and not source.exists and not staged_source.exists:
            return result(
                RecoveryDecision.SAFE_ADVANCE_PHYSICAL,
                "RECOVERY_DELETE_APPLIED",
                "The staged source is absent and the verified destination remains",
            )
    elif phase is PhysicalPhase.PHYSICAL_COMMITTED:
        if destination_matches:
            return result(
                RecoveryDecision.RECONCILE_METADATA,
                "RECOVERY_PHYSICAL_COMMITTED",
                "The physical destination is committed; only metadata remains",
            )
    elif phase is PhysicalPhase.NEEDS_REVIEW:
        return result(
            RecoveryDecision.NEEDS_REVIEW,
            operation.error_code or "RECOVERY_PREVIOUS_REVIEW",
            operation.error_message or "The physical operation requires review",
        )

    details = []
    if source.exists and not source_matches:
        details.append("source path does not contain the recorded source object")
    if destination.exists and not destination_matches:
        details.append("destination does not match recorded evidence")
    if temporary.exists and not temporary_owned:
        details.append("temporary path does not contain the recorded temporary object")
    if staged_source.exists and not staged_matches:
        details.append("staging path does not contain the recorded staged object")
    if not details:
        details.append("filesystem evidence is incomplete or contradictory")
    return result(
        RecoveryDecision.NEEDS_REVIEW,
        "RECOVERY_AMBIGUOUS_EVIDENCE",
        "; ".join(details),
    )


def assess_incomplete_operations(
    journal: OperationJournal,
) -> tuple[RecoveryAssessment, ...]:
    return tuple(
        assess_operation(journal, operation)
        for operation in journal.list_incomplete_operations()
    )


def _transition_evidence(assessment: RecoveryAssessment) -> TransitionEvidence:
    destination = assessment.destination
    staged = assessment.staged_source
    temporary = assessment.temporary
    return TransitionEvidence(
        actual_destination=destination.path if destination.exists else None,
        destination_identity=destination.identity if destination.exists else None,
        destination_hash=(
            assessment.operation.source_hash if destination.exists else None
        ),
        destination_size=destination.size if destination.exists else None,
        temp_path=temporary.path if temporary.path is not None else None,
        temp_identity=(
            temporary.identity
            if temporary.exists
            else assessment.operation.temp_identity
        ),
        staging_path=staged.path if staged.path is not None else None,
        staging_identity=(
            staged.identity
            if staged.exists
            else assessment.operation.staging_identity
        ),
    )


def _mark_review(journal: OperationJournal, assessment: RecoveryAssessment) -> None:
    operation = assessment.operation
    if operation.operation_status is OperationStatus.NEEDS_REVIEW:
        return
    journal.mark_needs_review(
        operation.operation_id,
        expected_phase=operation.physical_phase,
        error_code=assessment.reason_code,
        error_message=assessment.reason,
    )


def _cleanup_empty_staging_directory(operation: OperationRecord) -> None:
    if operation.staging_path is None:
        return
    staging_parent = Path(operation.staging_path).parent
    source_parent = Path(operation.source_path).parent
    if (
        staging_parent.parent != source_parent
        or not staging_parent.name.startswith(".filepilot-remove-")
        or staging_parent.is_symlink()
    ):
        return
    try:
        os.rmdir(staging_parent)
    except FileNotFoundError:
        pass
    except OSError:
        logging.warning(
            "Could not remove recovered staging directory %s",
            staging_parent,
        )


def _reconcile_hash_effect(
    journal: OperationJournal,
    operation: OperationRecord,
    effect: OperationEffect,
    hash_db_file: str,
) -> None:
    if effect.state is EffectState.APPLIED:
        return
    if effect.state is not EffectState.PENDING:
        journal.transition_effect(
            operation.operation_id,
            EffectType.HASH_INDEX,
            effect.state,
            EffectState.PENDING,
        )
    register_file_hash(
        operation.destination_hash or operation.source_hash,
        operation.actual_destination,
        hash_db_file,
    )
    journal.transition_effect(
        operation.operation_id,
        EffectType.HASH_INDEX,
        EffectState.PENDING,
        EffectState.APPLIED,
    )


def _reconcile_metadata(
    journal: OperationJournal,
    assessment: RecoveryAssessment,
    hash_db_file: str,
) -> bool:
    operation = assessment.operation
    effects = {effect.effect_type: effect for effect in assessment.effects}
    hash_effect = effects.get(EffectType.HASH_INDEX)
    if hash_effect is not None:
        _reconcile_hash_effect(journal, operation, hash_effect, hash_db_file)

    refreshed = tuple(journal.get_effects(operation.operation_id))
    unsafe = [
        effect.effect_type.value
        for effect in refreshed
        if effect.required and effect.state is not EffectState.APPLIED
    ]
    if unsafe:
        current = journal.get_operation(operation.operation_id)
        journal.mark_needs_review(
            operation.operation_id,
            expected_phase=current.physical_phase,
            error_code="RECOVERY_METADATA_UNSAFE",
            error_message=(
                "Non-idempotent metadata effects require review: "
                + ", ".join(unsafe)
            ),
        )
        return False
    journal.complete_operation(operation.operation_id)
    return True


def _reconcile_once(
    journal: OperationJournal,
    assessment: RecoveryAssessment,
    hash_db_file: str,
) -> None:
    operation = assessment.operation
    decision = assessment.decision
    if decision is RecoveryDecision.NEEDS_REVIEW:
        _mark_review(journal, assessment)
        return
    if decision is RecoveryDecision.SAFE_ABORT:
        journal.transition_phase(
            operation.operation_id,
            operation.physical_phase,
            PhysicalPhase.ABORTED,
            error_code=assessment.reason_code,
            error_message=assessment.reason,
        )
        return
    if decision is RecoveryDecision.SAFE_CLEANUP_TEMP:
        source = _inspect(operation.source_path, operation.source_hash)
        if not _matches(
            source,
            identity=operation.source_identity,
            content_hash=operation.source_hash,
            size=operation.source_size,
            modified_ns=operation.source_mtime_ns,
        ):
            raise RecoveryError(
                "Original source changed before temporary recovery cleanup"
            )
        current = _inspect(operation.temp_path, operation.source_hash)
        if not _identity_matches(current, operation.temp_identity):
            raise RecoveryError("Temporary object changed before recovery cleanup")
        final_source = _inspect(operation.source_path, operation.source_hash)
        final_temporary = _inspect(operation.temp_path, operation.source_hash)
        if not _matches(
            final_source,
            identity=operation.source_identity,
            content_hash=operation.source_hash,
            size=operation.source_size,
            modified_ns=operation.source_mtime_ns,
        ) or not _identity_matches(final_temporary, operation.temp_identity):
            raise RecoveryError(
                "Recovery evidence changed during temporary cleanup validation"
            )
        os.unlink(current.path)
        journal.transition_phase(
            operation.operation_id,
            operation.physical_phase,
            PhysicalPhase.ABORTED,
            error_code="RECOVERY_TEMP_CLEANED",
            error_message="Owned temporary object removed; original source preserved",
        )
        return
    if decision is RecoveryDecision.SAFE_ADVANCE_PHYSICAL:
        if operation.physical_phase is PhysicalPhase.SOURCE_DELETE_INTENT:
            _cleanup_empty_staging_directory(operation)
        journal.transition_phase(
            operation.operation_id,
            operation.physical_phase,
            PhysicalPhase.PHYSICAL_COMMITTED,
            evidence=_transition_evidence(assessment),
        )
        return
    if decision is RecoveryDecision.SAFE_RESUME_SOURCE_REMOVAL:
        if operation.physical_phase is PhysicalPhase.PUBLISH_INTENT:
            if assessment.temporary.exists:
                current = _inspect(operation.temp_path, operation.source_hash)
                if not _identity_matches(current, operation.temp_identity):
                    raise RecoveryError("Temporary object changed before recovery cleanup")
                os.unlink(current.path)
            journal.transition_phase(
                operation.operation_id,
                PhysicalPhase.PUBLISH_INTENT,
                PhysicalPhase.DESTINATION_PUBLISHED,
                evidence=_transition_evidence(assessment),
            )
            return
        if operation.physical_phase is PhysicalPhase.DESTINATION_PUBLISHED:
            journal.transition_phase(
                operation.operation_id,
                PhysicalPhase.DESTINATION_PUBLISHED,
                PhysicalPhase.DESTINATION_VERIFIED,
                evidence=_transition_evidence(assessment),
            )
            return
        if operation.physical_phase is PhysicalPhase.DESTINATION_VERIFIED:
            source_state = _journaled_source_state(operation)
            journal_move = mover._JournaledMove(journal, operation.operation_id)
            journal_move.phase = PhysicalPhase.DESTINATION_VERIFIED
            mover._remove_verified_source(
                assessment.source.path,
                assessment.destination.path,
                operation.source_hash,
                source_state,
                journal_move,
                operation.destination_identity,
            )
            return
    if decision is RecoveryDecision.SAFE_RESUME_STAGING:
        staging_path = assessment.staged_source.path
        source_path = assessment.source.path
        if staging_path is None or source_path is None:
            raise RecoveryError("Staging paths are unavailable")
        staging_parent = staging_path.parent
        if (
            staging_parent.parent != source_path.parent
            or not staging_parent.is_dir()
            or staging_parent.is_symlink()
            or os.path.lexists(staging_path)
        ):
            raise RecoveryError("Recorded staging directory is not safe to resume")
        current_source = _inspect(str(source_path), operation.source_hash)
        if not _matches(
            current_source,
            identity=operation.source_identity,
            content_hash=operation.source_hash,
            size=operation.source_size,
            modified_ns=operation.source_mtime_ns,
        ):
            raise RecoveryError("Source changed before recovery staging")
        mover._move_no_clobber(source_path, staging_path)
        staged = _inspect(str(staging_path), operation.source_hash)
        if not _matches(
            staged,
            identity=operation.source_identity,
            content_hash=operation.source_hash,
            size=operation.source_size,
            modified_ns=operation.source_mtime_ns,
        ):
            try:
                mover._move_no_clobber(staging_path, source_path)
            except OSError as restore_error:
                raise RecoveryError(
                    "Source changed while recovery staged it; the changed object "
                    f"was preserved at {staging_path}: {restore_error}"
                ) from restore_error
            raise RecoveryError(
                "Source changed while recovery staged it and was restored"
            )
        journal.transition_phase(
            operation.operation_id,
            PhysicalPhase.SOURCE_STAGE_INTENT,
            PhysicalPhase.SOURCE_STAGED,
            evidence=TransitionEvidence(
                actual_destination=assessment.destination.path,
                staging_path=staging_path,
                staging_identity=staged.identity,
            ),
        )
        return
    if decision is RecoveryDecision.SAFE_FINISH_STAGED_SOURCE:
        if operation.physical_phase is PhysicalPhase.SOURCE_STAGE_INTENT:
            journal.transition_phase(
                operation.operation_id,
                PhysicalPhase.SOURCE_STAGE_INTENT,
                PhysicalPhase.SOURCE_STAGED,
                evidence=_transition_evidence(assessment),
            )
            return
        if operation.physical_phase is PhysicalPhase.SOURCE_STAGED:
            journal.transition_phase(
                operation.operation_id,
                PhysicalPhase.SOURCE_STAGED,
                PhysicalPhase.SOURCE_DELETE_INTENT,
                evidence=_transition_evidence(assessment),
            )
            return
        if operation.physical_phase is PhysicalPhase.SOURCE_DELETE_INTENT:
            current = _inspect(operation.staging_path, operation.source_hash)
            if not _matches(
                current,
                identity=operation.staging_identity,
                content_hash=operation.source_hash,
                size=operation.source_size,
                modified_ns=operation.source_mtime_ns,
            ):
                raise RecoveryError("Staged source changed before recovery deletion")
            destination = _inspect(operation.actual_destination, operation.source_hash)
            if not _matches(
                destination,
                identity=operation.destination_identity,
                content_hash=operation.destination_hash or operation.source_hash,
                size=operation.destination_size or operation.source_size,
            ):
                raise RecoveryError("Destination changed before recovery deletion")
            final_staged = _inspect(operation.staging_path, operation.source_hash)
            if not _matches(
                final_staged,
                identity=operation.staging_identity,
                content_hash=operation.source_hash,
                size=operation.source_size,
                modified_ns=operation.source_mtime_ns,
            ):
                raise RecoveryError("Staged source changed before final recovery deletion")
            os.unlink(current.path)
            _cleanup_empty_staging_directory(operation)
            return
    if decision is RecoveryDecision.RECONCILE_METADATA:
        _reconcile_metadata(journal, assessment, hash_db_file)
        return
    raise RecoveryError(f"Unsupported recovery decision: {decision.value}")


def reconcile_incomplete_operations(
    journal: OperationJournal,
    *,
    hash_db_file: str,
) -> RecoveryReport:
    """Conservatively reconcile all incomplete operations before watchers start."""
    initial = assess_incomplete_operations(journal)
    reconciled = []
    for initial_assessment in initial:
        operation_id = initial_assessment.operation.operation_id
        for _ in range(16):
            operation = journal.get_operation(operation_id)
            if operation.operation_status not in {
                OperationStatus.OPEN,
                OperationStatus.NEEDS_REVIEW,
            }:
                reconciled.append(operation_id)
                break
            assessment = assess_operation(journal, operation)
            if assessment.decision is RecoveryDecision.NEEDS_REVIEW:
                _mark_review(journal, assessment)
                break
            try:
                _reconcile_once(journal, assessment, hash_db_file)
            except JournalError:
                raise
            except (OSError, RecoveryError) as error:
                refreshed = assess_operation(
                    journal,
                    journal.get_operation(operation_id),
                )
                if refreshed.decision is RecoveryDecision.NEEDS_REVIEW:
                    _mark_review(journal, refreshed)
                else:
                    journal.mark_needs_review(
                        operation_id,
                        expected_phase=refreshed.operation.physical_phase,
                        error_code="RECOVERY_ACTION_FAILED",
                        error_message=str(error),
                    )
                break
        else:
            raise RecoveryError(
                f"Recovery did not converge for operation {operation_id}"
            )

    final = assess_incomplete_operations(journal)
    needs_review = tuple(
        assessment.operation.operation_id
        for assessment in final
        if assessment.decision is RecoveryDecision.NEEDS_REVIEW
    )
    return RecoveryReport(
        assessments=final,
        reconciled_operation_ids=tuple(reconciled),
        needs_review_operation_ids=needs_review,
    )


def reconcile_operation(
    journal: OperationJournal,
    operation_id: str,
    *,
    hash_db_file: str,
) -> RecoveryAssessment | None:
    """Apply only the core's current safe decision for one operation."""
    for _ in range(16):
        operation = journal.get_operation(operation_id)
        if operation.operation_status not in {
            OperationStatus.OPEN,
            OperationStatus.NEEDS_REVIEW,
        }:
            return None
        assessment = assess_operation(journal, operation)
        if assessment.decision is RecoveryDecision.NEEDS_REVIEW:
            _mark_review(journal, assessment)
            return assess_operation(journal, journal.get_operation(operation_id))
        try:
            _reconcile_once(journal, assessment, hash_db_file)
        except JournalError:
            raise
        except (OSError, RecoveryError) as error:
            refreshed = assess_operation(journal, journal.get_operation(operation_id))
            if refreshed.decision is RecoveryDecision.NEEDS_REVIEW:
                _mark_review(journal, refreshed)
            else:
                journal.mark_needs_review(
                    operation_id,
                    expected_phase=refreshed.operation.physical_phase,
                    error_code="RECOVERY_ACTION_FAILED",
                    error_message=str(error),
                )
            return assess_operation(journal, journal.get_operation(operation_id))
    raise RecoveryError(f"Recovery did not converge for operation {operation_id}")
