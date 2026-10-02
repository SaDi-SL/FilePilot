from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.mover import DuplicateStatus, PreviewResult, PreviewStatus
from app.recovery import RecoveryAssessment, RecoveryDecision


class SafetyDataState(str, Enum):
    LOADING = "loading"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class RecoveryAction(str, Enum):
    APPLY_SAFE_RECOMMENDATION = "apply_safe_recommendation"


class RecoveryActionStatus(str, Enum):
    RECONCILED = "reconciled"
    STILL_NEEDS_REVIEW = "still_needs_review"
    NOT_ALLOWED = "not_allowed"
    FAILED = "failed"


@dataclass(frozen=True)
class OperationPreview:
    state: SafetyDataState
    status: PreviewStatus | None
    source: Path
    category: str | None = None
    proposed_destination: Path | None = None
    duplicate_status: DuplicateStatus = DuplicateStatus.UNKNOWN
    duplicate_of: Path | None = None
    destination_collision: bool = False
    alternative_name_required: bool = False
    safety_validated: bool = False
    execution_possible: bool = False
    classification_method: str | None = None
    classification_source: str | None = None
    message: str = ""
    warning: str | None = None
    non_mutating: bool = True

    @classmethod
    def unavailable(cls, source: str | Path, message: str) -> "OperationPreview":
        return cls(
            SafetyDataState.UNAVAILABLE,
            None,
            Path(source),
            message=message,
        )


@dataclass(frozen=True)
class RecoveryItem:
    operation_id: str
    filename: str
    source_path: Path
    destination_path: Path | None
    status: str
    reason_code: str
    reason: str
    evidence_summary: tuple[str, ...]
    available_actions: tuple[RecoveryAction, ...]
    manual_review_required: bool
    blocks_monitoring: bool
    recommendation: str = "Manual review required"


@dataclass(frozen=True)
class RecoverySnapshot:
    state: SafetyDataState
    items: tuple[RecoveryItem, ...] = ()
    total_items: int = 0
    has_more: bool = False
    error: str | None = None
    offset: int = 0

    @classmethod
    def unavailable(cls, message: str) -> "RecoverySnapshot":
        return cls(SafetyDataState.UNAVAILABLE, error=message)


@dataclass(frozen=True)
class RecoveryActionResult:
    operation_id: str
    status: RecoveryActionStatus
    message: str
    manual_review_required: bool = False


def operation_preview_from_domain(
    result: PreviewResult,
    *,
    classification_method: str,
) -> OperationPreview:
    messages = {
        PreviewStatus.READY: (
            "Current checks found a safe proposed organization. "
            "Execution would revalidate all evidence."
        ),
        PreviewStatus.DUPLICATE: (
            "FilePilot verified matching content. The source would be retained."
        ),
        PreviewStatus.SOURCE_MISSING: (
            "The selected source is missing or is not a regular file. Nothing changed."
        ),
        PreviewStatus.HASH_FAILED: (
            "FilePilot could not read the selected file safely. Nothing changed."
        ),
        PreviewStatus.UNSAFE: (
            "FilePilot could not establish a safe preview. Nothing changed."
        ),
    }
    return OperationPreview(
        state=SafetyDataState.AVAILABLE,
        status=result.status,
        source=result.source,
        category=result.category,
        proposed_destination=result.proposed_destination,
        duplicate_status=result.duplicate_status,
        duplicate_of=result.duplicate_of,
        destination_collision=result.destination_collision,
        alternative_name_required=result.alternative_name_required,
        safety_validated=result.safety_validated,
        execution_possible=result.execution_possible,
        classification_method=classification_method,
        classification_source=result.classification_source,
        message=messages[result.status],
        warning=result.warning,
    )


def recovery_item_from_assessment(
    assessment: RecoveryAssessment,
    *,
    action_allowed: bool,
    blocks_monitoring: bool,
) -> RecoveryItem:
    operation = assessment.operation
    destination = operation.actual_destination or operation.intended_destination
    evidence = []
    for label, item in (
        ("Original source", assessment.source),
        ("Destination", assessment.destination),
        ("Temporary file", assessment.temporary),
        ("Staged source", assessment.staged_source),
    ):
        if item.path is None:
            continue
        if item.error:
            state = "could not be inspected"
        elif item.exists:
            state = "exists"
        else:
            state = "is absent"
        evidence.append(f"{label} {state}")
    manual_review = assessment.decision is RecoveryDecision.NEEDS_REVIEW
    recommendations = {
        RecoveryDecision.SAFE_ABORT: "Abort interrupted move safely",
        RecoveryDecision.SAFE_CLEANUP_TEMP: "Remove verified temporary file",
        RecoveryDecision.SAFE_ADVANCE_PHYSICAL: "Complete interrupted move safely",
        RecoveryDecision.SAFE_RESUME_SOURCE_REMOVAL: (
            "Complete interrupted move safely"
        ),
        RecoveryDecision.SAFE_RESUME_STAGING: "Complete interrupted move safely",
        RecoveryDecision.SAFE_FINISH_STAGED_SOURCE: (
            "Complete interrupted move safely"
        ),
        RecoveryDecision.RECONCILE_METADATA: "Finish metadata recovery safely",
        RecoveryDecision.NEEDS_REVIEW: "Manual review required",
    }
    actions = (
        (RecoveryAction.APPLY_SAFE_RECOMMENDATION,)
        if action_allowed and not manual_review
        else ()
    )
    return RecoveryItem(
        operation_id=operation.operation_id,
        filename=Path(operation.source_path).name,
        source_path=Path(operation.source_path),
        destination_path=Path(destination) if destination else None,
        status=("Manual review required" if manual_review else "Safe recovery available"),
        reason_code=assessment.reason_code,
        reason=assessment.reason,
        evidence_summary=tuple(evidence),
        available_actions=actions,
        manual_review_required=manual_review,
        blocks_monitoring=blocks_monitoring,
        recommendation=recommendations[assessment.decision],
    )
