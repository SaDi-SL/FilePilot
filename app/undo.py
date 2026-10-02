import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app import mover
from app.hash_manager import register_file_hash
from app.operation_journal import (
    EffectType,
    JournalConflictError,
    JournalError,
    OperationJournal,
    OperationStatus,
    PhysicalPhase,
)


class UndoStatus(str, Enum):
    SUCCESS = "success"
    ALREADY_UNDONE = "already_undone"
    NOT_ELIGIBLE = "not_eligible"
    CONFLICT = "conflict"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


@dataclass(frozen=True)
class UndoResult:
    status: UndoStatus
    original_operation_id: str
    undo_operation_id: str | None = None
    source_path: Path | None = None
    destination_path: Path | None = None
    reason: str | None = None
    metadata_warning: str | None = None


@dataclass(frozen=True)
class UndoAvailability:
    operation_id: str
    eligible: bool
    reason: str
    current_path: Path | None = None
    restore_path: Path | None = None
    unavailable_status: UndoStatus | None = None
    conflict: bool = False
    manual_review_required: bool = False
    existing_undo_operation_id: str | None = None


@dataclass(frozen=True)
class _UndoPlan:
    availability: UndoAvailability
    restore_source: Path
    restore_target: Path
    expected_hash: str
    state: mover._FileState


def _existing_result(journal: OperationJournal, operation_id: str) -> UndoResult | None:
    existing = journal.get_inverse_operation(operation_id)
    if existing is None:
        return None
    paths = {
        "undo_operation_id": existing.operation_id,
        "source_path": Path(existing.source_path),
        "destination_path": Path(
            existing.actual_destination or existing.intended_destination
        ),
    }
    if existing.operation_status is OperationStatus.COMPLETE:
        return UndoResult(
            UndoStatus.ALREADY_UNDONE,
            operation_id,
            reason="The move has already been undone",
            **paths,
        )
    if existing.operation_status is OperationStatus.NEEDS_REVIEW:
        return UndoResult(
            UndoStatus.NEEDS_REVIEW,
            operation_id,
            reason=existing.error_message or "The existing undo requires review",
            **paths,
        )
    if existing.operation_status is OperationStatus.OPEN:
        return UndoResult(
            UndoStatus.NEEDS_REVIEW,
            operation_id,
            reason="An interrupted undo must be recovered before another request",
            **paths,
        )
    return UndoResult(
        UndoStatus.NOT_ELIGIBLE,
        operation_id,
        reason="A prior undo attempt is terminal and will not be repeated automatically",
        **paths,
    )


def _existing_availability(
    journal: OperationJournal,
    operation_id: str,
) -> UndoAvailability | None:
    existing = journal.get_inverse_operation(operation_id)
    if existing is None:
        return None
    current_path = Path(existing.source_path)
    restore_path = Path(existing.actual_destination or existing.intended_destination)
    if existing.operation_status is OperationStatus.COMPLETE:
        status = UndoStatus.ALREADY_UNDONE
        reason = "The move has already been undone"
        review = False
    elif existing.operation_status in {
        OperationStatus.NEEDS_REVIEW,
        OperationStatus.OPEN,
    }:
        status = UndoStatus.NEEDS_REVIEW
        reason = (
            existing.error_message
            if existing.operation_status is OperationStatus.NEEDS_REVIEW
            else "An interrupted undo must be recovered before another request"
        ) or "The existing undo requires review"
        review = True
    else:
        status = UndoStatus.NOT_ELIGIBLE
        reason = "A prior undo attempt is terminal and will not be repeated automatically"
        review = False
    return UndoAvailability(
        operation_id,
        False,
        reason,
        current_path,
        restore_path,
        status,
        manual_review_required=review,
        existing_undo_operation_id=existing.operation_id,
    )


def _prepare_undo(
    journal: OperationJournal,
    operation_id: str,
) -> _UndoPlan | UndoAvailability:
    try:
        existing = _existing_availability(journal, operation_id)
        if existing is not None:
            return existing
        original = journal.get_operation(operation_id)
    except JournalError as error:
        return UndoAvailability(
            operation_id,
            False,
            str(error),
            unavailable_status=UndoStatus.NOT_ELIGIBLE,
        )

    restore_source = (
        Path(original.actual_destination) if original.actual_destination else None
    )
    restore_target = Path(original.source_path)
    expected_hash = original.destination_hash or original.source_hash
    expected_size = (
        original.destination_size
        if original.destination_size is not None
        else original.source_size
    )
    if (
        original.inverse_of_operation_id is not None
        or original.operation_status is not OperationStatus.COMPLETE
        or original.physical_phase is not PhysicalPhase.PHYSICAL_COMMITTED
        or restore_source is None
        or original.destination_identity is None
        or expected_hash is None
        or expected_size is None
    ):
        return UndoAvailability(
            operation_id,
            False,
            "The operation is not a completed move with sufficient evidence",
            restore_source,
            restore_target,
            UndoStatus.NOT_ELIGIBLE,
        )
    if os.path.lexists(restore_target):
        return UndoAvailability(
            operation_id,
            False,
            "The original source path is occupied",
            restore_source,
            restore_target,
            UndoStatus.CONFLICT,
            conflict=True,
        )
    if not restore_target.parent.is_dir() or restore_target.parent.is_symlink():
        return UndoAvailability(
            operation_id,
            False,
            "The original source parent is missing or is not a safe directory",
            restore_source,
            restore_target,
            UndoStatus.NEEDS_REVIEW,
            manual_review_required=True,
        )
    try:
        before = restore_source.stat(follow_symlinks=False)
        state = mover._file_state(before)
        if (
            restore_source.is_symlink()
            or not restore_source.is_file()
            or mover._identity_text(state) != original.destination_identity
            or state.size != expected_size
            or mover.calculate_file_hash(restore_source) != expected_hash
            or mover._file_state(restore_source.stat(follow_symlinks=False)) != state
        ):
            raise ValueError("The current destination does not match the completed move")
    except FileNotFoundError:
        return UndoAvailability(
            operation_id,
            False,
            "The moved destination is missing",
            restore_source,
            restore_target,
            UndoStatus.NOT_ELIGIBLE,
        )
    except (OSError, ValueError) as error:
        return UndoAvailability(
            operation_id,
            False,
            str(error),
            restore_source,
            restore_target,
            UndoStatus.NEEDS_REVIEW,
            manual_review_required=True,
        )

    availability = UndoAvailability(
        operation_id,
        True,
        "The destination still matches the completed move and can be restored safely",
        restore_source,
        restore_target,
    )
    return _UndoPlan(
        availability,
        restore_source,
        restore_target,
        expected_hash,
        state,
    )


def evaluate_undo(
    journal: OperationJournal,
    operation_id: str,
) -> UndoAvailability:
    """Evaluate current undo evidence without mutating files or journal state."""
    prepared = _prepare_undo(journal, operation_id)
    return prepared.availability if isinstance(prepared, _UndoPlan) else prepared


def undo_operation(
    journal: OperationJournal,
    operation_id: str,
    *,
    hash_db_file: str,
) -> UndoResult:
    """Restore an exact completed move using strong evidence and durable intent."""
    prepared = _prepare_undo(journal, operation_id)
    if isinstance(prepared, UndoAvailability):
        return UndoResult(
            prepared.unavailable_status or UndoStatus.NOT_ELIGIBLE,
            operation_id,
            prepared.existing_undo_operation_id,
            prepared.current_path,
            prepared.restore_path,
            prepared.reason,
        )
    restore_source = prepared.restore_source
    restore_target = prepared.restore_target
    expected_hash = prepared.expected_hash
    state = prepared.state
    try:
        destination_root = restore_target.parent.resolve(strict=True)
        destination_root_state = mover._file_state(
            destination_root.stat(follow_symlinks=False)
        )
    except OSError as error:
        return UndoResult(
            UndoStatus.NEEDS_REVIEW,
            operation_id,
            source_path=restore_source,
            destination_path=restore_target,
            reason=f"The restore directory could not be verified safely: {error}",
        )
    try:
        undo = journal.create_inverse_operation(
            operation_id,
            source_path=restore_source.resolve(strict=True),
            organized_root=destination_root,
            intended_destination=restore_target,
            source_identity=mover._identity_text(state),
            source_hash=expected_hash,
            source_size=state.size,
            source_mtime_ns=state.modified_ns,
        )
    except JournalConflictError:
        try:
            existing = _existing_result(journal, operation_id)
        except JournalError as error:
            existing = UndoResult(
                UndoStatus.FAILED,
                operation_id,
                reason=f"The undo reservation could not be confirmed: {error}",
            )
        return existing or UndoResult(
                UndoStatus.FAILED,
                operation_id,
                reason="The undo reservation conflicted with another request",
            )
    except JournalError as error:
        return UndoResult(
            UndoStatus.FAILED,
            operation_id,
            source_path=restore_source,
            destination_path=restore_target,
            reason=f"Could not create durable undo intent: {error}",
        )

    journal_move = mover._JournaledMove(journal, undo.operation_id)
    try:
        journal.initialize_effect(undo.operation_id, EffectType.HASH_INDEX, required=True)
        restored = mover._move_to_exact_destination(
            restore_source,
            restore_target,
            destination_root,
            expected_hash,
            state,
            journal_move,
            destination_root_state,
        )
    except FileExistsError as error:
        journal_move.stop(error)
        return UndoResult(
            UndoStatus.CONFLICT,
            operation_id,
            undo.operation_id,
            restore_source,
            restore_target,
            str(error),
        )
    except (mover._SourceRemovalError, mover._JournalAfterFilesystemError) as error:
        journal_move.stop(
            error,
            getattr(error, "destination", None),
            force_review=True,
        )
        return UndoResult(
            UndoStatus.NEEDS_REVIEW,
            operation_id,
            undo.operation_id,
            restore_source,
            restore_target,
            str(error),
        )
    except Exception as error:
        journal_move.stop(error, force_review=True)
        return UndoResult(
            UndoStatus.NEEDS_REVIEW,
            operation_id,
            undo.operation_id,
            restore_source,
            restore_target,
            str(error),
        )

    metadata_warning = mover._run_effect(
        journal_move,
        EffectType.HASH_INDEX,
        lambda: register_file_hash(expected_hash, str(restored), hash_db_file),
    )
    if metadata_warning is None:
        try:
            journal.complete_operation(undo.operation_id)
        except JournalError as error:
            metadata_warning = f"Undo completion could not be confirmed: {error}"
    return UndoResult(
        UndoStatus.SUCCESS,
        operation_id,
        undo.operation_id,
        restore_source,
        restored,
        metadata_warning=metadata_warning,
    )
