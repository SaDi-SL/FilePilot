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
from pathlib import Path

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
    ) -> None:
        self.config            = config
        self.extension_lookup  = extension_lookup
        self.plugin_manager    = plugin_manager
        self._callback         = file_processed_callback

        # path_str → FileMonitor
        self._monitors: dict[str, FileMonitor] = {}

        self._build_monitors()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """True if at least one monitor is running."""
        return any(m.is_running for m in self._monitors.values())

    @property
    def running_folders(self) -> list[str]:
        return [p for p, m in self._monitors.items() if m.is_running]

    @property
    def all_folders(self) -> list[dict]:
        """Return watch_folders list from config."""
        return self.config.get("watch_folders", [])

    def set_file_processed_callback(self, callback) -> None:
        """Update callback for all monitors (used after theme/language reload)."""
        self._callback = callback
        for m in self._monitors.values():
            m.set_file_processed_callback(callback)

    def start_all(self) -> None:
        """Start all active folders."""
        folders = self.config.get("watch_folders", [])
        for folder in folders:
            if folder.get("active", True):
                self.start_folder(folder["path"])

    def stop_all(self) -> None:
        """Stop all running monitors."""
        for path in list(self._monitors.keys()):
            self.stop_folder(path)

    def start_folder(self, path: str) -> bool:
        """
        Start monitoring a specific folder.
        Returns True if started, False if already running or invalid.
        """
        path = str(Path(path).resolve())
        if path not in self._monitors:
            self._add_monitor(path)

        monitor = self._monitors.get(path)
        if not monitor:
            return False

        if monitor.is_running:
            return False

        if not Path(path).exists():
            logger.warning(f"Watch folder does not exist: {path}")
            return False

        monitor.start()
        logger.info(f"Started monitoring: {path}")
        return True

    def stop_folder(self, path: str) -> bool:
        """
        Stop monitoring a specific folder.
        Returns True if stopped.
        """
        path = str(Path(path).resolve())
        monitor = self._monitors.get(path)
        if not monitor or not monitor.is_running:
            return False
        monitor.stop()
        logger.info(f"Stopped monitoring: {path}")
        return True

    def add_watch_folder(self, path: str, label: str = "", active: bool = True) -> bool:
        """
        Add a new folder to watch_folders in config and create its monitor.
        Returns True if added, False if already exists.
        """
        path = str(Path(path).resolve())
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
        path = str(Path(path).resolve())
        self.stop_folder(path)

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

    def set_folder_active(self, path: str, active: bool) -> None:
        """Toggle the active flag of a folder in config (does not start/stop)."""
        path_resolved = str(Path(path).resolve())
        for f in self.config.get("watch_folders", []):
            if str(Path(f["path"]).resolve()) == path_resolved:
                f["active"] = active
                break

    def folder_status(self, path: str) -> str:
        """Return 'running', 'stopped', or 'not_found'."""
        path = str(Path(path).resolve())
        monitor = self._monitors.get(path)
        if not monitor:
            return "not_found"
        return "running" if monitor.is_running else "stopped"

    def reload_config(self, new_config: dict, new_extension_lookup: dict) -> None:
        """
        Called after settings are saved. Stops all, rebuilds monitors,
        does NOT auto-restart (caller decides).
        """
        self.stop_all()
        self.config           = new_config
        self.extension_lookup = new_extension_lookup
        self._monitors.clear()
        self._build_monitors()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_monitors(self) -> None:
        """Create FileMonitor for each watch folder in config."""
        # Migrate legacy single-folder config
        self._migrate_legacy_config()

        for folder in self.config.get("watch_folders", []):
            path = str(Path(folder["path"]).resolve())
            self._add_monitor(path)

    def _add_monitor(self, path: str) -> FileMonitor:
        """Create and register a FileMonitor for the given path."""
        # Build a per-folder config view (shares rules + organized_base)
        folder_config = dict(self.config)
        folder_config["source_folder"] = path

        monitor = FileMonitor(
            config=folder_config,
            extension_lookup=self.extension_lookup,
            plugin_manager=self.plugin_manager,
            file_processed_callback=self._callback,
        )
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
