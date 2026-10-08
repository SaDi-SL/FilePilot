from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

from app.config_loader import get_external_config_path
from app.classifier import build_extension_lookup
from app.mover import MoveResult, MoveStatus, PreviewStatus, move_file_with_retries, preview_move
from app.operation_journal import (
    MAX_RECENT_OPERATIONS,
    JournalError,
    JournalNotFoundError,
    OperationJournal,
)
from app.product_configuration import (
    CandidateClassificationPreview,
    ConfigurationDataState,
    ConfigurationSaveResult,
    ConfigurationSaveStatus,
    ConfigurationValidationResult,
    ProductConfigurationCandidate,
    ProductConfigurationError,
    ProductConfigurationSnapshot,
    ProductConfigurationStore,
    ProductFolderSettings,
    ProductRule,
    ProductWatchFolder,
    StaleConfigurationError,
    configuration_revision,
)
from app.embedding_service import OllamaEmbeddingProvider
from app.local_rag import LocalRAGService, RAGAnswer
from app.ai_service import OllamaProvider
from app.product_search import ProductSearch, SearchRefreshResult
from app.search_index import SearchIndexError, SearchResult, SemanticSearchResult
from app.product_read_model import (
    ActivityRecord,
    ActivityStatus,
    ProductDataState,
    ProductMetrics,
    ProductReadModel,
    ProductSnapshot,
)
from app.product_identity import PRODUCT_IDENTITY, ProductIdentity
from app.product_settings import (
    ProductSettingsCandidate,
    ProductSettingsSnapshot,
    ProductSettingsStore,
    SettingsSaveResult,
    SettingsValidationIssue,
    SettingsValidationResult,
)
from app.product_safety import (
    OperationPreview,
    RecoveryAction,
    RecoveryActionResult,
    RecoveryActionStatus,
    RecoveryItem,
    RecoverySnapshot,
    SafetyDataState,
    operation_preview_from_domain,
    recovery_item_from_assessment,
)
from app.recovery import (
    RecoveryBlockedError,
    RecoveryDecision,
    RecoveryError,
    RecoveryReport,
    assess_operation,
    reconcile_operation,
)
from app.undo import (
    UndoAvailability,
    UndoResult,
    UndoStatus,
    evaluate_undo,
    undo_operation as execute_undo,
)

logger = logging.getLogger(__name__)


class StartupStatus(str, Enum):
    READY = "ready"
    SETUP_REQUIRED = "setup_required"
    BLOCKED = "blocked"
    ERROR = "error"


class MonitorState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    BLOCKED = "blocked"
    ERROR = "error"


@dataclass(frozen=True)
class StartupResult:
    status: StartupStatus
    config: dict | None = None
    recovery_report: RecoveryReport | None = None
    blocking_operation_ids: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class ActivityEvent:
    source: Path
    category: str
    status: str
    move_result: MoveResult | None = None
    processing_error: str | None = None
    occurred_at_utc: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def filename(self) -> str:
        return self.source.name

    @property
    def actual_destination(self) -> Path | None:
        return self.move_result.destination if self.move_result else None

    @property
    def operation_id(self) -> str | None:
        return self.move_result.operation_id if self.move_result else None

    @property
    def duplicate_target(self) -> Path | None:
        return self.move_result.duplicate_of if self.move_result else None

    @property
    def error(self) -> str | None:
        if self.move_result and self.move_result.error:
            return self.move_result.error
        return self.processing_error

    @property
    def metadata_warning(self) -> str | None:
        return self.move_result.metadata_error if self.move_result else None


def product_record_from_activity_event(
    event: ActivityEvent,
    record_id: str,
) -> ActivityRecord:
    return ProductReadModel.from_live_event(event, record_id)


StateSubscriber = Callable[[MonitorState], None]
ActivitySubscriber = Callable[[ActivityEvent], None]


class FilePilotService:
    """Process-scoped owner of FilePilot bootstrap and monitor lifecycle."""

    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        monitor_builder: Callable[[], tuple[dict, object]] | None = None,
        journal_path: str | Path | None = None,
        product_reader: ProductReadModel | None = None,
        product_search: ProductSearch | None = None,
    ) -> None:
        self._config_path = (
            Path(config_path) if config_path is not None else get_external_config_path()
        )
        self._monitor_builder = monitor_builder
        self._journal_path = Path(journal_path) if journal_path is not None else None
        self._product_reader = product_reader or ProductReadModel(journal_path)
        self._product_search = product_search or ProductSearch(
            embedding_provider=OllamaEmbeddingProvider()
        )
        self._configuration_store = ProductConfigurationStore(self._config_path)
        self._settings_store = ProductSettingsStore(self._configuration_store)
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._subscriber_lock = threading.Lock()
        self._monitor = None
        self._config: dict | None = None
        self._startup_result: StartupResult | None = None
        self._monitor_state = MonitorState.STOPPED
        self._failed_folders: tuple[str, ...] = ()
        self._last_error: str | None = None
        self._activity_subscribers: list[ActivitySubscriber] = []
        self._state_subscribers: list[StateSubscriber] = []

    @property
    def startup_result(self) -> StartupResult | None:
        with self._state_lock:
            return self._startup_result

    @property
    def startup_status(self) -> StartupStatus | None:
        result = self.startup_result
        return result.status if result is not None else None

    @property
    def monitor_state(self) -> MonitorState:
        with self._state_lock:
            return self._monitor_state

    @property
    def monitor(self):
        with self._state_lock:
            return self._monitor

    @property
    def config(self) -> dict | None:
        with self._state_lock:
            return self._config

    @property
    def failed_folders(self) -> tuple[str, ...]:
        with self._state_lock:
            return self._failed_folders

    @property
    def last_error(self) -> str | None:
        with self._state_lock:
            return self._last_error

    def subscribe_activity(self, callback: ActivitySubscriber) -> Callable[[], None]:
        with self._subscriber_lock:
            self._activity_subscribers.append(callback)

        def unsubscribe() -> None:
            with self._subscriber_lock:
                if callback in self._activity_subscribers:
                    self._activity_subscribers.remove(callback)

        return unsubscribe

    def subscribe_state(self, callback: StateSubscriber) -> Callable[[], None]:
        with self._subscriber_lock:
            self._state_subscribers.append(callback)

        def unsubscribe() -> None:
            with self._subscriber_lock:
                if callback in self._state_subscribers:
                    self._state_subscribers.remove(callback)

        return unsubscribe

    def get_product_snapshot(self, limit: int = 20) -> ProductSnapshot:
        """Read durable product data without changing lifecycle or storage."""
        return self._product_reader.read(limit)

    def get_product_metrics(self) -> ProductMetrics:
        return self.get_product_snapshot(limit=1).metrics

    def get_recent_activity(self, limit: int = 20) -> tuple[ActivityRecord, ...]:
        return self.get_product_snapshot(limit=limit).activity

    def search_files(
        self,
        query: str,
        *,
        limit: int = 25,
    ) -> tuple[SearchResult, ...]:
        """Search the durable local catalog without changing user files."""
        return self._product_search.search(query, limit=limit)

    def semantic_search_files(
        self,
        query: str,
        *,
        limit: int = 25,
    ) -> tuple[SemanticSearchResult, ...]:
        """Search the local semantic catalog without changing user files."""
        return self._product_search.semantic_search(query, limit=limit)

    def ask_files(self, question: str) -> RAGAnswer:
        """Answer from the local catalog using the configured Ollama model only."""
        with self._state_lock:
            ai = dict((self._config or {}).get("ai") or {})
        provider = OllamaProvider(model=ai.get("ollama_model") or "mistral")
        return LocalRAGService(self._product_search, provider).ask(question)

    def refresh_search_index(self) -> SearchRefreshResult:
        """Reconcile the local search catalog with the configured organized root."""
        with self._lifecycle_lock:
            if self.startup_status is not StartupStatus.READY:
                raise SearchIndexError(
                    "Search indexing requires a ready FilePilot configuration"
                )
            config = dict(self.config or {})
            organized = config.get("organized_base_folder")
            if not isinstance(organized, str) or not organized.strip():
                raise SearchIndexError(
                    "Search indexing requires a configured organized folder"
                )
            organized_root = Path(organized)
        lexical = self._product_search.refresh(organized_root)
        try:
            semantic = self._product_search.refresh_semantic_embeddings()
        except SearchIndexError:
            return lexical
        return SearchRefreshResult(
            scanned=lexical.scanned,
            indexed=lexical.indexed,
            unchanged=lexical.unchanged,
            removed=lexical.removed,
            failed=lexical.failed,
            errors=lexical.errors,
            semantic_embedded=semantic.embedded,
            semantic_unchanged=semantic.unchanged,
            semantic_failed=semantic.failed,
            semantic_available=True,
        )

    def get_product_configuration(self) -> ProductConfigurationSnapshot:
        """Read one immutable product configuration snapshot."""
        with self._lifecycle_lock:
            return self._configuration_store.read_snapshot(
                changes_allowed=self._configuration_changes_allowed()
            )

    def get_product_identity(self) -> ProductIdentity:
        """Return the static identity used by source and packaged builds."""
        return PRODUCT_IDENTITY

    def get_product_settings(self) -> ProductSettingsSnapshot:
        """Read an immutable, secret-redacted Settings snapshot."""
        with self._lifecycle_lock:
            return self._settings_store.read_snapshot(
                changes_allowed=self._configuration_changes_allowed()
            )

    def validate_product_settings(
        self,
        candidate: ProductSettingsCandidate,
    ) -> SettingsValidationResult:
        """Validate Settings without changing configuration or runtime state."""
        try:
            document, _, _ = self._configuration_store.read_document()
        except (OSError, json.JSONDecodeError, ProductConfigurationError):
            return SettingsValidationResult(
                False,
                candidate,
                (
                    SettingsValidationIssue(
                        "settings",
                        "SETTINGS_UNAVAILABLE",
                        "Settings could not be validated against the current configuration.",
                    ),
                ),
            )
        return self._settings_store.validate_against_document(candidate, document)

    def save_product_settings(
        self,
        candidate: ProductSettingsCandidate,
        expected_revision: str,
    ) -> SettingsSaveResult:
        """Save supported Settings through the shared configuration transaction."""
        with self._lifecycle_lock:
            if not self._configuration_changes_allowed():
                return SettingsSaveResult(
                    ConfigurationSaveStatus.NOT_ALLOWED,
                    "Stop monitoring before applying Settings changes.",
                    self._settings_store.read_snapshot(changes_allowed=False),
                )
            try:
                current, revision, previous_bytes = (
                    self._configuration_store.read_document()
                )
            except (OSError, json.JSONDecodeError, ProductConfigurationError) as error:
                return SettingsSaveResult(
                    ConfigurationSaveStatus.WRITE_FAILED,
                    f"Settings could not be read safely: {error}",
                )
            if revision != expected_revision:
                return SettingsSaveResult(
                    ConfigurationSaveStatus.STALE,
                    "Configuration changed elsewhere. Refresh before saving Settings.",
                    self._settings_store.read_snapshot(changes_allowed=True),
                )
            try:
                validation = self._settings_store.validate_against_document(
                    candidate,
                    current,
                )
            except (AttributeError, TypeError, ValueError) as error:
                return SettingsSaveResult(
                    ConfigurationSaveStatus.INVALID,
                    f"Settings candidate is malformed: {error}",
                )
            if not validation.valid:
                return SettingsSaveResult(
                    ConfigurationSaveStatus.INVALID,
                    "Fix Settings validation issues before saving.",
                    self._settings_store.read_snapshot(changes_allowed=True),
                    validation.issues,
                )
            try:
                document = self._settings_store.document_for_candidate(
                    current,
                    validation.candidate,
                )
            except ProductConfigurationError as error:
                return SettingsSaveResult(
                    ConfigurationSaveStatus.INVALID,
                    str(error),
                    issues=validation.issues,
                )

            status, message = self._commit_configuration_document(
                document,
                revision,
                previous_bytes,
            )
            if status is ConfigurationSaveStatus.SAVED:
                from app.ai_classifier import reset_ai_classifier

                reset_ai_classifier()
                message = "Settings saved. Monitoring remains stopped."
            return SettingsSaveResult(
                status,
                message,
                self._settings_store.read_snapshot(
                    changes_allowed=self._configuration_changes_allowed()
                ),
            )

    def validate_product_configuration(
        self,
        candidate: ProductConfigurationCandidate,
    ) -> ConfigurationValidationResult:
        """Validate and normalize a complete candidate without mutation."""
        return self._configuration_store.validate(candidate)

    def preview_candidate_classification(
        self,
        rules: tuple[ProductRule, ...],
        filename: str,
    ) -> CandidateClassificationPreview:
        """Classify a filename using only the unsaved extension-rule candidate."""
        return self._configuration_store.preview_classification(rules, filename)

    def save_product_configuration(
        self,
        candidate: ProductConfigurationCandidate,
        expected_revision: str,
    ) -> ConfigurationSaveResult:
        """Atomically persist, rebuild, or roll back one complete candidate."""
        with self._lifecycle_lock:
            if not self._configuration_changes_allowed():
                return ConfigurationSaveResult(
                    ConfigurationSaveStatus.NOT_ALLOWED,
                    "Stop monitoring before applying folder or rule changes.",
                    self._configuration_store.read_snapshot(changes_allowed=False),
                )
            try:
                current, revision, previous_bytes = (
                    self._configuration_store.read_document()
                )
            except (OSError, json.JSONDecodeError, ProductConfigurationError) as error:
                return ConfigurationSaveResult(
                    ConfigurationSaveStatus.WRITE_FAILED,
                    f"Configuration could not be read safely: {error}",
                )
            if revision != expected_revision:
                return ConfigurationSaveResult(
                    ConfigurationSaveStatus.STALE,
                    "Configuration changed elsewhere. Refresh before saving.",
                    self._configuration_store.read_snapshot(changes_allowed=True),
                )

            try:
                validation = self._configuration_store.validate(candidate)
            except (AttributeError, TypeError, ValueError) as error:
                return ConfigurationSaveResult(
                    ConfigurationSaveStatus.INVALID,
                    f"Configuration candidate is malformed: {error}",
                    self._configuration_store.read_snapshot(changes_allowed=True),
                )
            if not validation.valid:
                return ConfigurationSaveResult(
                    ConfigurationSaveStatus.INVALID,
                    "Fix configuration validation issues before saving.",
                    self._configuration_store.read_snapshot(changes_allowed=True),
                    validation.issues,
                )
            try:
                document = self._configuration_store.document_for_candidate(
                    current,
                    validation.candidate,
                )
            except ProductConfigurationError as error:
                return ConfigurationSaveResult(
                    ConfigurationSaveStatus.INVALID,
                    str(error),
                    issues=validation.issues,
                )

            status, message = self._commit_configuration_document(
                document,
                revision,
                previous_bytes,
            )
            return ConfigurationSaveResult(
                status,
                message,
                self._configuration_store.read_snapshot(
                    changes_allowed=self._configuration_changes_allowed()
                ),
            )

    def _commit_configuration_document(
        self,
        document: dict,
        revision: str,
        previous_bytes: bytes,
    ) -> tuple[ConfigurationSaveStatus, str]:
        """Shared atomic write, runtime rebuild, and rollback transaction."""
        with self._state_lock:
            previous_runtime = (
                self._monitor,
                self._config,
                self._startup_result,
                self._monitor_state,
                self._failed_folders,
                self._last_error,
            )
        try:
            self._configuration_store.write_document_atomic(
                document,
                expected_revision=revision,
            )
        except StaleConfigurationError:
            return (
                ConfigurationSaveStatus.STALE,
                "Configuration changed elsewhere. Refresh before saving.",
            )
        except (OSError, ProductConfigurationError) as error:
            return (
                ConfigurationSaveStatus.WRITE_FAILED,
                f"Configuration was not saved: {error}",
            )

        try:
            rebuilt = self.bootstrap(force=True)
        except Exception as error:
            logger.error(
                "Configuration runtime reload failed unexpectedly",
                exc_info=True,
            )
            rebuilt = StartupResult(
                StartupStatus.ERROR,
                config=document,
                error=str(error),
            )
        if rebuilt.status is StartupStatus.READY:
            return (
                ConfigurationSaveStatus.SAVED,
                "Configuration saved. Monitoring remains stopped.",
            )

        try:
            self._configuration_store.write_bytes_atomic(
                previous_bytes,
                expected_revision=configuration_revision(document),
            )
        except (OSError, ProductConfigurationError) as rollback_error:
            message = rebuilt.error or "Runtime reload failed"
            self._set_monitor_state(
                MonitorState.ERROR,
                error=f"{message}; configuration rollback failed: {rollback_error}",
            )
            return (
                ConfigurationSaveStatus.ROLLBACK_FAILED,
                "Runtime reload and configuration rollback failed. Monitoring remains stopped.",
            )

        (
            previous_monitor,
            previous_config,
            previous_startup,
            previous_state,
            previous_failed,
            previous_error,
        ) = previous_runtime
        with self._state_lock:
            self._monitor = previous_monitor
            self._config = previous_config
            self._startup_result = previous_startup
            self._monitor_state = previous_state
            self._failed_folders = previous_failed
            self._last_error = previous_error
        self._set_monitor_state(
            previous_state,
            error=previous_error,
            failed_folders=previous_failed,
        )
        return (
            ConfigurationSaveStatus.RELOAD_FAILED,
            "The candidate could not be loaded, so FilePilot restored the previous configuration and runtime.",
        )

    def preview_file(self, source_file: str | Path) -> OperationPreview:
        """Preview one source through the configured non-mutating planner."""
        source = Path(source_file)
        with self._lifecycle_lock:
            if self.startup_status is not StartupStatus.READY:
                return OperationPreview.unavailable(
                    source,
                    "Preview requires a ready FilePilot configuration and does not start monitoring.",
                )
            config = dict(self.config or {})
        try:
            organized_root = Path(config["organized_base_folder"])
            resolved_source = source.resolve(strict=False)
            if resolved_source.is_relative_to(organized_root.resolve(strict=False)):
                return OperationPreview(
                    SafetyDataState.AVAILABLE,
                    PreviewStatus.UNSAFE,
                    source,
                    message=(
                        "Files already inside FilePilot's organized output cannot be previewed. "
                        "Nothing changed."
                    ),
                )
            rules = config.get("rules", {})
            extension_lookup = build_extension_lookup(rules)
            suffix = source.suffix.lower().strip()
            classification_method = (
                "extension" if suffix in extension_lookup else "fallback"
            )
            result = preview_move(
                source,
                dict(config["destination_folders"]),
                extension_lookup,
                str(config["hash_db_file"]),
                bool(config.get("archive_by_date", False)),
                organized_root,
                classification_source=classification_method,
            )
        except (KeyError, OSError, TypeError, ValueError) as error:
            logger.warning("File preview is unavailable: %s", error)
            return OperationPreview.unavailable(
                source,
                "Preview configuration is unavailable. Nothing changed.",
            )
        return operation_preview_from_domain(
            result,
            classification_method=classification_method,
        )

    def organize_file(self, source_file: str | Path) -> MoveResult:
        """Safely organize one user-selected file after full execution-time revalidation."""
        source = Path(source_file)
        with self._lifecycle_lock:
            if self.startup_status is not StartupStatus.READY:
                return MoveResult(
                    MoveStatus.MOVE_FAILED,
                    source,
                    error="FilePilot configuration is not ready. Nothing changed.",
                )
            if self.monitor_state is not MonitorState.STOPPED:
                return MoveResult(
                    MoveStatus.MOVE_FAILED,
                    source,
                    error="Stop automatic organization before organizing a selected file. Nothing changed.",
                )

            config = dict(self.config or {})
            preview = self.preview_file(source)
            if (
                preview.state is not SafetyDataState.AVAILABLE
                or preview.status is not PreviewStatus.READY
                or not preview.execution_possible
                or not preview.category
            ):
                return MoveResult(
                    MoveStatus.MOVE_FAILED,
                    source,
                    error=preview.message or "The current file evidence is not safe to execute. Nothing changed.",
                )

            try:
                organized_root = Path(config["organized_base_folder"])
                if source.resolve(strict=False).is_relative_to(
                    organized_root.resolve(strict=False)
                ):
                    return MoveResult(
                        MoveStatus.MOVE_FAILED,
                        source,
                        error="Files already inside the organized output cannot be organized again.",
                    )
                rules = dict(config["rules"])
                destinations = dict(config["destination_folders"])
                extension_lookup = build_extension_lookup(rules)
                suffix = source.suffix.lower().strip()
                classification_method = (
                    "extension" if suffix in extension_lookup else "fallback"
                )
                journal = OperationJournal(self._journal_path)
                result = move_file_with_retries(
                    source_file=source,
                    destination_folders=destinations,
                    extension_lookup=extension_lookup,
                    stats_file=str(config["stats_file"]),
                    history_file=str(config["history_file"]),
                    hash_db_file=str(config["hash_db_file"]),
                    archive_by_date=bool(config.get("archive_by_date", False)),
                    rules=rules,
                    organized_root=organized_root,
                    classification_method=classification_method,
                    smart_source="manual_preview",
                    category_override=preview.category,
                    journal=journal,
                )
            except (KeyError, OSError, TypeError, ValueError, JournalError) as error:
                logger.warning("Manual organization was refused: %s", error)
                return MoveResult(
                    MoveStatus.MOVE_FAILED,
                    source,
                    error=f"FilePilot could not safely organize this file: {error}",
                )

            status = {
                MoveStatus.MOVED: "moved",
                MoveStatus.DUPLICATE: "duplicate",
                MoveStatus.HASH_CHECK_FAILED: "hash_check_failed",
                MoveStatus.MOVE_FAILED: "failed",
            }[result.status]
            self._publish_activity(
                ActivityEvent(
                    source=source,
                    category=preview.category,
                    status=status,
                    move_result=result,
                    occurred_at_utc=datetime.now(timezone.utc),
                )
            )
            return result

    def get_undo_availability(self, operation_id: str) -> UndoAvailability:
        """Evaluate undo from current journal and filesystem evidence."""
        with self._lifecycle_lock:
            unavailable = self._undo_lifecycle_unavailable(operation_id)
            if unavailable is not None:
                return unavailable
            try:
                journal = OperationJournal.open_existing_read_only(self._journal_path)
                return evaluate_undo(journal, operation_id)
            except JournalError as error:
                return UndoAvailability(
                    operation_id,
                    False,
                    f"Undo evidence is unavailable: {error}",
                    unavailable_status=UndoStatus.NOT_ELIGIBLE,
                )

    def undo_operation(self, operation_id: str) -> UndoResult:
        """Execute one authoritative stopped-state undo with full revalidation."""
        with self._lifecycle_lock:
            unavailable = self._undo_lifecycle_unavailable(operation_id)
            if unavailable is not None:
                return UndoResult(
                    unavailable.unavailable_status or UndoStatus.NOT_ELIGIBLE,
                    operation_id,
                    source_path=unavailable.current_path,
                    destination_path=unavailable.restore_path,
                    reason=unavailable.reason,
                )
            config = self.config or {}
            hash_db_file = config.get("hash_db_file")
            if not isinstance(hash_db_file, str) or not hash_db_file:
                return UndoResult(
                    UndoStatus.FAILED,
                    operation_id,
                    reason="Undo metadata storage is unavailable; nothing was changed",
                )
            try:
                journal = OperationJournal(self._journal_path)
            except JournalError as error:
                return UndoResult(
                    UndoStatus.FAILED,
                    operation_id,
                    reason=f"Undo journal is unavailable; nothing was changed: {error}",
                )
            return execute_undo(
                journal,
                operation_id,
                hash_db_file=hash_db_file,
            )

    def get_recovery_snapshot(
        self,
        limit: int = MAX_RECENT_OPERATIONS,
        offset: int = 0,
    ) -> RecoverySnapshot:
        """Inspect a bounded recovery inventory without reconciling it."""
        if not isinstance(limit, int) or isinstance(limit, bool):
            return RecoverySnapshot(
                SafetyDataState.ERROR,
                error="Recovery limit must be an integer",
            )
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            return RecoverySnapshot(
                SafetyDataState.ERROR,
                error="Recovery offset must be a non-negative integer",
            )
        bounded_limit = max(1, min(limit, MAX_RECENT_OPERATIONS))
        with self._lifecycle_lock:
            startup = self.startup_result
            blocking = set(startup.blocking_operation_ids if startup else ())
            hash_db_file = self._recovery_hash_db_file()
            action_allowed = (
                self._recovery_actions_available()
                and hash_db_file is not None
            )
            try:
                journal = OperationJournal.open_existing_read_only(self._journal_path)
                operations, total = journal.read_incomplete_snapshot(
                    bounded_limit,
                    offset,
                )
                items = tuple(
                    recovery_item_from_assessment(
                        assess_operation(journal, operation),
                        action_allowed=action_allowed,
                        blocks_monitoring=operation.operation_id in blocking,
                    )
                    for operation in operations
                )
            except JournalError as error:
                return RecoverySnapshot.unavailable(
                    f"Recovery evidence is unavailable: {error}"
                )
            except (OSError, RecoveryError) as error:
                logger.warning("Recovery assessment failed: %s", error)
                return RecoverySnapshot(
                    SafetyDataState.ERROR,
                    error="Recovery evidence could not be inspected safely.",
                )
        return RecoverySnapshot(
            SafetyDataState.AVAILABLE,
            items,
            total,
            offset + len(items) < total,
            offset=offset,
        )

    def reconcile_recovery_item(
        self,
        operation_id: str,
        action: RecoveryAction,
    ) -> RecoveryActionResult:
        """Revalidate and apply one backend-approved recovery recommendation."""
        if action is not RecoveryAction.APPLY_SAFE_RECOMMENDATION:
            return RecoveryActionResult(
                operation_id,
                RecoveryActionStatus.NOT_ALLOWED,
                "That recovery action is not supported.",
            )
        with self._lifecycle_lock:
            if not self._recovery_actions_available():
                return RecoveryActionResult(
                    operation_id,
                    RecoveryActionStatus.NOT_ALLOWED,
                    "Stop monitoring before applying a safe recovery action.",
                )
            hash_db_file = self._recovery_hash_db_file()
            if hash_db_file is None:
                return RecoveryActionResult(
                    operation_id,
                    RecoveryActionStatus.NOT_ALLOWED,
                    "Resolved recovery metadata storage is unavailable.",
                )
            try:
                journal = OperationJournal(self._journal_path)
                operation = journal.get_operation(operation_id)
                assessment = assess_operation(journal, operation)
                if assessment.decision is RecoveryDecision.NEEDS_REVIEW:
                    return RecoveryActionResult(
                        operation_id,
                        RecoveryActionStatus.NOT_ALLOWED,
                        assessment.reason,
                        manual_review_required=True,
                    )
                final = reconcile_operation(
                    journal,
                    operation_id,
                    hash_db_file=hash_db_file,
                )
            except JournalError:
                return RecoveryActionResult(
                    operation_id,
                    RecoveryActionStatus.FAILED,
                    "The recovery operation is unavailable; nothing unsafe was attempted.",
                )
            except (OSError, RecoveryError) as error:
                logger.warning("Safe recovery action failed: %s", error)
                return RecoveryActionResult(
                    operation_id,
                    RecoveryActionStatus.FAILED,
                    "FilePilot could not complete the safe recovery action.",
                    manual_review_required=True,
                )
            if final is None:
                result = RecoveryActionResult(
                    operation_id,
                    RecoveryActionStatus.RECONCILED,
                    "The operation was reconciled using verified evidence.",
                )
                if (
                    self._startup_result is not None
                    and self._startup_result.status is StartupStatus.BLOCKED
                ):
                    self.bootstrap(force=True)
                return result
            return RecoveryActionResult(
                operation_id,
                RecoveryActionStatus.STILL_NEEDS_REVIEW,
                final.reason,
                manual_review_required=True,
            )

    def bootstrap(self, *, force: bool = False) -> StartupResult:
        with self._lifecycle_lock:
            current = self.startup_result
            if current is not None and not force:
                return current
            if force and self.monitor is not None and getattr(self.monitor, "is_running", False):
                return StartupResult(
                    StartupStatus.ERROR,
                    config=self.config,
                    error="Cannot force bootstrap while monitoring is running; use reload",
                )

            inspected = self._inspect_configuration()
            if inspected.status is not StartupStatus.READY:
                self._set_startup_result(inspected)
                state = (
                    MonitorState.ERROR
                    if inspected.status is StartupStatus.ERROR
                    else MonitorState.STOPPED
                )
                self._set_monitor_state(state, error=inspected.error)
                return inspected

            try:
                builder = self._monitor_builder
                if builder is None:
                    from app.main import build_monitor_from_config

                    config, monitor = build_monitor_from_config(inspected.config or {})
                else:
                    config, monitor = builder()
            except RecoveryBlockedError as error:
                result = StartupResult(
                    StartupStatus.BLOCKED,
                    config=inspected.config,
                    recovery_report=error.report,
                    blocking_operation_ids=error.blocking_operation_ids,
                    blocking_reasons=self._recovery_reasons(
                        error.report,
                        error.blocking_operation_ids,
                    ),
                    error=str(error),
                )
                self._set_startup_result(result)
                self._set_monitor_state(MonitorState.BLOCKED, error=str(error))
                return result
            except (RecoveryError, JournalError) as error:
                result = StartupResult(
                    StartupStatus.BLOCKED,
                    config=inspected.config,
                    error=str(error),
                )
                self._set_startup_result(result)
                self._set_monitor_state(MonitorState.BLOCKED, error=str(error))
                return result
            except Exception as error:
                logger.error("FilePilot bootstrap failed", exc_info=True)
                result = StartupResult(
                    StartupStatus.ERROR,
                    config=inspected.config,
                    error=str(error),
                )
                self._set_startup_result(result)
                self._set_monitor_state(MonitorState.ERROR, error=str(error))
                return result

            try:
                self._bind_activity_callback(monitor)
            except Exception as error:
                logger.error("FilePilot monitor binding failed", exc_info=True)
                try:
                    monitor.stop_all()
                except Exception:
                    logger.error("FilePilot monitor cleanup failed", exc_info=True)
                result = StartupResult(
                    StartupStatus.ERROR,
                    config=inspected.config,
                    error=str(error),
                )
                self._set_startup_result(result)
                self._set_monitor_state(MonitorState.ERROR, error=str(error))
                return result
            result = StartupResult(
                StartupStatus.READY,
                config=config,
                recovery_report=getattr(monitor, "recovery_report", None),
            )
            with self._state_lock:
                self._monitor = monitor
                self._config = config
                self._startup_result = result
                self._failed_folders = ()
                self._last_error = None
                runtime_journal = getattr(monitor, "operation_journal", None)
                if (
                    self._journal_path is None
                    and isinstance(runtime_journal, OperationJournal)
                ):
                    self._journal_path = runtime_journal.database_path
            self._set_monitor_state(MonitorState.STOPPED)
            return result

    def start(self) -> MonitorState:
        with self._lifecycle_lock:
            startup = self.startup_result or self.bootstrap()
            monitor = self.monitor
            if (
                startup.status is StartupStatus.READY
                and monitor is not None
                and getattr(monitor, "is_running", False)
            ):
                if self.monitor_state is not MonitorState.RUNNING:
                    self._set_monitor_state(MonitorState.RUNNING)
                return MonitorState.RUNNING
            if startup.status is StartupStatus.READY:
                recovery_refresh = self._recovery_refresh_required()
                if recovery_refresh is None:
                    self._set_monitor_state(
                        MonitorState.ERROR,
                        error=(
                            "Recovery evidence is unavailable; monitoring was not started"
                        ),
                    )
                    return MonitorState.ERROR
                if recovery_refresh:
                    startup = self.bootstrap(force=True)
            if startup.status is StartupStatus.BLOCKED:
                self._set_monitor_state(MonitorState.BLOCKED, error=startup.error)
                return MonitorState.BLOCKED
            if startup.status is not StartupStatus.READY:
                state = (
                    MonitorState.STOPPED
                    if startup.status is StartupStatus.SETUP_REQUIRED
                    else MonitorState.ERROR
                )
                self._set_monitor_state(state, error=startup.error)
                return state
            if self.monitor_state is MonitorState.RUNNING:
                return MonitorState.RUNNING

            monitor = self.monitor
            if monitor is None:
                self._set_monitor_state(
                    MonitorState.ERROR,
                    error="Runtime monitor is unavailable",
                )
                return MonitorState.ERROR

            expected = self._active_folder_map(self.config or {})
            self._set_monitor_state(MonitorState.STARTING)
            start_error = None
            try:
                monitor.start_all()
            except Exception as error:
                start_error = error

            running = self._running_folder_map(monitor)
            failed_keys = tuple(key for key in expected if key not in running)
            failed = tuple(expected[key] for key in failed_keys)
            complete = bool(expected) and not failed and len(running) == len(expected)

            if start_error is None and complete:
                self._set_monitor_state(MonitorState.RUNNING, failed_folders=())
                return MonitorState.RUNNING

            reason = (
                str(start_error)
                if start_error is not None
                else "No active watch folders started"
                if not expected
                else "One or more watch folders did not start"
            )
            try:
                monitor.stop_all()
            except Exception as cleanup_error:
                reason = f"{reason}; partial startup cleanup failed: {cleanup_error}"
            self._set_monitor_state(
                MonitorState.ERROR,
                error=reason,
                failed_folders=failed or tuple(expected.values()),
            )
            return MonitorState.ERROR

    def stop(self) -> MonitorState:
        with self._lifecycle_lock:
            startup = self.startup_result
            if startup is not None and startup.status is StartupStatus.BLOCKED:
                self._set_monitor_state(MonitorState.BLOCKED, error=startup.error)
                return MonitorState.BLOCKED

            monitor = self.monitor
            if monitor is None:
                self._set_monitor_state(MonitorState.STOPPED)
                return MonitorState.STOPPED
            if self.monitor_state is MonitorState.STOPPED and not monitor.is_running:
                return MonitorState.STOPPED

            self._set_monitor_state(MonitorState.STOPPING)
            try:
                monitor.stop_all()
            except Exception as error:
                self._set_monitor_state(MonitorState.ERROR, error=str(error))
                return MonitorState.ERROR
            if getattr(monitor, "is_running", False):
                self._set_monitor_state(
                    MonitorState.ERROR,
                    error="One or more watch folders remained active after stop",
                )
                return MonitorState.ERROR
            self._set_monitor_state(MonitorState.STOPPED, failed_folders=())
            return MonitorState.STOPPED

    def reload(self, *, preserve_running: bool = True) -> StartupResult:
        with self._lifecycle_lock:
            was_running = self.monitor_state is MonitorState.RUNNING
            if self.monitor is not None and (
                was_running or getattr(self.monitor, "is_running", False)
            ):
                stopped = self.stop()
                if stopped is not MonitorState.STOPPED:
                    return StartupResult(
                        StartupStatus.ERROR,
                        config=self.config,
                        error=self.last_error,
                    )

            result = self.bootstrap(force=True)
            if result.status is StartupStatus.READY and was_running and preserve_running:
                state = self.start()
                if state is not MonitorState.RUNNING:
                    return StartupResult(
                        StartupStatus.ERROR,
                        config=result.config,
                        recovery_report=result.recovery_report,
                        error=self.last_error or "Monitoring did not restart",
                    )
            return result

    def start_folder(self, path: str) -> MonitorState:
        """Activate one configured folder while keeping aggregate state truthful."""
        with self._lifecycle_lock:
            if self.startup_status is not StartupStatus.READY or self.monitor is None:
                return self._unavailable_start_state()
            if not getattr(self.monitor, "is_running", False):
                recovery_refresh = self._recovery_refresh_required()
                if recovery_refresh is None:
                    self._set_monitor_state(
                        MonitorState.ERROR,
                        error=(
                            "Recovery evidence is unavailable; monitoring was not started"
                        ),
                    )
                    return MonitorState.ERROR
                if recovery_refresh:
                    startup = self.bootstrap(force=True)
                    if startup.status is not StartupStatus.READY or self.monitor is None:
                        return self._unavailable_start_state()
            self._set_monitor_state(MonitorState.STARTING)
            try:
                self.monitor.set_folder_active(path, True)
                started = self.monitor.start_folder(path)
                if not started and self.monitor.folder_status(path) != "running":
                    raise RuntimeError(f"Watch folder did not start: {path}")
            except Exception as error:
                self._fail_closed_after_partial_start(str(error))
                return MonitorState.ERROR
            return self._settle_folder_state()

    def stop_folder(self, path: str) -> MonitorState:
        """Deactivate one folder and recompute aggregate service state."""
        with self._lifecycle_lock:
            if self.startup_status is not StartupStatus.READY or self.monitor is None:
                return self._unavailable_start_state()
            self._set_monitor_state(MonitorState.STOPPING)
            try:
                self.monitor.set_folder_active(path, False)
                self.monitor.stop_folder(path)
            except Exception as error:
                self._fail_closed_after_partial_start(str(error))
                return MonitorState.ERROR
            return self._settle_folder_state()

    def add_watch_folder(self, path: str, *, label: str = "") -> bool:
        """Add a folder through the sole runtime owner."""
        with self._lifecycle_lock:
            if self.startup_status is not StartupStatus.READY or self.monitor is None:
                return False
            was_running = self.monitor_state is MonitorState.RUNNING
            added = self.monitor.add_watch_folder(
                path,
                label=label,
                active=True,
            )
            if not added:
                return False
            if was_running:
                state = self.start_folder(path)
                if state is not MonitorState.RUNNING:
                    self.monitor.remove_watch_folder(path)
                    with self._state_lock:
                        self._config = self.monitor.config
                    return False
            with self._state_lock:
                self._config = self.monitor.config
            return True

    def remove_watch_folder(self, path: str) -> bool:
        """Remove a folder through the sole runtime owner."""
        with self._lifecycle_lock:
            if self.startup_status is not StartupStatus.READY or self.monitor is None:
                return False
            removed = self.monitor.remove_watch_folder(path)
            if not removed:
                return False
            with self._state_lock:
                self._config = self.monitor.config
            self._settle_folder_state()
            return True

    def shutdown(self) -> MonitorState:
        return self.stop()

    def _undo_lifecycle_unavailable(
        self,
        operation_id: str,
    ) -> UndoAvailability | None:
        if self.startup_status is not StartupStatus.READY:
            return UndoAvailability(
                operation_id,
                False,
                "Undo requires FilePilot startup and recovery checks to be ready.",
                unavailable_status=UndoStatus.NOT_ELIGIBLE,
                manual_review_required=self.startup_status is StartupStatus.BLOCKED,
            )
        monitor = self.monitor
        if (
            self.monitor_state is not MonitorState.STOPPED
            or (monitor is not None and getattr(monitor, "is_running", False))
        ):
            return UndoAvailability(
                operation_id,
                False,
                "Stop monitoring before evaluating or executing Undo.",
                unavailable_status=UndoStatus.NOT_ELIGIBLE,
            )
        return None

    def _configuration_changes_allowed(self) -> bool:
        monitor = self.monitor
        setup_in_progress = (
            self.startup_status is StartupStatus.SETUP_REQUIRED and monitor is None
        )
        return (
            (self.startup_status is StartupStatus.READY or setup_in_progress)
            and self.monitor_state is MonitorState.STOPPED
            and (monitor is not None or setup_in_progress)
            and not getattr(monitor, "is_running", False)
        )

    def _recovery_actions_available(self) -> bool:
        monitor = self.monitor
        return (
            self.monitor_state in {MonitorState.STOPPED, MonitorState.BLOCKED}
            and not (monitor is not None and getattr(monitor, "is_running", False))
        )

    def _recovery_hash_db_file(self) -> str | None:
        value = (self.config or {}).get("hash_db_file")
        if not isinstance(value, str) or not value:
            return None
        path = Path(value)
        if path.is_absolute():
            return str(path)
        from app.config_loader import resolve_runtime_path

        return str(resolve_runtime_path(value))

    def _recovery_refresh_required(self) -> bool | None:
        try:
            journal = OperationJournal.open_existing_read_only(self._journal_path)
            return bool(journal.read_incomplete_operations(limit=1))
        except JournalNotFoundError:
            return False if self._journal_path is None else None
        except JournalError as error:
            logger.warning("Recovery preflight failed before monitoring start: %s", error)
            return None

    def _inspect_configuration(self) -> StartupResult:
        if not self._config_path.is_file():
            return StartupResult(
                StartupStatus.SETUP_REQUIRED,
                error="FilePilot setup has not been completed",
            )
        try:
            with open(self._config_path, "r", encoding="utf-8") as file:
                config = json.load(file)
        except (OSError, json.JSONDecodeError) as error:
            return StartupResult(StartupStatus.ERROR, error=str(error))
        if not isinstance(config, dict):
            return StartupResult(
                StartupStatus.ERROR,
                error="Configuration root must be a JSON object",
            )
        if not self._configuration_is_complete(config):
            return StartupResult(StartupStatus.SETUP_REQUIRED, config=config)
        return StartupResult(StartupStatus.READY, config=config)

    @staticmethod
    def _configuration_is_complete(config: dict) -> bool:
        if config.get("first_run_completed") is False:
            return False
        organized = config.get("organized_base_folder")
        if not isinstance(organized, str) or not organized.strip():
            return False
        source = config.get("source_folder")
        folders = config.get("watch_folders")
        has_source = isinstance(source, str) and bool(source.strip())
        has_watch = isinstance(folders, list) and any(
            isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and bool(item["path"].strip())
            for item in folders
        )
        return has_watch if isinstance(folders, list) else has_source

    def _unavailable_start_state(self) -> MonitorState:
        startup = self.startup_status
        state = (
            MonitorState.BLOCKED
            if startup is StartupStatus.BLOCKED
            else MonitorState.ERROR
        )
        self._set_monitor_state(
            state,
            error=(self.startup_result.error if self.startup_result else None),
        )
        return state

    def _settle_folder_state(self) -> MonitorState:
        expected = self._active_folder_map(self.config or {})
        running = self._running_folder_map(self.monitor)
        failed = tuple(expected[key] for key in expected if key not in running)
        if failed or len(running) != len(expected):
            self._fail_closed_after_partial_start(
                "Folder lifecycle change left partial monitoring active",
                failed_folders=failed,
            )
            return MonitorState.ERROR
        if running:
            self._set_monitor_state(MonitorState.RUNNING, failed_folders=())
            return MonitorState.RUNNING
        self._set_monitor_state(MonitorState.STOPPED, failed_folders=())
        return MonitorState.STOPPED

    def _fail_closed_after_partial_start(
        self,
        reason: str,
        *,
        failed_folders: tuple[str, ...] = (),
    ) -> None:
        try:
            if self.monitor is not None:
                self.monitor.stop_all()
        except Exception as cleanup_error:
            reason = f"{reason}; partial startup cleanup failed: {cleanup_error}"
        self._set_monitor_state(
            MonitorState.ERROR,
            error=reason,
            failed_folders=failed_folders,
        )

    @staticmethod
    def _recovery_reasons(
        report: RecoveryReport,
        blocking_operation_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        blocking = set(blocking_operation_ids)
        return tuple(
            assessment.reason
            for assessment in report.assessments
            if assessment.operation.operation_id in blocking
        )

    def _bind_activity_callback(self, monitor) -> None:
        setter = getattr(monitor, "set_activity_callback", None)
        if callable(setter):
            setter(self._on_backend_activity)
            return
        legacy_setter = getattr(monitor, "set_file_processed_callback", None)
        if callable(legacy_setter):
            legacy_setter(self._on_legacy_activity)

    def _on_backend_activity(
        self,
        source: str | Path,
        category: str,
        status: str,
        move_result: MoveResult | None,
        processing_error: str | None,
    ) -> None:
        self._publish_activity(ActivityEvent(
            source=Path(source),
            category=category,
            status=status,
            move_result=move_result,
            processing_error=processing_error,
            occurred_at_utc=datetime.now(timezone.utc),
        ))

    def _on_legacy_activity(self, filename: str, category: str, status: str) -> None:
        self._publish_activity(ActivityEvent(
            source=Path(filename),
            category=category,
            status=status,
            occurred_at_utc=datetime.now(timezone.utc),
        ))

    def _publish_activity(self, event: ActivityEvent) -> None:
        with self._subscriber_lock:
            subscribers = tuple(self._activity_subscribers)
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                logger.error("Activity subscriber failed", exc_info=True)

    def _set_startup_result(self, result: StartupResult) -> None:
        with self._state_lock:
            self._startup_result = result
            self._config = result.config
            if result.status is not StartupStatus.READY:
                self._monitor = None

    def _set_monitor_state(
        self,
        state: MonitorState,
        *,
        error: str | None = None,
        failed_folders: tuple[str, ...] | None = None,
    ) -> None:
        with self._state_lock:
            self._monitor_state = state
            self._last_error = error
            if failed_folders is not None:
                self._failed_folders = failed_folders
        with self._subscriber_lock:
            subscribers = tuple(self._state_subscribers)
        for subscriber in subscribers:
            try:
                subscriber(state)
            except Exception:
                logger.error("State subscriber failed", exc_info=True)

    @staticmethod
    def _active_folder_map(config: dict) -> dict[str, str]:
        folders = config.get("watch_folders")
        if not isinstance(folders, list):
            source = config.get("source_folder")
            folders = [{"path": source, "active": True}] if source else []
        result = {}
        for item in folders:
            if not isinstance(item, dict) or not item.get("active", True):
                continue
            path = item.get("path")
            if isinstance(path, str) and path.strip():
                resolved = str(Path(path).resolve())
                result[os.path.normcase(resolved)] = resolved
        return result

    @staticmethod
    def _running_folder_map(monitor) -> dict[str, str]:
        result = {}
        for path in getattr(monitor, "running_folders", ()):
            resolved = str(Path(path).resolve())
            result[os.path.normcase(resolved)] = resolved
        return result
