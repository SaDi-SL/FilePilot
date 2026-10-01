from __future__ import annotations

import threading
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from app.application_service import (
    ActivityEvent,
    FilePilotService,
    MonitorState,
    StartupResult,
    StartupStatus,
)


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
    command_completed = Signal(str, object)
    command_failed = Signal(str, str)
    operation_thread_observed = Signal(str, int)
    closed = Signal()

    _bootstrap_worker = Signal()
    _start_worker = Signal()
    _stop_worker = Signal()
    _shutdown_worker = Signal()
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
    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._unsubscribe()
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

    def _on_service_state(self, state: MonitorState) -> None:
        if self._closing:
            return
        self._state_relay.emit(self._snapshot_from_service(state))

    def _on_service_activity(self, event: ActivityEvent) -> None:
        if not self._closing:
            self._activity_relay.emit(event)

    @Slot(object)
    def _accept_service_state(self, snapshot: ServiceSnapshot) -> None:
        if self._closing:
            return
        self._publish_snapshot(snapshot)

    @Slot(object)
    def _accept_service_activity(self, event: ActivityEvent) -> None:
        if not self._closing:
            self.activity_received.emit(event)

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

    @Slot(str, object, int)
    def _on_command_finished(
        self,
        command: str,
        result: object,
        worker_thread_id: int,
    ) -> None:
        if self._closing:
            return
        self.operation_thread_observed.emit(command, worker_thread_id)
        self.command_completed.emit(command, result)
        desired_changed = self._desired_running != self._inflight_running
        self._command_active = False
        self._inflight_running = None
        self._publish_snapshot(self._snapshot_from_service())
        if desired_changed:
            self._dispatch_lifecycle_command()

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
            self._closing = False
            self._subscribe()
            self.command_failed.emit(command, message)
            failed = replace(
                self._snapshot_from_service(),
                monitor_state=MonitorState.ERROR,
                error=message,
            )
            self._publish_snapshot(failed)
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
