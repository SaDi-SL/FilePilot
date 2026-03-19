"""
auto_backup.py — Automatic weekly backup for FilePilot.

Backs up config.json + history.csv + smart_rules.json every 7 days.
Keeps the last 5 backups, deletes older ones automatically.

Usage:
    from app.auto_backup import AutoBackupManager
    mgr = AutoBackupManager(config)
    mgr.start()          # start background scheduler
    mgr.stop()           # stop on app exit
    mgr.run_now()        # force immediate backup
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
BACKUP_INTERVAL_DAYS = 7
MAX_BACKUPS_TO_KEEP   = 5
LAST_BACKUP_KEY       = "last_backup_timestamp"


class AutoBackupManager:
    """
    Runs a background thread that checks once per hour whether a weekly
    backup is due. If yes, it copies the key data files to a timestamped
    backup folder and prunes old backups.
    """

    def __init__(self, config: dict) -> None:
        self.config      = config
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background backup scheduler thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._scheduler_loop,
            name="FilePilot-AutoBackup",
            daemon=True,
        )
        self._thread.start()
        logger.debug("AutoBackupManager started.")

    def stop(self) -> None:
        """Stop the scheduler thread gracefully."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        logger.debug("AutoBackupManager stopped.")

    def run_now(self) -> tuple[bool, str]:
        """
        Force an immediate backup regardless of schedule.
        Returns (success, message).
        """
        return self._do_backup(forced=True)

    def get_backup_folder(self) -> Path:
        """Return the path to the backups directory (creates it if needed)."""
        from app.config_loader import get_runtime_base_dir
        folder = get_runtime_base_dir() / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def list_backups(self) -> list[dict]:
        """
        Return a list of existing backup entries, newest first.
        Each entry: {"name": str, "path": Path, "created": datetime}
        """
        folder = self.get_backup_folder()
        entries = []
        for child in sorted(folder.iterdir(), reverse=True):
            if child.is_dir() and child.name.startswith("backup_"):
                try:
                    dt = datetime.strptime(child.name, "backup_%Y-%m-%d_%H-%M-%S")
                    entries.append({"name": child.name, "path": child, "created": dt})
                except ValueError:
                    pass
        return entries

    def is_due(self) -> bool:
        """Return True if a backup is due (last backup > 7 days ago or never done)."""
        return self._days_since_last_backup() >= BACKUP_INTERVAL_DAYS

    def days_until_next(self) -> int:
        """Return how many days remain until the next scheduled backup."""
        remaining = BACKUP_INTERVAL_DAYS - self._days_since_last_backup()
        return max(0, int(remaining))

    # ── Internal ──────────────────────────────────────────────────────────────

    def _scheduler_loop(self) -> None:
        """Check every hour if a backup is due."""
        # Check immediately on start
        if self.is_due():
            self._do_backup()

        while not self._stop_event.wait(timeout=3600):   # check every hour
            if self.is_due():
                self._do_backup()

    def _days_since_last_backup(self) -> float:
        """Return days elapsed since the last successful backup."""
        from app.config_loader import get_config_path
        try:
            cfg_path = get_config_path()
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            last_ts = cfg.get(LAST_BACKUP_KEY)
            if not last_ts:
                return float("inf")
            last_dt = datetime.fromisoformat(last_ts)
            return (datetime.now() - last_dt).total_seconds() / 86400
        except Exception:
            return float("inf")

    def _save_last_backup_timestamp(self) -> None:
        """Write the current timestamp to config.json."""
        from app.config_loader import get_config_path
        try:
            cfg_path = get_config_path()
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg[LAST_BACKUP_KEY] = datetime.now().isoformat()
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Could not save backup timestamp: {e}")

    def _files_to_backup(self) -> list[Path]:
        """Return list of files that should be backed up."""
        from app.config_loader import get_config_path, get_smart_rules_path
        candidates = [
            get_config_path(),
            get_smart_rules_path(),
            Path(self.config.get("history_file", "")),
            Path(self.config.get("stats_file", "")),
        ]
        return [p for p in candidates if p and p.exists()]

    def _do_backup(self, forced: bool = False) -> tuple[bool, str]:
        """
        Create a timestamped backup folder and copy key files into it.
        Returns (success, message).
        """
        try:
            ts = datetime.now().strftime("backup_%Y-%m-%d_%H-%M-%S")
            backup_dir = self.get_backup_folder() / ts
            backup_dir.mkdir(parents=True, exist_ok=True)

            files = self._files_to_backup()
            if not files:
                return False, "No files found to back up."

            copied = []
            for src in files:
                try:
                    shutil.copy2(src, backup_dir / src.name)
                    copied.append(src.name)
                except Exception as e:
                    logger.warning(f"Backup: could not copy {src.name}: {e}")

            if not copied:
                backup_dir.rmdir()
                return False, "Backup failed: could not copy any files."

            self._prune_old_backups()
            self._save_last_backup_timestamp()

            msg = f"Backup created: {ts} ({len(copied)} files)"
            logger.info(msg)
            return True, msg

        except Exception as e:
            msg = f"Backup error: {e}"
            logger.error(msg)
            return False, msg

    def _prune_old_backups(self) -> None:
        """Delete oldest backups, keeping only MAX_BACKUPS_TO_KEEP."""
        backups = self.list_backups()
        to_delete = backups[MAX_BACKUPS_TO_KEEP:]
        for entry in to_delete:
            try:
                shutil.rmtree(entry["path"])
                logger.debug(f"Pruned old backup: {entry['name']}")
            except Exception as e:
                logger.warning(f"Could not prune backup {entry['name']}: {e}")