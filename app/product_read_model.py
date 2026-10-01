from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from app.operation_journal import (
    EffectState,
    JournalError,
    JournalNotFoundError,
    MAX_RECENT_OPERATIONS,
    OperationContext,
    OperationEffect,
    OperationJournal,
    OperationRecord,
    OperationStatus,
    PhysicalPhase,
)


class ProductDataState(str, Enum):
    LOADING = "loading"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class ActivityStatus(str, Enum):
    COMPLETED = "completed"
    DUPLICATE = "duplicate"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    IN_PROGRESS = "in_progress"
    WARNING = "warning"
    RESTORED = "restored"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProductMetrics:
    total_processed: int | None
    failed: int | None
    duplicates: int | None
    needs_review: int | None
    active_rules: int | None = None

    @classmethod
    def unavailable(cls) -> "ProductMetrics":
        return cls(None, None, None, None, None)


@dataclass(frozen=True)
class ActivityRecord:
    record_id: str
    operation_id: str | None
    occurred_at_utc: datetime | None
    source_path: Path
    category: str | None
    status: ActivityStatus
    actual_destination: Path | None = None
    intended_destination: Path | None = None
    duplicate_target: Path | None = None
    error: str | None = None
    metadata_warning: str | None = None
    recovery_state: str | None = None
    durable: bool = True
    classification_method: str | None = None
    classification_source: str | None = None

    @property
    def filename(self) -> str:
        return self.source_path.name or str(self.source_path)

    @property
    def display_destination(self) -> Path | None:
        return self.actual_destination or self.duplicate_target


@dataclass(frozen=True)
class ProductSnapshot:
    state: ProductDataState
    metrics: ProductMetrics
    activity: tuple[ActivityRecord, ...] = ()
    error: str | None = None
    has_more: bool = False

    @classmethod
    def loading(cls) -> "ProductSnapshot":
        return cls(
            ProductDataState.LOADING,
            ProductMetrics.unavailable(),
        )

    @classmethod
    def unavailable(cls, message: str) -> "ProductSnapshot":
        return cls(
            ProductDataState.UNAVAILABLE,
            ProductMetrics.unavailable(),
            error=message,
        )


class ProductReadModel:
    """Maps durable journal evidence into immutable product-facing records."""

    def __init__(self, journal_path: str | Path | None = None) -> None:
        self._journal_path = Path(journal_path) if journal_path is not None else None

    def read(self, limit: int = 20) -> ProductSnapshot:
        bounded_limit = max(1, min(_integer_limit(limit), MAX_RECENT_OPERATIONS))
        try:
            journal = OperationJournal.open_existing_read_only(self._journal_path)
            snapshot = journal.read_recent_operations(bounded_limit)
        except JournalNotFoundError:
            return ProductSnapshot.unavailable(
                "No durable activity journal is available yet."
            )
        except JournalError as error:
            return ProductSnapshot(
                ProductDataState.ERROR,
                ProductMetrics.unavailable(),
                error=str(error),
            )

        effects_by_operation: dict[str, list[OperationEffect]] = {}
        for effect in snapshot.effects:
            effects_by_operation.setdefault(effect.operation_id, []).append(effect)
        contexts_by_operation = {
            context.operation_id: context for context in snapshot.contexts
        }

        activity = tuple(
            self._activity_record(
                operation,
                tuple(effects_by_operation.get(operation.operation_id, ())),
                contexts_by_operation.get(operation.operation_id),
            )
            for operation in snapshot.operations
        )
        metrics = ProductMetrics(
            total_processed=snapshot.counts.completed,
            failed=snapshot.counts.failed,
            duplicates=snapshot.counts.duplicates,
            needs_review=snapshot.counts.needs_review,
            active_rules=None,
        )
        return ProductSnapshot(
            ProductDataState.AVAILABLE,
            metrics,
            activity,
            has_more=snapshot.counts.total_operations > len(activity),
        )

    @staticmethod
    def from_live_event(event, record_id: str) -> ActivityRecord:
        status = {
            "moved": ActivityStatus.COMPLETED,
            "duplicate": ActivityStatus.DUPLICATE,
            "failed": ActivityStatus.FAILED,
            "hash_check_failed": ActivityStatus.FAILED,
            "error": ActivityStatus.FAILED,
        }.get(str(event.status).lower(), ActivityStatus.UNKNOWN)
        if status is ActivityStatus.COMPLETED and event.metadata_warning:
            status = ActivityStatus.WARNING
        return ActivityRecord(
            record_id=record_id,
            operation_id=event.operation_id,
            occurred_at_utc=event.occurred_at_utc,
            source_path=Path(event.source),
            category=event.category or None,
            status=status,
            actual_destination=event.actual_destination,
            duplicate_target=event.duplicate_target,
            error=event.error,
            metadata_warning=event.metadata_warning,
            durable=False,
        )

    @classmethod
    def _activity_record(
        cls,
        operation: OperationRecord,
        effects: tuple[OperationEffect, ...],
        context: OperationContext | None,
    ) -> ActivityRecord:
        status = cls._status(operation)
        warning = cls._metadata_warning(operation, effects)
        if warning and status is ActivityStatus.COMPLETED:
            status = ActivityStatus.WARNING
        return ActivityRecord(
            record_id=operation.operation_id,
            operation_id=operation.operation_id,
            occurred_at_utc=_parse_timestamp(
                operation.completed_at_utc or operation.updated_at_utc
            ),
            source_path=Path(operation.source_path),
            category=context.category if context else None,
            status=status,
            actual_destination=(
                Path(operation.actual_destination)
                if operation.actual_destination
                else None
            ),
            intended_destination=Path(operation.intended_destination),
            duplicate_target=(
                Path(operation.duplicate_of_path)
                if operation.duplicate_of_path
                else None
            ),
            error=operation.error_message,
            metadata_warning=warning,
            recovery_state=operation.physical_phase.value,
            durable=True,
            classification_method=(
                context.classification_method if context else None
            ),
            classification_source=(
                context.classification_source if context else None
            ),
        )

    @staticmethod
    def _status(operation: OperationRecord) -> ActivityStatus:
        if operation.operation_status is OperationStatus.COMPLETE:
            return (
                ActivityStatus.RESTORED
                if operation.inverse_of_operation_id
                else ActivityStatus.COMPLETED
            )
        if operation.operation_status is OperationStatus.DUPLICATE:
            return ActivityStatus.DUPLICATE
        if operation.operation_status is OperationStatus.ABORTED:
            return ActivityStatus.FAILED
        if operation.operation_status is OperationStatus.NEEDS_REVIEW:
            return ActivityStatus.NEEDS_REVIEW
        if operation.operation_status is OperationStatus.OPEN:
            return (
                ActivityStatus.WARNING
                if operation.physical_phase is PhysicalPhase.PHYSICAL_COMMITTED
                else ActivityStatus.IN_PROGRESS
            )
        return ActivityStatus.UNKNOWN

    @staticmethod
    def _metadata_warning(
        operation: OperationRecord,
        effects: tuple[OperationEffect, ...],
    ) -> str | None:
        failures = [
            effect.error_message or f"{effect.effect_type.value} requires attention"
            for effect in effects
            if effect.required
            and effect.state in {EffectState.FAILED, EffectState.UNKNOWN}
        ]
        if failures:
            return "; ".join(failures)
        if (
            operation.operation_status is OperationStatus.OPEN
            and operation.physical_phase is PhysicalPhase.PHYSICAL_COMMITTED
        ):
            return "The move completed, but metadata finalization is incomplete."
        return None


def _integer_limit(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("limit must be an integer")
    return value


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

