from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot

from app.application_service import (
    ActivityEvent,
    ActivityRecord,
    FilePilotService,
    MonitorState,
    ProductConfigurationCandidate,
    ProductConfigurationSnapshot,
    ProductDataState,
    ProductRule,
    ProductMetrics,
    ProductSnapshot,
    RecoveryAction,
    RecoverySnapshot,
    SafetyDataState,
    StartupResult,
    StartupStatus,
    product_record_from_activity_event,
)
from app.product_settings import (
    ProductSettingsCandidate,
    ProductSettingsSnapshot,
)
from app.product_identity import PRODUCT_IDENTITY


@dataclass(frozen=True)
class ServiceSnapshot:
    startup_status: StartupStatus | None
    monitor_state: MonitorState
    error: str | None = None
    blocking_operation_ids: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    failed_folders: tuple[str, ...] = ()


class _ServiceWorker(QObject):
    bootstrap_finished = Signal(object, int)
    command_finished = Signal(str, object, int)
    command_failed = Signal(str, str, int)
    shutdown_finished = Signal(int)
    product_read_finished = Signal(int, object, int, int)
    product_read_failed = Signal(int, str, int, int)
    search_finished = Signal(int, str, object, int)
    semantic_search_finished = Signal(int, str, object, int)
    search_refresh_finished = Signal(int, object, int)
    search_failed = Signal(str, int, str, int)
    preview_finished = Signal(int, object, int)
    organize_finished = Signal(int, object, int)
    undo_availability_finished = Signal(int, object, int)
    undo_finished = Signal(int, object, int)
    recovery_finished = Signal(int, object, int)
    recovery_action_finished = Signal(int, object, int)
    safety_failed = Signal(str, int, str, int)
    configuration_read_finished = Signal(int, object, int)
    configuration_validation_finished = Signal(int, str, object, int)
    candidate_classification_finished = Signal(int, str, object, int)
    configuration_save_finished = Signal(int, str, object, int)
    configuration_failed = Signal(str, int, str, str, int)
    settings_read_finished = Signal(int, object, int)
    settings_validation_finished = Signal(int, str, object, int)
    settings_save_finished = Signal(int, str, object, int)
    settings_failed = Signal(str, int, str, str, int)

    def __init__(self, service: FilePilotService) -> None:
        super().__init__()
        self._service = service

    @Slot()
    def bootstrap(self) -> None:
        try:
            result = self._service.bootstrap()
            self.bootstrap_finished.emit(result, threading.get_ident())
        except Exception as error:
            self.command_failed.emit("bootstrap", str(error), threading.get_ident())

    @Slot()
    def start(self) -> None:
        self._run_command("start", self._service.start)

    @Slot()
    def stop(self) -> None:
        self._run_command("stop", self._service.stop)

    @Slot()
    def shutdown(self) -> None:
        try:
            state = self._service.shutdown()
            if state not in {MonitorState.STOPPED, MonitorState.BLOCKED}:
                self.command_failed.emit(
                    "shutdown",
                    self._service.last_error
                    or "Monitoring did not reach a safe stopped state",
                    threading.get_ident(),
                )
                return
        except Exception as error:
            self.command_failed.emit("shutdown", str(error), threading.get_ident())
            return
        self.shutdown_finished.emit(threading.get_ident())

    @Slot(int, int, int)
    def read_product_data(
        self,
        request_id: int,
        limit: int,
        activity_revision: int,
    ) -> None:
        try:
            snapshot = self._service.get_product_snapshot(limit)
        except Exception as error:
            self.product_read_failed.emit(
                request_id,
                str(error),
                activity_revision,
                threading.get_ident(),
            )
            return
        self.product_read_finished.emit(
            request_id,
            snapshot,
            activity_revision,
            threading.get_ident(),
        )

    @Slot(int, str, int)
    def search_files(self, request_id: int, query: str, limit: int) -> None:
        try:
            result = self._service.search_files(query, limit=limit)
        except Exception as error:
            self.search_failed.emit(
                "search",
                request_id,
                str(error),
                threading.get_ident(),
            )
            return
        self.search_finished.emit(request_id, query, result, threading.get_ident())

    @Slot(int, str, int)
    def semantic_search_files(self, request_id: int, query: str, limit: int) -> None:
        try:
            result = self._service.semantic_search_files(query, limit=limit)
        except Exception as error:
            self.search_failed.emit(
                "semantic_search",
                request_id,
                str(error),
                threading.get_ident(),
            )
            return
        self.semantic_search_finished.emit(
            request_id,
            query,
            result,
            threading.get_ident(),
        )

    @Slot(int)
    def refresh_search_index(self, request_id: int) -> None:
        try:
            result = self._service.refresh_search_index()
        except Exception as error:
            self.search_failed.emit(
                "search_refresh",
                request_id,
                str(error),
                threading.get_ident(),
            )
            return
        self.search_refresh_finished.emit(
            request_id,
            result,
            threading.get_ident(),
        )

    @Slot(int, str)
    def preview_file(self, request_id: int, source: str) -> None:
        self._run_safety_request(
            "preview",
            request_id,
            lambda: self._service.preview_file(source),
            self.preview_finished,
        )

    @Slot(int, str)
    def organize_file(self, request_id: int, source: str) -> None:
        self._run_safety_request(
            "organize",
            request_id,
            lambda: self._service.organize_file(source),
            self.organize_finished,
        )

    @Slot(int, str)
    def evaluate_undo(self, request_id: int, operation_id: str) -> None:
        self._run_safety_request(
            "undo_availability",
            request_id,
            lambda: self._service.get_undo_availability(operation_id),
            self.undo_availability_finished,
        )

    @Slot(int, str)
    def undo(self, request_id: int, operation_id: str) -> None:
        self._run_safety_request(
            "undo",
            request_id,
            lambda: self._service.undo_operation(operation_id),
            self.undo_finished,
        )

    @Slot(int, int, int)
    def read_recovery(self, request_id: int, limit: int, offset: int) -> None:
        self._run_safety_request(
            "recovery",
            request_id,
            lambda: self._service.get_recovery_snapshot(limit, offset),
            self.recovery_finished,
        )

    @Slot(int, str, object)
    def apply_recovery_action(
        self,
        request_id: int,
        operation_id: str,
        action: RecoveryAction,
    ) -> None:
        self._run_safety_request(
            "recovery_action",
            request_id,
            lambda: self._service.reconcile_recovery_item(operation_id, action),
            self.recovery_action_finished,
        )

    @Slot(int)
    def read_configuration(self, request_id: int) -> None:
        self._run_configuration_request(
            "configuration_read",
            request_id,
            "",
            lambda: self._service.get_product_configuration(),
            self.configuration_read_finished,
        )

    @Slot(int, str, object)
    def validate_configuration(
        self,
        request_id: int,
        context: str,
        candidate: ProductConfigurationCandidate,
    ) -> None:
        self._run_configuration_request(
            "configuration_validation",
            request_id,
            context,
            lambda: self._service.validate_product_configuration(candidate),
            self.configuration_validation_finished,
        )

    @Slot(int, str, object, str)
    def preview_candidate_classification(
        self,
        request_id: int,
        context: str,
        rules: tuple[ProductRule, ...],
        filename: str,
    ) -> None:
        self._run_configuration_request(
            "candidate_classification",
            request_id,
            context,
            lambda: self._service.preview_candidate_classification(rules, filename),
            self.candidate_classification_finished,
        )

    @Slot(int, str, object, str)
    def save_configuration(
        self,
        request_id: int,
        context: str,
        candidate: ProductConfigurationCandidate,
        expected_revision: str,
    ) -> None:
        self._run_configuration_request(
            "configuration_save",
            request_id,
            context,
            lambda: self._service.save_product_configuration(
                candidate,
                expected_revision,
            ),
            self.configuration_save_finished,
        )

    @Slot(int)
    def read_settings(self, request_id: int) -> None:
        self._run_settings_request(
            "settings_read",
            request_id,
            "",
            lambda: self._service.get_product_settings(),
            self.settings_read_finished,
        )

    @Slot(int, str, object)
    def validate_settings(
        self,
        request_id: int,
        context: str,
        candidate: ProductSettingsCandidate,
    ) -> None:
        self._run_settings_request(
            "settings_validation",
            request_id,
            context,
            lambda: self._service.validate_product_settings(candidate),
            self.settings_validation_finished,
        )

    @Slot(int, str, object, str)
    def save_settings(
        self,
        request_id: int,
        context: str,
        candidate: ProductSettingsCandidate,
        expected_revision: str,
    ) -> None:
        self._run_settings_request(
            "settings_save",
            request_id,
            context,
            lambda: self._service.save_product_settings(candidate, expected_revision),
            self.settings_save_finished,
        )

    def _run_settings_request(
        self,
        name: str,
        request_id: int,
        context: str,
        operation,
        completed_signal,
    ) -> None:
        try:
            result = operation()
        except Exception:
            self.settings_failed.emit(
                name,
                request_id,
                context,
                "FilePilot could not complete the Settings request.",
                threading.get_ident(),
            )
            return
        if context:
            completed_signal.emit(
                request_id,
                context,
                result,
                threading.get_ident(),
            )
        else:
            completed_signal.emit(request_id, result, threading.get_ident())

    def _run_configuration_request(
        self,
        name: str,
        request_id: int,
        context: str,
        operation,
        completed_signal,
    ) -> None:
        try:
            result = operation()
        except Exception:
            self.configuration_failed.emit(
                name,
                request_id,
                context,
                "FilePilot could not complete the configuration request.",
                threading.get_ident(),
            )
            return
        if context:
            completed_signal.emit(
                request_id,
                context,
                result,
                threading.get_ident(),
            )
        else:
            completed_signal.emit(request_id, result, threading.get_ident())

    def _run_safety_request(
        self,
        name: str,
        request_id: int,
        operation,
        completed_signal,
    ) -> None:
        try:
            result = operation()
        except Exception:
            self.safety_failed.emit(
                name,
                request_id,
                "FilePilot could not complete the safety request.",
                threading.get_ident(),
            )
            return
        completed_signal.emit(request_id, result, threading.get_ident())

    def _run_command(self, name: str, command) -> None:
        try:
            result = command()
            self.command_finished.emit(name, result, threading.get_ident())
        except Exception as error:
            self.command_failed.emit(name, str(error), threading.get_ident())


class QtServiceBridge(QObject):
    """Queued Qt adapter for FilePilotService lifecycle and events."""

    state_changed = Signal(object)
    startup_completed = Signal(object)
    activity_received = Signal(object)
    product_snapshot_changed = Signal(object)
    product_read_failed = Signal(str)
    search_results_changed = Signal(str, object)
    semantic_search_results_changed = Signal(str, object)
    search_refresh_completed = Signal(object)
    search_request_failed = Signal(str, str)
    preview_changed = Signal(object)
    organize_started = Signal(str)
    organize_completed = Signal(object)
    undo_availability_changed = Signal(object)
    undo_started = Signal(str)
    undo_completed = Signal(object)
    recovery_snapshot_changed = Signal(object)
    recovery_action_started = Signal(str)
    recovery_action_completed = Signal(object)
    safety_request_failed = Signal(str, str)
    configuration_snapshot_changed = Signal(object)
    configuration_validation_changed = Signal(str, object)
    candidate_classification_changed = Signal(str, object)
    configuration_save_started = Signal(str)
    configuration_save_completed = Signal(str, object)
    configuration_request_failed = Signal(str, str, str)
    settings_snapshot_changed = Signal(object)
    settings_validation_changed = Signal(str, object)
    settings_save_started = Signal(str)
    settings_save_completed = Signal(str, object)
    settings_request_failed = Signal(str, str, str)
    command_completed = Signal(str, object)
    command_failed = Signal(str, str)
    operation_thread_observed = Signal(str, int)
    closed = Signal()

    _bootstrap_worker = Signal()
    _start_worker = Signal()
    _stop_worker = Signal()
    _shutdown_worker = Signal()
    _product_read_worker = Signal(int, int, int)
    _search_worker = Signal(int, str, int)
    _semantic_search_worker = Signal(int, str, int)
    _search_refresh_worker = Signal(int)
    _preview_worker = Signal(int, str)
    _organize_worker = Signal(int, str)
    _undo_availability_worker = Signal(int, str)
    _undo_worker = Signal(int, str)
    _recovery_worker = Signal(int, int, int)
    _recovery_action_worker = Signal(int, str, object)
    _configuration_read_worker = Signal(int)
    _configuration_validation_worker = Signal(int, str, object)
    _candidate_classification_worker = Signal(int, str, object, str)
    _configuration_save_worker = Signal(int, str, object, str)
    _settings_read_worker = Signal(int)
    _settings_validation_worker = Signal(int, str, object)
    _settings_save_worker = Signal(int, str, object, str)
    _state_relay = Signal(object)
    _activity_relay = Signal(object)

    def __init__(
        self,
        service: FilePilotService | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service or FilePilotService()
        self._snapshot = self._snapshot_from_service()
        self._bootstrapped = False
        self._bootstrap_active = False
        self._command_active = False
        self._desired_running: bool | None = None
        self._inflight_running: bool | None = None
        self._closing = False
        self._closed = False
        self._product_snapshot = ProductSnapshot.loading()
        self._live_activity: dict[str, ActivityRecord] = {}
        self._live_sequence = 0
        self._activity_revision = 0
        self._product_limit = 100
        self._product_request_sequence = 0
        self._product_request_active = False
        self._active_product_request_id: int | None = None
        self._product_refresh_pending = False
        self._pending_product_limit = 100
        self._product_retry_revision: int | None = None
        self._search_sequence = 0
        self._active_search_id: int | None = None
        self._semantic_search_sequence = 0
        self._active_semantic_search_id: int | None = None
        self._search_refresh_sequence = 0
        self._search_refresh_active = False
        self._active_search_refresh_id: int | None = None
        self._preview_sequence = 0
        self._preview_active = False
        self._active_preview_id: int | None = None
        self._pending_preview_source: str | None = None
        self._organize_sequence = 0
        self._organize_active = False
        self._active_organize_id: int | None = None
        self._active_organize_source: str | None = None
        self._undo_availability_sequence = 0
        self._undo_availability_active = False
        self._active_undo_availability_id: int | None = None
        self._pending_undo_operation_id: str | None = None
        self._undo_sequence = 0
        self._undo_active = False
        self._active_undo_id: int | None = None
        self._active_undo_operation_id: str | None = None
        self._recovery_snapshot = RecoverySnapshot(
            SafetyDataState.LOADING,
            (),
            error="Loading recovery evidence...",
        )
        self._recovery_sequence = 0
        self._recovery_active = False
        self._active_recovery_id: int | None = None
        self._recovery_pending = False
        self._pending_recovery_limit = 100
        self._pending_recovery_offset = 0
        self._recovery_action_sequence = 0
        self._recovery_action_active = False
        self._active_recovery_action_id: int | None = None
        self._active_recovery_operation_id: str | None = None
        self._configuration_snapshot = ProductConfigurationSnapshot.loading()
        self._configuration_read_sequence = 0
        self._configuration_validation_sequence = 0
        self._configuration_validation_requests: dict[str, int] = {}
        self._candidate_classification_sequence = 0
        self._candidate_classification_requests: dict[str, int] = {}
        self._configuration_save_sequence = 0
        self._configuration_save_active = False
        self._active_configuration_save_id: int | None = None
        self._settings_snapshot = ProductSettingsSnapshot.loading()
        self._settings_read_sequence = 0
        self._settings_validation_sequence = 0
        self._settings_validation_requests: dict[str, int] = {}
        self._settings_save_sequence = 0
        self._active_settings_save_id: int | None = None
        identity_reader = getattr(self._service, "get_product_identity", None)
        self.product_identity = (
            identity_reader() if identity_reader is not None else PRODUCT_IDENTITY
        )

        self._unsubscribe_state = None
        self._unsubscribe_activity = None
        self._subscribe()

        self._thread = QThread(self)
        self._thread.setObjectName("FilePilotServiceThread")
        self._worker = _ServiceWorker(self._service)
        self._worker.moveToThread(self._thread)

        self._bootstrap_worker.connect(
            self._worker.bootstrap,
            Qt.ConnectionType.QueuedConnection,
        )
        self._start_worker.connect(
            self._worker.start,
            Qt.ConnectionType.QueuedConnection,
        )
        self._stop_worker.connect(
            self._worker.stop,
            Qt.ConnectionType.QueuedConnection,
        )
        self._shutdown_worker.connect(
            self._worker.shutdown,
            Qt.ConnectionType.QueuedConnection,
        )
        self._product_read_worker.connect(
            self._worker.read_product_data,
            Qt.ConnectionType.QueuedConnection,
        )
        self._search_worker.connect(
            self._worker.search_files,
            Qt.ConnectionType.QueuedConnection,
        )
        self._semantic_search_worker.connect(
            self._worker.semantic_search_files,
            Qt.ConnectionType.QueuedConnection,
        )
        self._search_refresh_worker.connect(
            self._worker.refresh_search_index,
            Qt.ConnectionType.QueuedConnection,
        )
        self._preview_worker.connect(
            self._worker.preview_file,
            Qt.ConnectionType.QueuedConnection,
        )
        self._organize_worker.connect(
            self._worker.organize_file,
            Qt.ConnectionType.QueuedConnection,
        )
        self._undo_availability_worker.connect(
            self._worker.evaluate_undo,
            Qt.ConnectionType.QueuedConnection,
        )
        self._undo_worker.connect(
            self._worker.undo,
            Qt.ConnectionType.QueuedConnection,
        )
        self._recovery_worker.connect(
            self._worker.read_recovery,
            Qt.ConnectionType.QueuedConnection,
        )
        self._recovery_action_worker.connect(
            self._worker.apply_recovery_action,
            Qt.ConnectionType.QueuedConnection,
        )
        self._configuration_read_worker.connect(
            self._worker.read_configuration,
            Qt.ConnectionType.QueuedConnection,
        )
        self._configuration_validation_worker.connect(
            self._worker.validate_configuration,
            Qt.ConnectionType.QueuedConnection,
        )
        self._candidate_classification_worker.connect(
            self._worker.preview_candidate_classification,
            Qt.ConnectionType.QueuedConnection,
        )
        self._configuration_save_worker.connect(
            self._worker.save_configuration,
            Qt.ConnectionType.QueuedConnection,
        )
        self._settings_read_worker.connect(
            self._worker.read_settings,
            Qt.ConnectionType.QueuedConnection,
        )
        self._settings_validation_worker.connect(
            self._worker.validate_settings,
            Qt.ConnectionType.QueuedConnection,
        )
        self._settings_save_worker.connect(
            self._worker.save_settings,
            Qt.ConnectionType.QueuedConnection,
        )
        self._state_relay.connect(
            self._accept_service_state,
            Qt.ConnectionType.QueuedConnection,
        )
        self._activity_relay.connect(
            self._accept_service_activity,
            Qt.ConnectionType.QueuedConnection,
        )

        self._worker.bootstrap_finished.connect(self._on_bootstrap_finished)
        self._worker.command_finished.connect(self._on_command_finished)
        self._worker.command_failed.connect(self._on_command_failed)
        self._worker.shutdown_finished.connect(self._on_worker_shutdown)
        self._worker.product_read_finished.connect(self._on_product_read_finished)
        self._worker.product_read_failed.connect(self._on_product_read_failed)
        self._worker.search_finished.connect(self._on_search_finished)
        self._worker.semantic_search_finished.connect(
            self._on_semantic_search_finished
        )
        self._worker.search_refresh_finished.connect(
            self._on_search_refresh_finished
        )
        self._worker.search_failed.connect(self._on_search_failed)
        self._worker.preview_finished.connect(self._on_preview_finished)
        self._worker.organize_finished.connect(self._on_organize_finished)
        self._worker.undo_availability_finished.connect(
            self._on_undo_availability_finished
        )
        self._worker.undo_finished.connect(self._on_undo_finished)
        self._worker.recovery_finished.connect(self._on_recovery_finished)
        self._worker.recovery_action_finished.connect(
            self._on_recovery_action_finished
        )
        self._worker.safety_failed.connect(self._on_safety_failed)
        self._worker.configuration_read_finished.connect(
            self._on_configuration_read_finished
        )
        self._worker.configuration_validation_finished.connect(
            self._on_configuration_validation_finished
        )
        self._worker.candidate_classification_finished.connect(
            self._on_candidate_classification_finished
        )
        self._worker.configuration_save_finished.connect(
            self._on_configuration_save_finished
        )
        self._worker.configuration_failed.connect(self._on_configuration_failed)
        self._worker.settings_read_finished.connect(self._on_settings_read_finished)
        self._worker.settings_validation_finished.connect(
            self._on_settings_validation_finished
        )
        self._worker.settings_save_finished.connect(self._on_settings_save_finished)
        self._worker.settings_failed.connect(self._on_settings_failed)
        self._worker.shutdown_finished.connect(
            self._worker.deleteLater,
            Qt.ConnectionType.DirectConnection,
        )
        self._worker.shutdown_finished.connect(
            self._thread.quit,
            Qt.ConnectionType.DirectConnection,
        )
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    @property
    def snapshot(self) -> ServiceSnapshot:
        return self._snapshot

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def product_snapshot(self) -> ProductSnapshot:
        return self._merged_product_snapshot()

    @property
    def recovery_snapshot(self) -> RecoverySnapshot:
        return self._recovery_snapshot

    @property
    def configuration_snapshot(self) -> ProductConfigurationSnapshot:
        return self._configuration_snapshot

    @property
    def settings_snapshot(self) -> ProductSettingsSnapshot:
        return self._settings_snapshot

    @Slot()
    def bootstrap(self) -> None:
        if self._closing or self._bootstrapped or self._bootstrap_active:
            return
        self._bootstrap_active = True
        self._bootstrap_worker.emit()

    @Slot()
    def request_start(self) -> None:
        if not self._can_accept_lifecycle_request():
            return
        self._desired_running = True
        self._dispatch_lifecycle_command()

    @Slot()
    def request_stop(self) -> None:
        if not self._can_accept_lifecycle_request():
            return
        self._desired_running = False
        self._dispatch_lifecycle_command()

    @Slot()
    def request_product_refresh(self, limit: int = 100) -> None:
        if self._closing:
            return
        bounded_limit = max(1, min(int(limit), 100))
        self._pending_product_limit = max(
            bounded_limit,
            self._pending_product_limit if self._product_refresh_pending else 0,
        )
        if self._product_request_active:
            self._product_refresh_pending = True
            return
        self._dispatch_product_read(self._pending_product_limit)

    @Slot(str, int)
    def request_search(self, query: str, limit: int = 25) -> None:
        if self._closing:
            return
        self._search_sequence += 1
        self._active_search_id = self._search_sequence
        bounded_limit = max(1, min(int(limit), 100))
        self._search_worker.emit(self._search_sequence, query, bounded_limit)

    @Slot(str, int)
    def request_semantic_search(self, query: str, limit: int = 25) -> None:
        if self._closing:
            return
        self._semantic_search_sequence += 1
        self._active_semantic_search_id = self._semantic_search_sequence
        bounded_limit = max(1, min(int(limit), 100))
        self._semantic_search_worker.emit(
            self._semantic_search_sequence,
            query,
            bounded_limit,
        )

    @Slot()
    def request_search_refresh(self) -> None:
        if self._closing or self._search_refresh_active:
            return
        self._search_refresh_sequence += 1
        self._active_search_refresh_id = self._search_refresh_sequence
        self._search_refresh_active = True
        self._search_refresh_worker.emit(self._search_refresh_sequence)

    @Slot(str)
    def request_preview(self, source: str) -> None:
        if self._closing:
            return
        self._pending_preview_source = source
        if self._preview_active:
            return
        self._dispatch_preview()

    @Slot(str)
    def request_organize(self, source: str) -> None:
        if self._closing or self._organize_active or not source:
            return
        self._organize_sequence += 1
        self._active_organize_id = self._organize_sequence
        self._active_organize_source = source
        self._organize_active = True
        self.organize_started.emit(source)
        self._organize_worker.emit(self._organize_sequence, source)

    @Slot(str)
    def request_undo_availability(self, operation_id: str) -> None:
        if self._closing:
            return
        self._pending_undo_operation_id = operation_id
        if self._undo_availability_active:
            return
        self._dispatch_undo_availability()

    @Slot(str)
    def request_undo(self, operation_id: str) -> None:
        if self._closing or self._undo_active or not operation_id:
            return
        self._undo_sequence += 1
        self._active_undo_id = self._undo_sequence
        self._active_undo_operation_id = operation_id
        self._undo_active = True
        self.undo_started.emit(operation_id)
        self._undo_worker.emit(self._undo_sequence, operation_id)

    @Slot(int, int)
    def request_recovery_refresh(self, limit: int = 100, offset: int = 0) -> None:
        if self._closing:
            return
        self._pending_recovery_limit = max(1, min(int(limit), 100))
        self._pending_recovery_offset = max(0, int(offset))
        if self._recovery_active:
            self._recovery_pending = True
            return
        self._dispatch_recovery_read()

    @Slot(str, object)
    def request_recovery_action(
        self,
        operation_id: str,
        action: RecoveryAction,
    ) -> None:
        if (
            self._closing
            or self._recovery_action_active
            or not operation_id
        ):
            return
        self._recovery_action_sequence += 1
        self._active_recovery_action_id = self._recovery_action_sequence
        self._active_recovery_operation_id = operation_id
        self._recovery_action_active = True
        self.recovery_action_started.emit(operation_id)
        self._recovery_action_worker.emit(
            self._recovery_action_sequence,
            operation_id,
            action,
        )

    @Slot()
    def request_configuration_refresh(self) -> None:
        if self._closing:
            return
        self._configuration_read_sequence += 1
        self._configuration_read_worker.emit(self._configuration_read_sequence)

    @Slot(str, object)
    def request_configuration_validation(
        self,
        context: str,
        candidate: ProductConfigurationCandidate,
    ) -> None:
        if self._closing:
            return
        self._configuration_validation_sequence += 1
        page_context = context.split(":", 1)[0]
        self._configuration_validation_requests = {
            key: request_id
            for key, request_id in self._configuration_validation_requests.items()
            if key.split(":", 1)[0] != page_context
        }
        self._configuration_validation_requests[context] = (
            self._configuration_validation_sequence
        )
        self._configuration_validation_worker.emit(
            self._configuration_validation_sequence,
            context,
            candidate,
        )

    @Slot(str, object, str)
    def request_candidate_classification(
        self,
        context: str,
        rules: tuple[ProductRule, ...],
        filename: str,
    ) -> None:
        if self._closing:
            return
        self._candidate_classification_sequence += 1
        self._candidate_classification_requests[context] = (
            self._candidate_classification_sequence
        )
        self._candidate_classification_worker.emit(
            self._candidate_classification_sequence,
            context,
            rules,
            filename,
        )

    @Slot(str, object, str)
    def request_configuration_save(
        self,
        context: str,
        candidate: ProductConfigurationCandidate,
        expected_revision: str,
    ) -> None:
        if self._closing:
            return
        if self._configuration_save_active:
            self.configuration_request_failed.emit(
                "configuration_save",
                context,
                "Another configuration save is already in progress.",
            )
            return
        self._configuration_save_sequence += 1
        self._active_configuration_save_id = self._configuration_save_sequence
        self._configuration_save_active = True
        self.configuration_save_started.emit(context)
        self._configuration_save_worker.emit(
            self._configuration_save_sequence,
            context,
            candidate,
            expected_revision,
        )

    @Slot()
    def request_settings_refresh(self) -> None:
        if self._closing:
            return
        self._settings_read_sequence += 1
        self._settings_read_worker.emit(self._settings_read_sequence)

    @Slot(str, object)
    def request_settings_validation(
        self,
        context: str,
        candidate: ProductSettingsCandidate,
    ) -> None:
        if self._closing:
            return
        self._settings_validation_sequence += 1
        self._settings_validation_requests = {
            key: request_id
            for key, request_id in self._settings_validation_requests.items()
            if key.split(":", 1)[0] != context.split(":", 1)[0]
        }
        self._settings_validation_requests[context] = self._settings_validation_sequence
        self._settings_validation_worker.emit(
            self._settings_validation_sequence,
            context,
            candidate,
        )

    @Slot(str, object, str)
    def request_settings_save(
        self,
        context: str,
        candidate: ProductSettingsCandidate,
        expected_revision: str,
    ) -> None:
        if self._closing:
            return
        if self._configuration_save_active:
            self.settings_request_failed.emit(
                "settings_save",
                context,
                "Another configuration save is already in progress.",
            )
            return
        self._settings_save_sequence += 1
        self._active_settings_save_id = self._settings_save_sequence
        self._configuration_save_active = True
        self.settings_save_started.emit(context)
        self._settings_save_worker.emit(
            self._settings_save_sequence,
            context,
            candidate,
            expected_revision,
        )

    @Slot()
    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._shutdown_worker.emit()

    def wait_for_shutdown(self, timeout_ms: int = 5000) -> bool:
        if not self._closing:
            self.shutdown()
        return self._thread.wait(timeout_ms)

    def _can_accept_lifecycle_request(self) -> bool:
        return (
            not self._closing
            and self._bootstrapped
            and self._snapshot.startup_status is StartupStatus.READY
        )

    def _dispatch_lifecycle_command(self) -> None:
        if self._command_active or self._desired_running is None:
            return
        self._command_active = True
        self._inflight_running = self._desired_running
        if self._inflight_running:
            self._start_worker.emit()
        else:
            self._stop_worker.emit()

    def _dispatch_product_read(self, limit: int) -> None:
        self._product_refresh_pending = False
        self._pending_product_limit = limit
        self._product_request_sequence += 1
        self._active_product_request_id = self._product_request_sequence
        self._product_request_active = True
        self._product_limit = limit
        self._product_read_worker.emit(
            self._active_product_request_id,
            limit,
            self._activity_revision,
        )

    def _dispatch_preview(self) -> None:
        source = self._pending_preview_source
        if source is None:
            return
        self._pending_preview_source = None
        self._preview_sequence += 1
        self._active_preview_id = self._preview_sequence
        self._preview_active = True
        self._preview_worker.emit(self._preview_sequence, source)

    def _dispatch_undo_availability(self) -> None:
        operation_id = self._pending_undo_operation_id
        if operation_id is None:
            return
        self._pending_undo_operation_id = None
        self._undo_availability_sequence += 1
        self._active_undo_availability_id = self._undo_availability_sequence
        self._undo_availability_active = True
        self._undo_availability_worker.emit(
            self._undo_availability_sequence,
            operation_id,
        )

    def _dispatch_recovery_read(self) -> None:
        self._recovery_pending = False
        self._recovery_sequence += 1
        self._active_recovery_id = self._recovery_sequence
        self._recovery_active = True
        self._recovery_worker.emit(
            self._recovery_sequence,
            self._pending_recovery_limit,
            self._pending_recovery_offset,
        )

    def _on_service_state(self, state: MonitorState) -> None:
        if self._closing:
            return
        self._state_relay.emit(self._snapshot_from_service(state))

    def _on_service_activity(self, event: ActivityEvent) -> None:
        if not self._closed:
            self._activity_relay.emit(event)

    @Slot(object)
    def _accept_service_state(self, snapshot: ServiceSnapshot) -> None:
        if self._closing:
            return
        self._publish_snapshot(snapshot)

    @Slot(object)
    def _accept_service_activity(self, event: ActivityEvent) -> None:
        self._activity_revision += 1
        self._live_sequence += 1
        record_id = event.operation_id or f"live:{self._live_sequence}"
        self._live_activity[record_id] = product_record_from_activity_event(
            event,
            record_id,
        )
        while len(self._live_activity) > 100:
            self._live_activity.pop(next(iter(self._live_activity)))
        if self._closing:
            return
        self.activity_received.emit(event)
        self.product_snapshot_changed.emit(self._merged_product_snapshot())
        self.request_product_refresh(self._product_limit)

    @Slot(object, int)
    def _on_bootstrap_finished(
        self,
        result: StartupResult,
        worker_thread_id: int,
    ) -> None:
        if self._closing:
            return
        self._bootstrap_active = False
        self._bootstrapped = True
        self.operation_thread_observed.emit("bootstrap", worker_thread_id)
        self.startup_completed.emit(result)
        self._publish_snapshot(self._snapshot_from_service())
        self.request_product_refresh(self._product_limit)
        self.request_recovery_refresh(self._pending_recovery_limit, 0)
        self.request_configuration_refresh()
        self.request_settings_refresh()

    @Slot(int, object, int, int)
    def _on_product_read_finished(
        self,
        request_id: int,
        snapshot: ProductSnapshot,
        activity_revision: int,
        worker_thread_id: int,
    ) -> None:
        if request_id != self._active_product_request_id:
            return
        self._product_request_active = False
        self._active_product_request_id = None
        if self._closing:
            return
        self.operation_thread_observed.emit("product_read", worker_thread_id)
        if (
            activity_revision == self._activity_revision
            and not self._product_refresh_pending
        ):
            self._product_snapshot = self._converge_product_snapshot(snapshot)
            self.product_snapshot_changed.emit(self._merged_product_snapshot())
            if snapshot.state is ProductDataState.AVAILABLE:
                self._product_retry_revision = None
            else:
                self._schedule_live_confirmation_retry(activity_revision)
        else:
            self._product_refresh_pending = True
        self._dispatch_pending_product_read()

    @Slot(int, str, int, int)
    def _on_product_read_failed(
        self,
        request_id: int,
        message: str,
        activity_revision: int,
        worker_thread_id: int,
    ) -> None:
        if request_id != self._active_product_request_id:
            return
        self._product_request_active = False
        self._active_product_request_id = None
        if self._closing:
            return
        self.operation_thread_observed.emit("product_read", worker_thread_id)
        if (
            activity_revision == self._activity_revision
            and not self._product_refresh_pending
        ):
            self._product_snapshot = ProductSnapshot(
                ProductDataState.ERROR,
                ProductMetrics.unavailable(),
                error=message,
            )
            self.product_read_failed.emit(message)
            self.product_snapshot_changed.emit(self._merged_product_snapshot())
            self._schedule_live_confirmation_retry(activity_revision)
        else:
            self._product_refresh_pending = True
        self._dispatch_pending_product_read()

    @Slot(int, str, object, int)
    def _on_search_finished(
        self,
        request_id: int,
        query: str,
        results: object,
        worker_thread_id: int,
    ) -> None:
        if request_id != self._active_search_id or self._closing:
            return
        self._active_search_id = None
        self.operation_thread_observed.emit("search", worker_thread_id)
        self.search_results_changed.emit(query, results)

    @Slot(int, str, object, int)
    def _on_semantic_search_finished(
        self,
        request_id: int,
        query: str,
        results: object,
        worker_thread_id: int,
    ) -> None:
        if request_id != self._active_semantic_search_id or self._closing:
            return
        self._active_semantic_search_id = None
        self.operation_thread_observed.emit("semantic_search", worker_thread_id)
        self.semantic_search_results_changed.emit(query, results)

    @Slot(int, object, int)
    def _on_search_refresh_finished(
        self,
        request_id: int,
        result: object,
        worker_thread_id: int,
    ) -> None:
        if request_id != self._active_search_refresh_id:
            return
        self._search_refresh_active = False
        self._active_search_refresh_id = None
        if self._closing:
            return
        self.operation_thread_observed.emit("search_refresh", worker_thread_id)
        self.search_refresh_completed.emit(result)

    @Slot(str, int, str, int)
    def _on_search_failed(
        self,
        name: str,
        request_id: int,
        message: str,
        worker_thread_id: int,
    ) -> None:
        accepted = (
            name == "search" and request_id == self._active_search_id
        ) or (
            name == "semantic_search"
            and request_id == self._active_semantic_search_id
        ) or (
            name == "search_refresh"
            and request_id == self._active_search_refresh_id
        )
        if not accepted:
            return
        if name == "search":
            self._active_search_id = None
        elif name == "semantic_search":
            self._active_semantic_search_id = None
        else:
            self._search_refresh_active = False
            self._active_search_refresh_id = None
        if self._closing:
            return
        self.operation_thread_observed.emit(name, worker_thread_id)
        self.search_request_failed.emit(name, message)

    @Slot(int, object, int)
    def _on_preview_finished(
        self,
        request_id: int,
        preview: object,
        worker_thread_id: int,
    ) -> None:
        if request_id != self._active_preview_id:
            return
        self._preview_active = False
        self._active_preview_id = None
        if self._closing:
            return
        self.operation_thread_observed.emit("preview", worker_thread_id)
        if self._pending_preview_source is None:
            self.preview_changed.emit(preview)
        else:
            self._dispatch_preview()

    @Slot(int, object, int)
    def _on_organize_finished(
        self,
        request_id: int,
        result: object,
        worker_thread_id: int,
    ) -> None:
        if request_id != self._active_organize_id:
            return
        self._organize_active = False
        self._active_organize_id = None
        self._active_organize_source = None
        if self._closing:
            return
        self.operation_thread_observed.emit("organize", worker_thread_id)
        self.organize_completed.emit(result)
        self.request_product_refresh(self._product_limit)
        self.request_recovery_refresh(self._pending_recovery_limit, 0)

    @Slot(int, object, int)
    def _on_undo_availability_finished(
        self,
        request_id: int,
        availability: object,
        worker_thread_id: int,
    ) -> None:
        if request_id != self._active_undo_availability_id:
            return
        self._undo_availability_active = False
        self._active_undo_availability_id = None
        if self._closing:
            return
        self.operation_thread_observed.emit(
            "undo_availability",
            worker_thread_id,
        )
        if self._pending_undo_operation_id is None:
            self.undo_availability_changed.emit(availability)
        else:
            self._dispatch_undo_availability()

    @Slot(int, object, int)
    def _on_undo_finished(
        self,
        request_id: int,
        result: object,
        worker_thread_id: int,
    ) -> None:
        if request_id != self._active_undo_id:
            return
        self._undo_active = False
        self._active_undo_id = None
        operation_id = self._active_undo_operation_id
        self._active_undo_operation_id = None
        if self._closing:
            return
        self.operation_thread_observed.emit("undo", worker_thread_id)
        self.undo_completed.emit(result)
        if operation_id:
            self.request_undo_availability(operation_id)
        self.request_product_refresh(self._product_limit)
        self.request_recovery_refresh(self._pending_recovery_limit, 0)

    @Slot(int, object, int)
    def _on_recovery_finished(
        self,
        request_id: int,
        snapshot: RecoverySnapshot,
        worker_thread_id: int,
    ) -> None:
        if request_id != self._active_recovery_id:
            return
        self._recovery_active = False
        self._active_recovery_id = None
        if self._closing:
            return
        self.operation_thread_observed.emit("recovery", worker_thread_id)
        if self._recovery_pending:
            self._dispatch_recovery_read()
            return
        self._recovery_snapshot = snapshot
        self.recovery_snapshot_changed.emit(snapshot)

    @Slot(int, object, int)
    def _on_recovery_action_finished(
        self,
        request_id: int,
        result: object,
        worker_thread_id: int,
    ) -> None:
        if request_id != self._active_recovery_action_id:
            return
        self._recovery_action_active = False
        self._active_recovery_action_id = None
        self._active_recovery_operation_id = None
        if self._closing:
            return
        self.operation_thread_observed.emit("recovery_action", worker_thread_id)
        self.recovery_action_completed.emit(result)
        self.request_recovery_refresh(self._pending_recovery_limit, 0)
        self.request_product_refresh(self._product_limit)

    @Slot(int, object, int)
    def _on_configuration_read_finished(
        self,
        request_id: int,
        snapshot: ProductConfigurationSnapshot,
        worker_thread_id: int,
    ) -> None:
        if request_id != self._configuration_read_sequence or self._closing:
            return
        self.operation_thread_observed.emit("configuration_read", worker_thread_id)
        self._configuration_snapshot = snapshot
        self.configuration_snapshot_changed.emit(snapshot)

    @Slot(int, str, object, int)
    def _on_configuration_validation_finished(
        self,
        request_id: int,
        context: str,
        result: object,
        worker_thread_id: int,
    ) -> None:
        if (
            request_id != self._configuration_validation_requests.get(context)
            or self._closing
        ):
            return
        self._configuration_validation_requests.pop(context, None)
        self.operation_thread_observed.emit(
            "configuration_validation",
            worker_thread_id,
        )
        self.configuration_validation_changed.emit(context, result)

    @Slot(int, str, object, int)
    def _on_candidate_classification_finished(
        self,
        request_id: int,
        context: str,
        result: object,
        worker_thread_id: int,
    ) -> None:
        if (
            request_id != self._candidate_classification_requests.get(context)
            or self._closing
        ):
            return
        self.operation_thread_observed.emit(
            "candidate_classification",
            worker_thread_id,
        )
        self.candidate_classification_changed.emit(context, result)

    @Slot(int, str, object, int)
    def _on_configuration_save_finished(
        self,
        request_id: int,
        context: str,
        result: object,
        worker_thread_id: int,
    ) -> None:
        if request_id != self._active_configuration_save_id:
            return
        self._configuration_save_active = False
        self._active_configuration_save_id = None
        if self._closing:
            return
        self.operation_thread_observed.emit("configuration_save", worker_thread_id)
        snapshot = getattr(result, "snapshot", None)
        if snapshot is not None:
            self._configuration_snapshot = snapshot
            self.configuration_snapshot_changed.emit(snapshot)
        self.configuration_save_completed.emit(context, result)
        self.request_settings_refresh()

    @Slot(int, object, int)
    def _on_settings_read_finished(
        self,
        request_id: int,
        snapshot: ProductSettingsSnapshot,
        worker_thread_id: int,
    ) -> None:
        if request_id != self._settings_read_sequence or self._closing:
            return
        self.operation_thread_observed.emit("settings_read", worker_thread_id)
        self._settings_snapshot = snapshot
        self.settings_snapshot_changed.emit(snapshot)

    @Slot(int, str, object, int)
    def _on_settings_validation_finished(
        self,
        request_id: int,
        context: str,
        result: object,
        worker_thread_id: int,
    ) -> None:
        if (
            request_id != self._settings_validation_requests.get(context)
            or self._closing
        ):
            return
        self._settings_validation_requests.pop(context, None)
        self.operation_thread_observed.emit("settings_validation", worker_thread_id)
        self.settings_validation_changed.emit(context, result)

    @Slot(int, str, object, int)
    def _on_settings_save_finished(
        self,
        request_id: int,
        context: str,
        result: object,
        worker_thread_id: int,
    ) -> None:
        if request_id != self._active_settings_save_id:
            return
        self._configuration_save_active = False
        self._active_settings_save_id = None
        if self._closing:
            return
        self.operation_thread_observed.emit("settings_save", worker_thread_id)
        snapshot = getattr(result, "snapshot", None)
        if snapshot is not None:
            self._settings_snapshot = snapshot
            self.settings_snapshot_changed.emit(snapshot)
        self.settings_save_completed.emit(context, result)
        self.request_configuration_refresh()

    @Slot(str, int, str, str, int)
    def _on_settings_failed(
        self,
        name: str,
        request_id: int,
        context: str,
        message: str,
        worker_thread_id: int,
    ) -> None:
        if name == "settings_read":
            accepted = request_id == self._settings_read_sequence
        elif name == "settings_validation":
            accepted = request_id == self._settings_validation_requests.get(context)
            if accepted:
                self._settings_validation_requests.pop(context, None)
        elif name == "settings_save":
            accepted = request_id == self._active_settings_save_id
            if accepted:
                self._configuration_save_active = False
                self._active_settings_save_id = None
        else:
            accepted = False
        if not accepted or self._closing:
            return
        self.operation_thread_observed.emit(name, worker_thread_id)
        self.settings_request_failed.emit(name, context, message)

    @Slot(str, int, str, str, int)
    def _on_configuration_failed(
        self,
        name: str,
        request_id: int,
        context: str,
        message: str,
        worker_thread_id: int,
    ) -> None:
        if name == "configuration_read":
            accepted = request_id == self._configuration_read_sequence
        elif name == "configuration_validation":
            accepted = request_id == self._configuration_validation_requests.get(context)
            if accepted:
                self._configuration_validation_requests.pop(context, None)
        elif name == "candidate_classification":
            accepted = request_id == self._candidate_classification_requests.get(context)
        elif name == "configuration_save":
            accepted = request_id == self._active_configuration_save_id
            if accepted:
                self._configuration_save_active = False
                self._active_configuration_save_id = None
        else:
            accepted = False
        if not accepted or self._closing:
            return
        self.operation_thread_observed.emit(name, worker_thread_id)
        self.configuration_request_failed.emit(name, context, message)

    @Slot(str, int, str, int)
    def _on_safety_failed(
        self,
        name: str,
        request_id: int,
        message: str,
        worker_thread_id: int,
    ) -> None:
        if not self._accept_safety_failure(name, request_id):
            return
        self.operation_thread_observed.emit(name, worker_thread_id)
        if self._closing:
            return
        self.safety_request_failed.emit(name, message)
        if name == "preview" and self._pending_preview_source is not None:
            self._dispatch_preview()
        elif (
            name == "undo_availability"
            and self._pending_undo_operation_id is not None
        ):
            self._dispatch_undo_availability()
        elif name == "recovery" and self._recovery_pending:
            self._dispatch_recovery_read()

    def _accept_safety_failure(self, name: str, request_id: int) -> bool:
        if name == "organize" and request_id == self._active_organize_id:
            self._organize_active = False
            self._active_organize_id = None
            self._active_organize_source = None
            return True
        if name == "preview" and request_id == self._active_preview_id:
            self._preview_active = False
            self._active_preview_id = None
            return True
        if (
            name == "undo_availability"
            and request_id == self._active_undo_availability_id
        ):
            self._undo_availability_active = False
            self._active_undo_availability_id = None
            return True
        if name == "undo" and request_id == self._active_undo_id:
            self._undo_active = False
            self._active_undo_id = None
            self._active_undo_operation_id = None
            return True
        if name == "recovery" and request_id == self._active_recovery_id:
            self._recovery_active = False
            self._active_recovery_id = None
            return True
        if (
            name == "recovery_action"
            and request_id == self._active_recovery_action_id
        ):
            self._recovery_action_active = False
            self._active_recovery_action_id = None
            self._active_recovery_operation_id = None
            return True
        return False

    def _schedule_live_confirmation_retry(self, activity_revision: int) -> None:
        if (
            not self._live_activity
            or self._product_retry_revision == activity_revision
        ):
            return
        self._product_retry_revision = activity_revision
        QTimer.singleShot(
            250,
            lambda revision=activity_revision: self._retry_live_confirmation(revision),
        )

    def _retry_live_confirmation(self, activity_revision: int) -> None:
        if (
            self._closing
            or activity_revision != self._activity_revision
            or self._product_retry_revision != activity_revision
        ):
            return
        self.request_product_refresh(self._product_limit)

    def _dispatch_pending_product_read(self) -> None:
        if self._product_refresh_pending and not self._closing:
            self._dispatch_product_read(self._pending_product_limit)

    def _converge_product_snapshot(
        self,
        snapshot: ProductSnapshot,
    ) -> ProductSnapshot:
        converged = []
        for durable in snapshot.activity:
            self._live_activity.pop(
                durable.operation_id or durable.record_id,
                None,
            )
            converged.append(durable)
        return replace(snapshot, activity=tuple(converged))

    def _merged_product_snapshot(self) -> ProductSnapshot:
        records = list(self._product_snapshot.activity)
        durable_ids = {
            record.operation_id or record.record_id for record in records
        }
        records.extend(
            record
            for key, record in self._live_activity.items()
            if key not in durable_ids
        )
        minimum = datetime.min.replace(tzinfo=timezone.utc)
        records.sort(
            key=lambda record: (
                record.occurred_at_utc or minimum,
                record.record_id,
            ),
            reverse=True,
        )
        return replace(
            self._product_snapshot,
            activity=tuple(records[: self._product_limit]),
        )

    @Slot(str, object, int)
    def _on_command_finished(
        self,
        command: str,
        result: object,
        worker_thread_id: int,
    ) -> None:
        desired_changed = self._desired_running != self._inflight_running
        self._command_active = False
        self._inflight_running = None
        if self._closing:
            return
        self.operation_thread_observed.emit(command, worker_thread_id)
        self.command_completed.emit(command, result)
        self._publish_snapshot(self._snapshot_from_service())
        self.request_configuration_refresh()
        if desired_changed:
            self._dispatch_lifecycle_command()
        else:
            self.request_recovery_refresh(self._pending_recovery_limit, 0)

    @Slot(str, str, int)
    def _on_command_failed(
        self,
        command: str,
        message: str,
        worker_thread_id: int,
    ) -> None:
        self.operation_thread_observed.emit(command, worker_thread_id)
        if command == "bootstrap":
            self._bootstrap_active = False
        if command == "shutdown":
            self._command_active = False
            self._inflight_running = None
            self._product_request_active = False
            self._active_product_request_id = None
            self._product_refresh_pending = False
            self._closing = False
            self._subscribe()
            self.command_failed.emit(command, message)
            failed = replace(
                self._snapshot_from_service(),
                monitor_state=MonitorState.ERROR,
                error=message,
            )
            self._publish_snapshot(failed)
            self.product_snapshot_changed.emit(self._merged_product_snapshot())
            self.request_product_refresh(self._product_limit)
            return
        self._command_active = False
        self._inflight_running = None
        if not self._closing:
            self.command_failed.emit(command, message)
            failed = self._snapshot_from_service()
            if command == "bootstrap":
                failed = replace(
                    failed,
                    startup_status=StartupStatus.ERROR,
                    monitor_state=MonitorState.ERROR,
                    error=message,
                )
            else:
                failed = replace(
                    failed,
                    monitor_state=MonitorState.ERROR,
                    error=message,
                )
            self._publish_snapshot(failed)

    @Slot(int)
    def _on_worker_shutdown(self, worker_thread_id: int) -> None:
        self._unsubscribe()
        self.operation_thread_observed.emit("shutdown", worker_thread_id)

    @Slot()
    def _on_thread_finished(self) -> None:
        self._closed = True
        self.closed.emit()

    def _publish_snapshot(self, snapshot: ServiceSnapshot) -> None:
        self._snapshot = snapshot
        self.state_changed.emit(snapshot)

    def _snapshot_from_service(
        self,
        monitor_state: MonitorState | None = None,
    ) -> ServiceSnapshot:
        startup = self._service.startup_result
        return ServiceSnapshot(
            startup_status=startup.status if startup is not None else None,
            monitor_state=monitor_state or self._service.monitor_state,
            error=self._service.last_error or (startup.error if startup else None),
            blocking_operation_ids=(
                startup.blocking_operation_ids if startup is not None else ()
            ),
            blocking_reasons=(
                startup.blocking_reasons if startup is not None else ()
            ),
            failed_folders=self._service.failed_folders,
        )

    def _unsubscribe(self) -> None:
        for name in ("_unsubscribe_state", "_unsubscribe_activity"):
            unsubscribe = getattr(self, name, None)
            if callable(unsubscribe):
                unsubscribe()
                setattr(self, name, None)

    def _subscribe(self) -> None:
        if self._unsubscribe_state is None:
            self._unsubscribe_state = self._service.subscribe_state(
                self._on_service_state
            )
        if self._unsubscribe_activity is None:
            self._unsubscribe_activity = self._service.subscribe_activity(
                self._on_service_activity
            )
