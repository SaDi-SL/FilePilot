import logging
import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app.mover import move_file_with_retries


def should_ignore_file(file_path: Path, config: dict) -> bool:
    """تحديد هل يجب تجاهل الملف أم لا."""
    ignored_extensions = [ext.lower().strip() for ext in config.get("ignored_extensions", [])]
    ignored_prefixes = config.get("ignored_prefixes", [])

    file_name = file_path.name
    file_extension = file_path.suffix.lower().strip()

    if file_extension in ignored_extensions:
        return True

    for prefix in ignored_prefixes:
        if file_name.startswith(prefix):
            return True

    return False


class NewFileHandler(FileSystemEventHandler):
    def __init__(self, config: dict, extension_lookup: dict):
        self.config = config
        self.extension_lookup = extension_lookup
        self.destination_folders = config["destination_folders"]
        self.rules = config["rules"]
        self.processing_wait_seconds = config.get("processing_wait_seconds", 5)
        self.duplicate_event_window = config.get("duplicate_event_window_seconds", 3)
        self.archive_by_date = config.get("archive_by_date", False)
        self.stats_file = config["stats_file"]
        self.history_file = config["history_file"]
        self.hash_db_file = config["hash_db_file"]
        self.recent_events = {}
        self.last_processed_file = "No file processed yet"

    def is_duplicate_event(self, file_path: Path) -> bool:
        now = time.time()
        key = str(file_path)

        expired_keys = [
            existing_key
            for existing_key, timestamp in self.recent_events.items()
            if now - timestamp > self.duplicate_event_window
        ]
        for expired_key in expired_keys:
            del self.recent_events[expired_key]

        if key in self.recent_events:
            return True

        self.recent_events[key] = now
        return False

    def process_file(self, file_path: str, source_event: str) -> None:
        try:
            source_path = Path(file_path)

            if source_path.is_dir():
                return

            if not source_path.exists():
                return

            if should_ignore_file(source_path, self.config):
                print(f"[DEBUG] Ignored file: {source_path.name}")
                return

            if self.is_duplicate_event(source_path):
                print(f"[DEBUG] Duplicate event ignored: {source_path.name}")
                return

            print(f"[DEBUG] Detected file: {source_path.name} | event: {source_event}")
            print(f"[DEBUG] Waiting {self.processing_wait_seconds} seconds before processing: {source_path.name}")

            time.sleep(self.processing_wait_seconds)

            self.last_processed_file = source_path.name

            move_file_with_retries(
                source_file=source_path,
                destination_folders=self.destination_folders,
                extension_lookup=self.extension_lookup,
                stats_file=self.stats_file,
                history_file=self.history_file,
                hash_db_file=self.hash_db_file,
                archive_by_date=self.archive_by_date,
                rules=self.rules,
                retries=8,
                delay=2
            )

        except Exception as error:
            logging.error(f"Error while processing file {file_path}: {error}")
            print(f"[DEBUG] Processing error: {error}")


class FileMonitor:
    def __init__(self, config: dict, extension_lookup: dict):
        self.config = config
        self.extension_lookup = extension_lookup
        self.source_folder = config["source_folder"]
        self.event_handler = NewFileHandler(config, extension_lookup)
        self.observer = Observer()
        self.is_running = False

    def scan_existing_files(self) -> None:
        source_folder = Path(self.source_folder)

        if not source_folder.exists():
            return

        print("[DEBUG] Scanning existing files in incoming...")
        for item in source_folder.iterdir():
            if item.is_file():
                self.event_handler.process_file(str(item), "startup_scan")

    def start(self) -> None:
        if self.is_running:
            return

        self.scan_existing_files()
        self.observer.schedule(self.event_handler, self.source_folder, recursive=False)
        self.observer.start()
        self.is_running = True

        print(f"[DEBUG] Monitoring folder: {self.source_folder}")

    def stop(self) -> None:
        if not self.is_running:
            return

        self.observer.stop()
        self.observer.join()
        self.is_running = False
        print("[DEBUG] Monitoring stopped.")