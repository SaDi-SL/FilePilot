import logging
import math
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app.mover import MoveStatus, move_file_with_retries
from app.path_topology import (
    get_organized_root,
    is_path_within,
    validate_watch_root,
)
from app.smart_classifier import smart_classify


class _ProcessingResult(Enum):
    COMPLETED = auto()
    RETRY_WHEN_STABLE = auto()
    ABANDONED = auto()


@dataclass(frozen=True)
class _RetryJob:
    file_path: str
    deadline: float
    retry_number: int
    identity: tuple[int, int] | None
    generation: int


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
                  file_processed_callback=None, submit_file=None,
                  stability_interval_seconds=None, stable_intervals=None,
                  stability_timeout_seconds=None, operation_journal=None):
        self.config = config
        self.extension_lookup = extension_lookup
        self.plugin_manager = plugin_manager
        self.destination_folders = config["destination_folders"]
        self.organized_root = get_organized_root(config)
        self.rules = config["rules"]
        try:
            self.processing_wait_seconds = max(
                float(config.get("processing_wait_seconds", 5)), 0.0
            )
        except (TypeError, ValueError):
            self.processing_wait_seconds = 5.0
        self.archive_by_date = config.get("archive_by_date", False)
        self.stats_file = config["stats_file"]
        self.history_file = config["history_file"]
        self.hash_db_file = config["hash_db_file"]
        self.operation_journal = operation_journal
        self._submit_file = submit_file
        if stability_interval_seconds is None:
            stability_interval_seconds = config.get(
                "processing_stability_interval_seconds", 0.5
            )
        if stable_intervals is None:
            stable_intervals = config.get("processing_stability_checks", 2)
        if stability_timeout_seconds is None:
            stability_timeout_seconds = config.get(
                "processing_stability_timeout_seconds",
                self.processing_wait_seconds,
            )
        try:
            self.stability_interval_seconds = max(
                float(stability_interval_seconds), 0.01
            )
        except (TypeError, ValueError):
            self.stability_interval_seconds = 0.5
        try:
            self.stable_intervals = max(int(stable_intervals), 1)
        except (TypeError, ValueError):
            self.stable_intervals = 2
        try:
            self.stability_timeout_seconds = max(
                float(stability_timeout_seconds), 0.0
            )
        except (TypeError, ValueError):
            self.stability_timeout_seconds = self.processing_wait_seconds
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

    def _dispatch(self, file_path: str, source_event: str) -> None:
        if self._is_organized_output(file_path):
            return
        if self._submit_file is not None:
            self._submit_file(file_path, source_event)
        else:
            self._process_file_thread(file_path, source_event)

    def _is_organized_output(self, file_path: str | Path) -> bool:
        try:
            inside_organized = is_path_within(file_path, self.organized_root)
        except (OSError, RuntimeError):
            logging.warning(f"Ignored path that could not be resolved safely: {file_path}")
            return True
        if inside_organized:
            logging.debug(f"Ignored organized output: {file_path}")
        return inside_organized

    @staticmethod
    def _sample_file_state(source_path: Path):
        try:
            if not source_path.is_file():
                return None
            stat = source_path.stat()
            return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
        except OSError:
            return None

    def _wait_until_stable(self, source_path: Path, stop_event=None) -> bool:
        previous_state = self._sample_file_state(source_path)
        if previous_state is None:
            return False

        minimum_stability_window = (
            self.stability_interval_seconds * self.stable_intervals
        )
        timeout = max(
            self.stability_timeout_seconds,
            minimum_stability_window,
        )

        stable_count = 0
        elapsed = 0.0
        while elapsed < timeout:
            wait_seconds = min(self.stability_interval_seconds, timeout - elapsed)
            if stop_event is not None:
                if stop_event.wait(wait_seconds):
                    return False
            else:
                time.sleep(wait_seconds)
            elapsed += wait_seconds

            current_state = self._sample_file_state(source_path)
            if current_state is None:
                return False
            if current_state == previous_state:
                stable_count += 1
                if stable_count >= self.stable_intervals:
                    return True
            else:
                stable_count = 0
                previous_state = current_state

        return False

    def _process_file_thread(self, file_path: str, source_event: str,
                              stop_event=None) -> _ProcessingResult:
        classification_method = "extension"
        smart_source = ""
        final_category = None
        status = "unknown"
        source_path = Path(file_path)
        notify_callback = False

        if self._is_organized_output(source_path):
            return _ProcessingResult.ABANDONED

        try:
            if not source_path.is_file():
                return _ProcessingResult.ABANDONED

            if should_ignore_file(source_path, self.config):
                logging.debug(f"Ignored: {source_path.name}")
                return _ProcessingResult.ABANDONED

            logging.info(f"Detected: {source_path.name} | event: {source_event}")
            if not self._wait_until_stable(source_path, stop_event):
                if stop_event is not None and stop_event.is_set():
                    logging.debug(f"Stability wait cancelled: {source_path.name}")
                    return _ProcessingResult.ABANDONED
                if source_path.is_file():
                    logging.info(f"File is still changing; retry deferred: {source_path.name}")
                    return _ProcessingResult.RETRY_WHEN_STABLE
                logging.info(f"File disappeared before becoming stable: {source_path.name}")
                return _ProcessingResult.ABANDONED

            notify_callback = True
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

            # 3) Configured extension rule
            suffix = source_path.suffix.lower().strip()
            if not final_category and suffix in self.extension_lookup:
                final_category = self.extension_lookup[suffix]

            # 4) AI Classifier (fallback when plugin, smart, and extension miss)
            if not final_category:
                ai_enabled = self.config.get("ai", {}).get("enabled") is True
                if ai_enabled:
                    try:
                        from app.ai_classifier import get_ai_classifier
                        ai = get_ai_classifier(self.config)
                        categories = list(self.rules.keys())
                        if ai.is_enabled and ai.is_available():
                            ai_result = ai.classify(source_path.name, categories)
                            if ai_result.ok and ai_result.category in categories:
                                logging.info(f"AI classified: {source_path.name} → {ai_result.category} ({ai_result.reason})")
                                final_category = ai_result.category
                                classification_method = "ai"
                                smart_source = f"{ai_result.provider}:{ai_result.reason[:40]}"
                    except Exception as ai_err:
                        logging.debug(f"AI classifier skipped: {ai_err}")

            # 5) Others
            if not final_category:
                final_category = "others"

            # إنشاء مجلد فئة جديدة إذا لزم
            if final_category and final_category not in self.destination_folders:
                new_folder = self.organized_root / final_category
                self.destination_folders[final_category] = str(new_folder)

            move_result = move_file_with_retries(
                source_file=source_path,
                destination_folders=self.destination_folders,
                extension_lookup=self.extension_lookup,
                stats_file=self.stats_file,
                history_file=self.history_file,
                hash_db_file=self.hash_db_file,
                archive_by_date=self.archive_by_date,
                rules=self.rules,
                organized_root=self.organized_root,
                retries=8,
                delay=2,
                classification_method=classification_method,
                smart_source=smart_source,
                category_override=final_category,
                journal=self.operation_journal,
            )
            status = {
                MoveStatus.MOVED: "moved",
                MoveStatus.DUPLICATE: "duplicate",
                MoveStatus.HASH_CHECK_FAILED: "hash_check_failed",
                MoveStatus.MOVE_FAILED: "failed",
            }[move_result.status]

        except Exception as error:
            logging.error(f"Error processing {file_path}: {error}", exc_info=True)
            status = "error"
            notify_callback = True

        finally:
            # أبلغ الـ GUI لحظياً بانتهاء المعالجة
            if notify_callback and self.file_processed_callback is not None:
                try:
                    self.file_processed_callback(
                        Path(file_path).name,
                        final_category or "unknown",
                        status,
                    )
                except Exception:
                    logging.error(
                        f"File processed callback failed for {file_path}",
                        exc_info=True,
                    )

        return _ProcessingResult.COMPLETED


class FileMonitor:
    # Two workers permit useful parallelism without overwhelming disks or movers.
    DEFAULT_PROCESSING_WORKERS = 2
    DEFAULT_STABILITY_RETRY_DELAY_SECONDS = 2.0
    DEFAULT_STABILITY_MAX_RETRIES = 3

    def __init__(self, config: dict, extension_lookup: dict, plugin_manager=None,
                  file_processed_callback=None,
                  max_processing_workers=None, operation_journal=None):
        self.config = config
        self.extension_lookup = extension_lookup
        self.source_folder = config["source_folder"]
        self.organized_root = get_organized_root(config)
        validate_watch_root(self.source_folder, self.organized_root)
        if max_processing_workers is None:
            max_processing_workers = config.get(
                "processing_max_workers", self.DEFAULT_PROCESSING_WORKERS
            )
        try:
            self.max_processing_workers = max(int(max_processing_workers), 1)
        except (TypeError, ValueError):
            self.max_processing_workers = self.DEFAULT_PROCESSING_WORKERS
        try:
            self.duplicate_event_window_seconds = max(
                float(config.get("duplicate_event_window_seconds", 3)), 0.0
            )
        except (TypeError, ValueError):
            self.duplicate_event_window_seconds = 3.0
        try:
            retry_delay = float(config.get(
                "processing_stability_retry_delay_seconds",
                self.DEFAULT_STABILITY_RETRY_DELAY_SECONDS,
            ))
            if (not math.isfinite(retry_delay)
                    or retry_delay < 0
                    or retry_delay > threading.TIMEOUT_MAX):
                raise ValueError
            self.stability_retry_delay_seconds = retry_delay
        except (TypeError, ValueError):
            self.stability_retry_delay_seconds = (
                self.DEFAULT_STABILITY_RETRY_DELAY_SECONDS
            )
        try:
            retry_limit = int(config.get(
                "processing_stability_max_retries",
                self.DEFAULT_STABILITY_MAX_RETRIES,
            ))
            if retry_limit < 0:
                raise ValueError
            self.stability_max_retries = retry_limit
        except (TypeError, ValueError, OverflowError):
            self.stability_max_retries = self.DEFAULT_STABILITY_MAX_RETRIES
        self._condition = threading.Condition()
        self._state = "stopped"
        self._accepting_submissions = False
        self._generation = 0
        self._stop_event = None
        self._executor = None
        self._retry_thread = None
        self._worker_local = threading.local()
        self._pending_paths: set[str] = set()
        self._recent_submissions: dict[
            str, tuple[float, tuple[int, int] | None]
        ] = {}
        self._retry_jobs: dict[str, _RetryJob] = {}
        self._next_event_cleanup_at = 0.0
        self._futures: set[Future] = set()
        self.event_handler = NewFileHandler(
            config, extension_lookup, plugin_manager,
            file_processed_callback=file_processed_callback,
            submit_file=self.submit,
            operation_journal=operation_journal,
        )
        self.observer = None
        self.is_running = False

    def set_file_processed_callback(self, callback) -> None:
        """ضبط الـ callback بعد الإنشاء (مفيد عند reload)."""
        self.event_handler.file_processed_callback = callback

    def owns_current_processing_thread(self) -> bool:
        return bool(getattr(self._worker_local, "active", False))

    def scan_existing_files(self) -> None:
        source_folder = Path(self.source_folder)
        if not source_folder.exists():
            return
        logging.info("Scanning existing files in incoming folder...")
        try:
            for item in source_folder.iterdir():
                if item.is_file():
                    self.submit(str(item), "startup_scan")
        except OSError as error:
            logging.error(f"Failed to scan {source_folder}: {error}", exc_info=True)

    @staticmethod
    def _path_key(file_path: str) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(file_path)))

    @staticmethod
    def _file_identity(file_path: str | Path) -> tuple[int, int] | None:
        try:
            source_path = Path(file_path)
            if not source_path.is_file():
                return None
            stat = source_path.stat()
            return stat.st_dev, stat.st_ino
        except OSError:
            return None

    def submit(self, file_path: str, source_event: str, *,
               _retry_number=0, _expected_identity=None,
               _generation=None, _bypass_recent=False,
               _retry_promotion=False) -> bool:
        if self.event_handler._is_organized_output(file_path):
            return False
        key = self._path_key(file_path)
        current_identity = self._file_identity(file_path)
        with self._condition:
            executor = self._executor
            stop_event = self._stop_event
            if not self._accepting_submissions or executor is None:
                return False
            generation = self._generation
            if _generation is not None and _generation != generation:
                return False
            if key in self._pending_paths:
                return False

            retry_job = self._retry_jobs.get(key)
            if retry_job is not None:
                same_file = (
                    retry_job.identity is None
                    or current_identity is None
                    or retry_job.identity == current_identity
                )
                if not _retry_promotion and same_file:
                    return False
                self._retry_jobs.pop(key, None)
                if not _retry_promotion:
                    _bypass_recent = True

            submitted_at = time.monotonic()
            previous_submission = self._recent_submissions.get(key)
            if (previous_submission is not None
                    and submitted_at - previous_submission[0]
                    < self.duplicate_event_window_seconds
                    and previous_submission[1] == current_identity
                    and not _bypass_recent):
                return False

            if submitted_at >= self._next_event_cleanup_at:
                cutoff = submitted_at - self.duplicate_event_window_seconds
                expired_paths = [
                    path for path, submission in self._recent_submissions.items()
                    if path not in self._pending_paths and submission[0] <= cutoff
                ]
                for path in expired_paths:
                    del self._recent_submissions[path]
                self._next_event_cleanup_at = (
                    submitted_at + max(self.duplicate_event_window_seconds, 1.0)
                )

            self._pending_paths.add(key)
            self._recent_submissions[key] = submitted_at, current_identity
            try:
                future = executor.submit(
                    self._run_processing,
                    file_path,
                    source_event,
                    key,
                    stop_event,
                    generation,
                    _retry_number,
                    _expected_identity or current_identity,
                )
            except Exception:
                self._pending_paths.discard(key)
                self._recent_submissions.pop(key, None)
                logging.error(f"Failed to submit {file_path} for processing", exc_info=True)
                return False

            self._futures.add(future)
            future.add_done_callback(self._future_completed)
            return True

    def _run_processing(self, file_path: str, source_event: str, key: str,
                         stop_event, generation: int, retry_number: int,
                         expected_identity) -> None:
        self._worker_local.active = True
        try:
            result = self.event_handler._process_file_thread(
                file_path,
                source_event,
                stop_event,
            )
            if result is _ProcessingResult.RETRY_WHEN_STABLE:
                self._schedule_stability_retry(
                    file_path,
                    key,
                    generation,
                    retry_number,
                    expected_identity,
                    stop_event,
                )
        finally:
            self._worker_local.active = False
            with self._condition:
                self._pending_paths.discard(key)
                self._condition.notify_all()

    def _schedule_stability_retry(self, file_path: str, key: str,
                                  generation: int, retry_number: int,
                                  expected_identity, stop_event) -> None:
        current_identity = self._file_identity(file_path)
        if current_identity is None:
            return
        replaced = (
            expected_identity is not None
            and current_identity != expected_identity
        )
        if not replaced and retry_number >= self.stability_max_retries:
            logging.warning(
                f"Stability retry limit reached; leaving file in place: {file_path}"
            )
            return

        next_retry_number = 0 if replaced else retry_number + 1

        job = _RetryJob(
            file_path=file_path,
            deadline=time.monotonic() + self.stability_retry_delay_seconds,
            retry_number=next_retry_number,
            identity=current_identity,
            generation=generation,
        )
        with self._condition:
            if (not self._accepting_submissions
                    or self._generation != generation
                    or self._stop_event is not stop_event
                    or stop_event.is_set()):
                return
            self._retry_jobs[key] = job
            self._condition.notify_all()

    def _run_retry_scheduler(self, generation: int, stop_event) -> None:
        while True:
            with self._condition:
                while True:
                    if (stop_event.is_set()
                            or self._generation != generation
                            or not self._accepting_submissions):
                        return
                    available = [
                        (key, job)
                        for key, job in self._retry_jobs.items()
                        if (job.generation == generation
                            and key not in self._pending_paths)
                    ]
                    if not available:
                        self._condition.wait()
                        continue
                    key, job = min(
                        available,
                        key=lambda item: item[1].deadline,
                    )
                    remaining = job.deadline - time.monotonic()
                    if remaining > 0:
                        self._condition.wait(remaining)
                        continue
                    break

            current_identity = self._file_identity(job.file_path)
            if current_identity is None:
                with self._condition:
                    if self._retry_jobs.get(key) is job:
                        del self._retry_jobs[key]
                    self._condition.notify_all()
                continue
            retry_number = job.retry_number
            if job.identity is not None and current_identity != job.identity:
                retry_number = 0
            try:
                submitted = self.submit(
                    job.file_path,
                    "stability_retry",
                    _retry_number=retry_number,
                    _expected_identity=current_identity,
                    _generation=generation,
                    _bypass_recent=True,
                    _retry_promotion=True,
                )
                if not submitted:
                    with self._condition:
                        if self._retry_jobs.get(key) is job:
                            del self._retry_jobs[key]
                        self._condition.notify_all()
            except Exception:
                logging.error(
                    f"Failed to promote stability retry for {job.file_path}",
                    exc_info=True,
                )
                with self._condition:
                    if self._retry_jobs.get(key) is job:
                        del self._retry_jobs[key]
                    self._condition.notify_all()

    def _future_completed(self, future: Future) -> None:
        if not future.cancelled():
            error = future.exception()
            if error is not None:
                logging.error(
                    "Unhandled file processing error",
                    exc_info=(type(error), error, error.__traceback__),
                )
        with self._condition:
            self._futures.discard(future)
            self._condition.notify_all()

    def start(self) -> None:
        if self.owns_current_processing_thread():
            raise RuntimeError("A processing callback cannot start its FileMonitor")
        with self._condition:
            while self._state in {"starting", "stopping"}:
                self._condition.wait()
            if self._state == "running":
                return
            if self._state == "failed":
                raise RuntimeError("FileMonitor shutdown previously failed")
            self._state = "starting"
            self._generation += 1
            generation = self._generation

        stop_event = threading.Event()
        executor = None
        observer = None
        retry_thread = None
        try:
            executor = ThreadPoolExecutor(
                max_workers=self.max_processing_workers,
                thread_name_prefix=f"filepilot-{id(self):x}-g{generation}",
            )
            observer = Observer()
            retry_thread = threading.Thread(
                target=self._run_retry_scheduler,
                args=(generation, stop_event),
                name=f"filepilot-retry-{id(self):x}-g{generation}",
            )
            with self._condition:
                self._stop_event = stop_event
                self._executor = executor
                self.observer = observer
                self._retry_thread = retry_thread
                self._accepting_submissions = True

            retry_thread.start()
            observer.schedule(self.event_handler, self.source_folder, recursive=False)
            observer.start()
            self.scan_existing_files()
        except Exception:
            with self._condition:
                self._accepting_submissions = False
                stop_event.set()
                self._retry_jobs.clear()
                self._condition.notify_all()
            if observer is not None:
                try:
                    observer.stop()
                    observer.join()
                except Exception:
                    pass
            if retry_thread is not None and retry_thread.is_alive():
                retry_thread.join()
            if executor is not None:
                executor.shutdown(wait=True)
            with self._condition:
                self._executor = None
                self._stop_event = None
                self._retry_thread = None
                self.observer = None
                self._pending_paths.clear()
                self._recent_submissions.clear()
                self._retry_jobs.clear()
                self._next_event_cleanup_at = 0.0
                self._futures.clear()
                self._state = "stopped"
                self.is_running = False
                self._condition.notify_all()
            raise

        with self._condition:
            self._state = "running"
            self.is_running = True
            self._condition.notify_all()
        logging.info(f"Monitoring started: {self.source_folder}")

    def stop(self) -> None:
        if self.owns_current_processing_thread():
            raise RuntimeError("A processing callback cannot stop its FileMonitor")
        with self._condition:
            while self._state == "starting":
                self._condition.wait()
            if self._state == "stopping":
                while self._state == "stopping":
                    self._condition.wait()
                return
            if self._state == "stopped":
                self.is_running = False
                return

            self._state = "stopping"
            self._accepting_submissions = False
            observer = self.observer
            executor = self._executor
            stop_event = self._stop_event
            retry_thread = self._retry_thread
            if stop_event is not None:
                stop_event.set()
            self._retry_jobs.clear()
            self._condition.notify_all()

        errors = []
        if observer is not None:
            try:
                observer.stop()
            except Exception as error:
                errors.append(error)
                logging.error("Failed while stopping file observer", exc_info=True)
            try:
                observer.join()
            except Exception as error:
                errors.append(error)
                logging.error("Failed while joining file observer", exc_info=True)

        try:
            if retry_thread is not None:
                retry_thread.join()
        except Exception as error:
            errors.append(error)
            logging.error("Failed while joining stability retry scheduler", exc_info=True)

        try:
            if executor is not None:
                executor.shutdown(wait=True)
        except Exception as error:
            errors.append(error)
            logging.error("Failed while draining file processing", exc_info=True)
        finally:
            with self._condition:
                if errors:
                    self._state = "failed"
                else:
                    if self.observer is observer:
                        self.observer = None
                    if self._executor is executor:
                        self._executor = None
                    if self._stop_event is stop_event:
                        self._stop_event = None
                    if self._retry_thread is retry_thread:
                        self._retry_thread = None
                    self._pending_paths.clear()
                    self._recent_submissions.clear()
                    self._retry_jobs.clear()
                    self._next_event_cleanup_at = 0.0
                    self._futures.clear()
                    self._state = "stopped"
                self.is_running = False
                self._condition.notify_all()

        if errors:
            raise RuntimeError("FileMonitor did not stop cleanly") from errors[0]
        logging.info("Monitoring stopped.")
