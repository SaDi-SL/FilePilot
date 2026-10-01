"""
multi_watcher.py — Multi-folder monitoring engine for FilePilot.

Manages multiple FileMonitor instances, one per watch folder.
All folders share the same rules, organized_base_folder, and plugins.

Usage:
    from app.multi_watcher import MultiFolderMonitor
    monitor = MultiFolderMonitor(config, extension_lookup, plugin_manager)
    monitor.set_file_processed_callback(callback)
    monitor.start_all()
    monitor.stop_all()
    monitor.start_folder("C:/Downloads")
    monitor.stop_folder("C:/Downloads")
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.path_topology import (
    get_organized_root,
    validate_configured_topology,
    validate_watch_root,
)
from app.watcher import FileMonitor

logger = logging.getLogger(__name__)


class MultiFolderMonitor:
    """
    Orchestrates one FileMonitor per active watch folder.

    Config schema expected:
        watch_folders: [
            {"path": "C:/Downloads",  "active": true,  "label": "Downloads"},
            {"path": "C:/Desktop",    "active": false, "label": "Desktop"},
        ]
        organized_base_folder: "C:/Organized"
        rules: { ... }
    """

    def __init__(
        self,
        config: dict,
        extension_lookup: dict,
        plugin_manager=None,
        file_processed_callback=None,
        operation_journal=None,
    ) -> None:
        self.config            = config
        self.extension_lookup  = extension_lookup
        self.plugin_manager    = plugin_manager
        self._callback         = file_processed_callback
        self.operation_journal = operation_journal

        # path_str → FileMonitor
        self._monitors: dict[str, FileMonitor] = {}
        self._lifecycle_lock = threading.RLock()
        self._lifecycle_condition = threading.Condition(self._lifecycle_lock)
        self._lifecycle_operation: str | None = None

        self._build_monitors()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """True if at least one monitor is running."""
        with self._lifecycle_lock:
            monitors = list(self._monitors.values())
        return any(m.is_running for m in monitors)

    @property
    def running_folders(self) -> list[str]:
        with self._lifecycle_lock:
            monitors = list(self._monitors.items())
        return [p for p, m in monitors if m.is_running]

    @property
    def all_folders(self) -> list[dict]:
        """Return watch_folders list from config."""
        with self._lifecycle_lock:
            return list(self.config.get("watch_folders", []))

    def set_file_processed_callback(self, callback) -> None:
        """Update callback for all monitors (used after theme/language reload)."""
        with self._lifecycle_lock:
            self._callback = callback
            monitors = list(self._monitors.values())
        for monitor in monitors:
            monitor.set_file_processed_callback(callback)

    def start_all(self) -> None:
        """Start all active folders."""
        self._begin_lifecycle_operation("start_all")
        errors = []
        try:
            validate_configured_topology(self.config)
            with self._lifecycle_lock:
                monitors = []
                for folder in self.config.get("watch_folders", []):
                    if not folder.get("active", True):
                        continue
                    path = str(Path(folder["path"]).resolve())
                    monitor = self._monitors.get(path)
                    if monitor is None:
                        monitor = self._add_monitor(path)
                    monitors.append((path, monitor))

            for path, monitor in monitors:
                if not Path(path).exists():
                    logger.warning(f"Watch folder does not exist: {path}")
                    continue
                try:
                    monitor.start()
                    logger.info(f"Started monitoring: {path}")
                except Exception as error:
                    errors.append(error)
                    logger.error(f"Failed to start monitoring: {path}", exc_info=True)
        finally:
            self._end_lifecycle_operation()

        if errors:
            raise RuntimeError("One or more folder monitors failed to start") from errors[0]

    def stop_all(self) -> None:
        """Stop and drain every child monitor."""
        self._begin_lifecycle_operation("stop_all")
        errors = []
        try:
            with self._lifecycle_lock:
                monitors = list(self._monitors.items())

            for path, monitor in monitors:
                was_running = monitor.is_running
                try:
                    monitor.stop()
                    if was_running:
                        logger.info(f"Stopped monitoring: {path}")
                except Exception as error:
                    errors.append(error)
                    logger.error(f"Failed to stop monitoring: {path}", exc_info=True)
        finally:
            self._end_lifecycle_operation()

        if errors:
            raise RuntimeError("One or more folder monitors failed to stop") from errors[0]

    def start_folder(self, path: str) -> bool:
        """
        Start monitoring a specific folder.
        Returns True if started, False if already running or invalid.
        """
        self._begin_lifecycle_operation("start_folder")
        try:
            validate_watch_root(path, get_organized_root(self.config))
            with self._lifecycle_lock:
                path = str(Path(path).resolve())
                if path not in self._monitors:
                    self._add_monitor(path)
                monitor = self._monitors.get(path)

            if not monitor or monitor.is_running:
                return False

            if not Path(path).exists():
                logger.warning(f"Watch folder does not exist: {path}")
                return False

            monitor.start()
            logger.info(f"Started monitoring: {path}")
            return True
        finally:
            self._end_lifecycle_operation()

    def stop_folder(self, path: str) -> bool:
        """
        Stop monitoring a specific folder.
        Returns True if stopped.
        """
        self._begin_lifecycle_operation("stop_folder")
        try:
            with self._lifecycle_lock:
                path = str(Path(path).resolve())
                monitor = self._monitors.get(path)
            if not monitor:
                return False
            was_running = monitor.is_running
            monitor.stop()
            if was_running:
                logger.info(f"Stopped monitoring: {path}")
            return was_running
        finally:
            self._end_lifecycle_operation()

    def _begin_lifecycle_operation(self, operation: str) -> None:
        with self._lifecycle_condition:
            self._reject_processing_callback_lifecycle_change()
            while self._lifecycle_operation is not None:
                self._lifecycle_condition.wait()
            self._lifecycle_operation = operation

    def _reject_processing_callback_lifecycle_change(self) -> None:
        if any(
            getattr(
                monitor,
                "owns_current_processing_thread",
                lambda: False,
            )()
            for monitor in self._monitors.values()
        ):
            raise RuntimeError(
                "A processing callback cannot change monitor lifecycle"
            )

    def _end_lifecycle_operation(self) -> None:
        with self._lifecycle_condition:
            self._lifecycle_operation = None
            self._lifecycle_condition.notify_all()

    def add_watch_folder(self, path: str, label: str = "", active: bool = True) -> bool:
        """
        Add a new folder to watch_folders in config and create its monitor.
        Returns True if added, False if already exists.
        """
        with self._lifecycle_condition:
            self._reject_processing_callback_lifecycle_change()
            while self._lifecycle_operation is not None:
                self._lifecycle_condition.wait()
            path = str(Path(path).resolve())
            validate_watch_root(path, get_organized_root(self.config))
            folders = self.config.setdefault("watch_folders", [])

            # Check for duplicate
            for f in folders:
                if str(Path(f["path"]).resolve()) == path:
                    return False

            entry = {"path": path, "label": label or Path(path).name, "active": active}
            folders.append(entry)
            self._add_monitor(path)
            logger.info(f"Added watch folder: {path}")
            return True

    def remove_watch_folder(self, path: str) -> bool:
        """
        Remove a folder from watch_folders. Stops it first if running.
        Returns True if removed.
        """
        self._begin_lifecycle_operation("remove_watch_folder")
        try:
            path = str(Path(path).resolve())
            with self._lifecycle_lock:
                monitor = self._monitors.get(path)

            if monitor is not None:
                monitor.stop()

            with self._lifecycle_lock:
                folders = self.config.get("watch_folders", [])
                before = len(folders)
                self.config["watch_folders"] = [
                    f for f in folders
                    if str(Path(f["path"]).resolve()) != path
                ]
                removed = len(self.config["watch_folders"]) < before

                if path in self._monitors:
                    del self._monitors[path]

            if removed:
                logger.info(f"Removed watch folder: {path}")
            return removed
        finally:
            self._end_lifecycle_operation()

    def set_folder_active(self, path: str, active: bool) -> None:
        """Toggle the active flag of a folder in config (does not start/stop)."""
        path_resolved = str(Path(path).resolve())
        with self._lifecycle_condition:
            self._reject_processing_callback_lifecycle_change()
            while self._lifecycle_operation is not None:
                self._lifecycle_condition.wait()
            for f in self.config.get("watch_folders", []):
                if str(Path(f["path"]).resolve()) == path_resolved:
                    f["active"] = active
                    break

    def folder_status(self, path: str) -> str:
        """Return 'running', 'stopped', or 'not_found'."""
        path = str(Path(path).resolve())
        with self._lifecycle_lock:
            monitor = self._monitors.get(path)
        if not monitor:
            return "not_found"
        return "running" if monitor.is_running else "stopped"

    def reload_config(self, new_config: dict, new_extension_lookup: dict) -> None:
        """
        Called after settings are saved. Stops all, rebuilds monitors,
        does NOT auto-restart (caller decides).
        """
        validate_configured_topology(new_config)
        self._begin_lifecycle_operation("reload_config")
        errors = []
        try:
            with self._lifecycle_lock:
                monitors = list(self._monitors.items())

            for path, monitor in monitors:
                try:
                    monitor.stop()
                except Exception as error:
                    errors.append(error)
                    logger.error(f"Failed to stop monitoring: {path}", exc_info=True)

            if errors:
                raise RuntimeError(
                    "One or more folder monitors failed during reload"
                ) from errors[0]

            with self._lifecycle_lock:
                self.config = new_config
                self.extension_lookup = new_extension_lookup
                self._monitors.clear()
                self._build_monitors()
        finally:
            self._end_lifecycle_operation()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_monitors(self) -> None:
        """Create FileMonitor for each watch folder in config."""
        # Migrate legacy single-folder config
        self._migrate_legacy_config()
        validate_configured_topology(self.config)

        for folder in self.config.get("watch_folders", []):
            path = str(Path(folder["path"]).resolve())
            self._add_monitor(path)

    def _add_monitor(self, path: str) -> FileMonitor:
        """Create and register a FileMonitor for the given path."""
        validate_watch_root(path, get_organized_root(self.config))
        # Build a per-folder config view (shares rules + organized_base)
        folder_config = dict(self.config)
        folder_config["source_folder"] = path

        monitor_arguments = {
            "config": folder_config,
            "extension_lookup": self.extension_lookup,
            "plugin_manager": self.plugin_manager,
            "file_processed_callback": self._callback,
        }
        if self.operation_journal is not None:
            monitor_arguments["operation_journal"] = self.operation_journal
        monitor = FileMonitor(**monitor_arguments)
        self._monitors[path] = monitor
        return monitor

    def _migrate_legacy_config(self) -> None:
        """
        Convert old single-folder config to watch_folders list.
        Called once on startup for backwards compatibility.
        """
        if "watch_folders" not in self.config:
            legacy_path = self.config.get("source_folder", "")
            if legacy_path:
                self.config["watch_folders"] = [
                    {
                        "path": legacy_path,
                        "label": Path(legacy_path).name,
                        "active": True,
                    }
                ]
                logger.info(f"Migrated legacy source_folder to watch_folders: {legacy_path}")
            else:
                self.config["watch_folders"] = []
