import logging
import threading
import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app.mover import move_file_with_retries
from app.smart_classifier import smart_classify


def should_ignore_file(file_path: Path, config: dict) -> bool:
    ignored_extensions = [ext.lower().strip() for ext in config.get("ignored_extensions", [])]
    ignored_prefixes = config.get("ignored_prefixes", [])
    file_extension = file_path.suffix.lower().strip()
    if file_extension in ignored_extensions:
        return True
    for prefix in ignored_prefixes:
        if file_path.name.startswith(prefix):
            return True
    return False


class NewFileHandler(FileSystemEventHandler):
    def __init__(self, config: dict, extension_lookup: dict, plugin_manager=None,
                 file_processed_callback=None):
        self.config = config
        self.extension_lookup = extension_lookup
        self.plugin_manager = plugin_manager
        self.destination_folders = config["destination_folders"]
        self.rules = config["rules"]
        self.processing_wait_seconds = config.get("processing_wait_seconds", 5)
        self.duplicate_event_window = config.get("duplicate_event_window_seconds", 3)
        self.archive_by_date = config.get("archive_by_date", False)
        self.stats_file = config["stats_file"]
        self.history_file = config["history_file"]
        self.hash_db_file = config["hash_db_file"]
        self.recent_events: dict[str, float] = {}
        self._lock = threading.Lock()
        self.last_processed_file = "No file processed yet"
        # callback(filename, category, status) — يُستدعى من thread منفصل
        # يجب أن يكون thread-safe (استخدم root.after من الـ GUI)
        self.file_processed_callback = file_processed_callback

    # ---- Watchdog event handlers ----

    def on_created(self, event):
        if not event.is_directory:
            self._dispatch(event.src_path, "created")

    def on_modified(self, event):
        if not event.is_directory:
            self._dispatch(event.src_path, "modified")

    def on_moved(self, event):
        if not event.is_directory:
            self._dispatch(event.dest_path, "moved")

    # ---- Internal ----

    def is_duplicate_event(self, file_path: Path) -> bool:
        now = time.time()
        key = str(file_path)
        with self._lock:
            expired = [k for k, t in self.recent_events.items()
                       if now - t > self.duplicate_event_window]
            for k in expired:
                del self.recent_events[k]
            if key in self.recent_events:
                return True
            self.recent_events[key] = now
        return False

    def _dispatch(self, file_path: str, source_event: str) -> None:
        """كل ملف في thread منفصل — watchdog لا يتجمّد."""
        threading.Thread(
            target=self._process_file_thread,
            args=(file_path, source_event),
            daemon=True,
        ).start()

    def _process_file_thread(self, file_path: str, source_event: str) -> None:
        classification_method = "extension"
        smart_source = ""
        final_category = None
        status = "unknown"

        try:
            source_path = Path(file_path)

            if source_path.is_dir() or not source_path.exists():
                return

            if should_ignore_file(source_path, self.config):
                logging.debug(f"Ignored: {source_path.name}")
                return

            if self.is_duplicate_event(source_path):
                logging.debug(f"Duplicate event skipped: {source_path.name}")
                return

            logging.info(f"Detected: {source_path.name} | event: {source_event}")
            time.sleep(self.processing_wait_seconds)

            self.last_processed_file = source_path.name

            # 1) Plugins
            if self.plugin_manager:
                plugin_category = self.plugin_manager.classify_with_plugins(
                    source_path, {"rules": self.rules}
                )
                if plugin_category:
                    logging.info(f"Plugin classified: {source_path.name} → {plugin_category}")
                    final_category = plugin_category
                    classification_method = "plugin"
                    smart_source = "plugin"

            # 2) Smart Classifier
            if not final_category:
                smart_category = smart_classify(source_path)
                if smart_category:
                    logging.info(f"Smart classified: {source_path.name} → {smart_category}")
                    final_category = smart_category
                    classification_method = "smart"
                    smart_source = "content_or_filename"

            # 3) AI Classifier (fallback when smart + extension both miss)
            if not final_category:
                try:
                    from app.ai_classifier import get_ai_classifier
                    ai = get_ai_classifier(self.config)
                    if ai.is_enabled and ai.is_available():
                        categories = list(self.rules.keys())
                        ai_result = ai.classify(source_path.name, categories)
                        if ai_result.ok:
                            logging.info(f"AI classified: {source_path.name} → {ai_result.category} ({ai_result.reason})")
                            final_category = ai_result.category
                            classification_method = "ai"
                            smart_source = f"{ai_result.provider}:{ai_result.reason[:40]}"
                except Exception as ai_err:
                    logging.debug(f"AI classifier skipped: {ai_err}")

            # 3) إنشاء مجلد فئة جديدة إذا لزم
            if final_category and final_category not in self.destination_folders:
                new_folder = Path(self.destination_folders["others"]).parent / final_category
                new_folder.mkdir(parents=True, exist_ok=True)
                self.destination_folders[final_category] = str(new_folder)

            if final_category:
                self.extension_lookup[source_path.suffix.lower()] = final_category

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
                delay=2,
                classification_method=classification_method,
                smart_source=smart_source,
            )
            status = "moved"

        except Exception as error:
            logging.error(f"Error processing {file_path}: {error}", exc_info=True)
            status = "error"

        finally:
            # أبلغ الـ GUI لحظياً بانتهاء المعالجة
            if self.file_processed_callback is not None:
                try:
                    self.file_processed_callback(
                        Path(file_path).name,
                        final_category or "unknown",
                        status,
                    )
                except Exception:
                    pass


class FileMonitor:
    def __init__(self, config: dict, extension_lookup: dict, plugin_manager=None,
                 file_processed_callback=None):
        self.config = config
        self.extension_lookup = extension_lookup
        self.source_folder = config["source_folder"]
        self.event_handler = NewFileHandler(
            config, extension_lookup, plugin_manager,
            file_processed_callback=file_processed_callback,
        )
        self.observer = Observer()
        self.is_running = False

    def set_file_processed_callback(self, callback) -> None:
        """ضبط الـ callback بعد الإنشاء (مفيد عند reload)."""
        self.event_handler.file_processed_callback = callback

    def scan_existing_files(self) -> None:
        def _scan():
            source_folder = Path(self.source_folder)
            if not source_folder.exists():
                return
            logging.info("Scanning existing files in incoming folder...")
            for item in source_folder.iterdir():
                if item.is_file():
                    self.event_handler._process_file_thread(str(item), "startup_scan")

        threading.Thread(target=_scan, daemon=True).start()

    def start(self) -> None:
        if self.is_running:
            return
        self.scan_existing_files()
        self.observer.schedule(self.event_handler, self.source_folder, recursive=False)
        self.observer.start()
        self.is_running = True
        logging.info(f"Monitoring started: {self.source_folder}")

    def stop(self) -> None:
        if not self.is_running:
            return
        self.observer.stop()
        self.observer.join()
        self.is_running = False
        logging.info("Monitoring stopped.")
