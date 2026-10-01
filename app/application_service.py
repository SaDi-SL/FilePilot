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
from app.mover import MoveResult
from app.operation_journal import JournalError
from app.recovery import (
    RecoveryBlockedError,
    RecoveryError,
    RecoveryReport,
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


StateSubscriber = Callable[[MonitorState], None]
ActivitySubscriber = Callable[[ActivityEvent], None]


class FilePilotService:
    """Process-scoped owner of FilePilot bootstrap and monitor lifecycle."""

    def __init__(
        self,
        *,
        config_path: str | Path | None = None,
        monitor_builder: Callable[[], tuple[dict, object]] | None = None,
    ) -> None:
        self._config_path = (
            Path(config_path) if config_path is not None else get_external_config_path()
        )
        self._monitor_builder = monitor_builder
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
                    from app.main import build_monitor
                    builder = build_monitor
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

            self._bind_activity_callback(monitor)
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
            self._set_monitor_state(MonitorState.STOPPED)
            return result

    def start(self) -> MonitorState:
        with self._lifecycle_lock:
            startup = self.startup_result or self.bootstrap()
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
        return has_source or has_watch

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
