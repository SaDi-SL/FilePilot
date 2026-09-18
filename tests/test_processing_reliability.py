import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.gui_monitoring import MonitoringMixin
from app.mover import MoveResult, MoveStatus
from app.multi_watcher import MultiFolderMonitor
from app.watcher import FileMonitor, NewFileHandler


class ThreadDouble:
    def __init__(self, target=None, args=(), daemon=None, **_kwargs):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True


class ObserverDouble:
    """Model the non-restartable lifecycle of watchdog's thread-based observer."""

    def __init__(self):
        self.schedule_calls = []
        self.start_calls = 0
        self.stop_calls = 0
        self.join_calls = 0
        self._started_once = False

    def schedule(self, handler, path, recursive=False):
        self.schedule_calls.append((handler, path, recursive))

    def start(self):
        self.start_calls += 1
        if self._started_once:
            raise RuntimeError("threads can only be started once")
        self._started_once = True

    def stop(self):
        self.stop_calls += 1

    def join(self):
        self.join_calls += 1


class ProcessingReliabilityCharacterizationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.incoming = self.root / "incoming"
        self.incoming.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _config(self, processing_wait_seconds=0):
        return {
            "source_folder": str(self.incoming),
            "destination_folders": {
                "documents": str(self.root / "documents"),
                "others": str(self.root / "others"),
            },
            "rules": {"documents": [".txt"]},
            "processing_wait_seconds": processing_wait_seconds,
            "duplicate_event_window_seconds": 30,
            "archive_by_date": False,
            "stats_file": str(self.root / "stats.json"),
            "history_file": str(self.root / "history.csv"),
            "hash_db_file": str(self.root / "hashes.json"),
        }

    def _multi_config(self, folders):
        config = self._config()
        config["watch_folders"] = folders
        return config

    @staticmethod
    def _event(path, is_directory=False):
        return SimpleNamespace(is_directory=is_directory, src_path=str(path))

    @staticmethod
    def _moved(source):
        return MoveResult(
            MoveStatus.MOVED,
            Path(source),
            destination=Path(source).parent / "moved" / Path(source).name,
        )

    def test_current_directory_created_event_is_ignored(self):
        handler = NewFileHandler(self._config(), {".txt": "documents"})

        with patch("app.watcher.threading.Thread") as thread_type:
            handler.on_created(self._event(self.incoming, is_directory=True))

        thread_type.assert_not_called()

    def test_current_created_event_spawns_untracked_daemon_processing_thread(self):
        """Current event workers are daemonized and not retained for shutdown."""
        source = self.incoming / "one.txt"
        source.write_text("one", encoding="utf-8")
        handler = NewFileHandler(self._config(), {".txt": "documents"})
        created_threads = []

        def make_thread(**kwargs):
            thread = ThreadDouble(**kwargs)
            created_threads.append(thread)
            return thread

        with patch("app.watcher.threading.Thread", side_effect=make_thread):
            handler.on_created(self._event(source))

        self.assertEqual(len(created_threads), 1)
        worker = created_threads[0]
        self.assertTrue(worker.started)
        self.assertTrue(worker.daemon)
        self.assertEqual(worker.target, handler._process_file_thread)
        self.assertEqual(worker.args, (str(source), "created"))
        self.assertNotIn(worker, handler.__dict__.values())

    def test_current_each_created_event_spawns_an_independent_thread(self):
        handler = NewFileHandler(self._config(), {".txt": "documents"})
        created_threads = []

        def make_thread(**kwargs):
            thread = ThreadDouble(**kwargs)
            created_threads.append(thread)
            return thread

        with patch("app.watcher.threading.Thread", side_effect=make_thread):
            for index in range(4):
                handler.on_created(self._event(self.incoming / f"file-{index}.txt"))

        self.assertEqual(len(created_threads), 4)
        self.assertTrue(all(thread.started for thread in created_threads))
        self.assertTrue(all(thread.daemon for thread in created_threads))
        self.assertEqual(
            [thread.args[1] for thread in created_threads],
            ["created"] * 4,
        )

    def test_current_startup_scan_uses_one_untracked_daemon_thread(self):
        """Startup scan has separate ownership and processes its files serially."""
        first = self.incoming / "first.txt"
        second = self.incoming / "second.txt"
        first.write_text("first", encoding="utf-8")
        second.write_text("second", encoding="utf-8")

        with patch("app.watcher.Observer", ObserverDouble):
            monitor = FileMonitor(self._config(), {".txt": "documents"})
        monitor.event_handler._process_file_thread = MagicMock()
        created_threads = []

        def make_thread(**kwargs):
            thread = ThreadDouble(**kwargs)
            created_threads.append(thread)
            return thread

        with patch("app.watcher.threading.Thread", side_effect=make_thread):
            monitor.scan_existing_files()

        self.assertEqual(len(created_threads), 1)
        scan_thread = created_threads[0]
        self.assertTrue(scan_thread.started)
        self.assertTrue(scan_thread.daemon)
        self.assertNotIn(scan_thread, monitor.__dict__.values())

        scan_thread.target()

        self.assertEqual(monitor.event_handler._process_file_thread.call_count, 2)
        processed = {
            (Path(call.args[0]), call.args[1])
            for call in monitor.event_handler._process_file_thread.call_args_list
        }
        self.assertEqual(processed, {(first, "startup_scan"), (second, "startup_scan")})

    def test_current_repeated_same_path_events_spawn_workers_but_one_is_suppressed(self):
        """Suppression occurs inside workers, so every event still produces a callback."""
        source = self.incoming / "repeated.txt"
        source.write_text("content", encoding="utf-8")
        callbacks = []
        callback_lock = threading.Lock()

        def callback(*args):
            with callback_lock:
                callbacks.append(args)

        handler = NewFileHandler(
            self._config(),
            {".txt": "documents"},
            file_processed_callback=callback,
        )
        real_thread = threading.Thread
        workers = []

        def make_thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            workers.append(thread)
            return thread

        with (
            patch("app.watcher.threading.Thread", side_effect=make_thread),
            patch("app.watcher.time.time", return_value=100.0),
            patch("app.watcher.time.sleep"),
            patch("app.watcher.smart_classify", return_value=None),
            patch(
                "app.watcher.move_file_with_retries",
                return_value=self._moved(source),
            ) as move_file,
        ):
            handler.on_created(self._event(source))
            handler.on_created(self._event(source))
            for worker in workers:
                worker.join(timeout=5)

        self.assertEqual(len(workers), 2)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        move_file.assert_called_once()
        self.assertCountEqual(
            callbacks,
            [
                (source.name, "documents", "moved"),
                (source.name, "unknown", "unknown"),
            ],
        )

    def test_current_different_files_process_concurrently_and_callback_on_workers(self):
        first = self.incoming / "first.txt"
        second = self.incoming / "second.txt"
        first.write_text("first", encoding="utf-8")
        second.write_text("second", encoding="utf-8")
        rendezvous = threading.Barrier(3)
        callback_threads = {}
        mover_threads = {}
        result_lock = threading.Lock()
        main_thread_id = threading.get_ident()

        def move(**kwargs):
            source = kwargs["source_file"]
            with result_lock:
                mover_threads[source.name] = threading.get_ident()
            rendezvous.wait(timeout=5)
            return self._moved(source)

        def callback(filename, _category, _status):
            with result_lock:
                callback_threads[filename] = threading.get_ident()

        handler = NewFileHandler(
            self._config(),
            {".txt": "documents"},
            file_processed_callback=callback,
        )
        real_thread = threading.Thread
        workers = []

        def make_thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            workers.append(thread)
            return thread

        try:
            with (
                patch("app.watcher.threading.Thread", side_effect=make_thread),
                patch("app.watcher.time.sleep"),
                patch("app.watcher.smart_classify", return_value=None),
                patch("app.watcher.move_file_with_retries", side_effect=move),
            ):
                handler.on_created(self._event(first))
                handler.on_created(self._event(second))
                rendezvous.wait(timeout=5)
                for worker in workers:
                    worker.join(timeout=5)
        finally:
            rendezvous.abort()
            for worker in workers:
                worker.join(timeout=5)

        self.assertEqual(len(workers), 2)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(set(mover_threads), {first.name, second.name})
        self.assertEqual(set(callback_threads), {first.name, second.name})
        self.assertEqual(callback_threads, mover_threads)
        self.assertNotIn(main_thread_id, callback_threads.values())
        self.assertEqual(len(set(mover_threads.values())), 2)

    def test_current_fixed_delay_allows_file_to_change_after_processing_begins(self):
        """There is no size/mtime stability protocol after the one fixed delay."""
        source = self.incoming / "still-writing.txt"
        source.write_text("part-one", encoding="utf-8")
        delay_completed = threading.Event()
        classification_started = threading.Event()
        release_classification = threading.Event()
        mover_content = []
        callbacks = []

        def fixed_delay(seconds):
            self.assertEqual(seconds, 7)
            delay_completed.set()

        def blocked_classification(_source):
            classification_started.set()
            if not release_classification.wait(timeout=5):
                raise TimeoutError("classification was not released")
            return None

        def move(**kwargs):
            mover_content.append(kwargs["source_file"].read_text(encoding="utf-8"))
            return self._moved(kwargs["source_file"])

        handler = NewFileHandler(
            self._config(processing_wait_seconds=7),
            {".txt": "documents"},
            file_processed_callback=lambda *args: callbacks.append(args),
        )
        worker = threading.Thread(
            target=handler._process_file_thread,
            args=(str(source), "created"),
        )

        try:
            with (
                patch("app.watcher.time.sleep", side_effect=fixed_delay) as sleep_call,
                patch("app.watcher.smart_classify", side_effect=blocked_classification),
                patch("app.watcher.move_file_with_retries", side_effect=move),
            ):
                worker.start()
                self.assertTrue(delay_completed.wait(timeout=5))
                self.assertTrue(classification_started.wait(timeout=5))
                source.write_text("part-one-part-two", encoding="utf-8")
                release_classification.set()
                worker.join(timeout=5)
        finally:
            release_classification.set()
            worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        sleep_call.assert_called_once_with(7)
        self.assertEqual(mover_content, ["part-one-part-two"])
        self.assertEqual(callbacks, [(source.name, "documents", "moved")])

    def test_current_stop_waits_for_observer_but_not_active_processing_worker(self):
        """A worker can continue and invoke its callback after stop() returns."""
        source = self.incoming / "blocked.txt"
        source.write_text("blocked", encoding="utf-8")
        worker_entered = threading.Event()
        release_worker = threading.Event()
        callback_done = threading.Event()
        callbacks = []

        def blocked_move(**kwargs):
            worker_entered.set()
            if not release_worker.wait(timeout=5):
                raise TimeoutError("worker was not released")
            return self._moved(kwargs["source_file"])

        def callback(*args):
            callbacks.append(args)
            callback_done.set()

        with patch("app.watcher.Observer", ObserverDouble):
            monitor = FileMonitor(
                self._config(),
                {".txt": "documents"},
                file_processed_callback=callback,
            )
        monitor.is_running = True
        real_thread = threading.Thread
        workers = []

        def make_thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            workers.append(thread)
            return thread

        try:
            with (
                patch("app.watcher.threading.Thread", side_effect=make_thread),
                patch("app.watcher.time.sleep"),
                patch("app.watcher.smart_classify", return_value=None),
                patch("app.watcher.move_file_with_retries", side_effect=blocked_move),
            ):
                monitor.event_handler.on_created(self._event(source))
                self.assertTrue(worker_entered.wait(timeout=5))

                monitor.stop()

                self.assertFalse(monitor.is_running)
                self.assertEqual(monitor.observer.stop_calls, 1)
                self.assertEqual(monitor.observer.join_calls, 1)
                self.assertEqual(len(workers), 1)
                self.assertTrue(workers[0].is_alive())
                self.assertFalse(callback_done.is_set())
                self.assertNotIn(workers[0], monitor.__dict__.values())

                release_worker.set()
                workers[0].join(timeout=5)
        finally:
            release_worker.set()
            for worker in workers:
                worker.join(timeout=5)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertTrue(callback_done.is_set())
        self.assertEqual(callbacks, [(source.name, "documents", "moved")])

    def test_current_stop_then_start_reuses_non_restartable_observer(self):
        """The retained observer rejects a second start after it has been joined."""
        with patch("app.watcher.Observer", ObserverDouble):
            monitor = FileMonitor(self._config(), {".txt": "documents"})

        with patch.object(monitor, "scan_existing_files") as scan_existing:
            monitor.start()
            monitor.stop()
            with self.assertRaisesRegex(RuntimeError, "threads can only be started once"):
                monitor.start()

        self.assertFalse(monitor.is_running)
        self.assertEqual(scan_existing.call_count, 2)
        self.assertEqual(monitor.observer.start_calls, 2)
        self.assertEqual(monitor.observer.stop_calls, 1)
        self.assertEqual(monitor.observer.join_calls, 1)
        self.assertEqual(len(monitor.observer.schedule_calls), 2)

    def test_current_reload_can_start_new_monitor_while_old_worker_is_active(self):
        """stop_all() does not establish ownership between monitor generations."""
        source = self.incoming / "old-generation.txt"
        source.write_text("old", encoding="utf-8")
        old_worker_entered = threading.Event()
        release_old_worker = threading.Event()
        old_callback_done = threading.Event()

        def blocked_move(**kwargs):
            old_worker_entered.set()
            if not release_old_worker.wait(timeout=5):
                raise TimeoutError("old worker was not released")
            return self._moved(kwargs["source_file"])

        old_config = self._multi_config([
            {"path": str(self.incoming), "label": "Incoming", "active": True}
        ])
        new_config = self._multi_config([
            {"path": str(self.incoming), "label": "Reloaded", "active": True}
        ])
        real_thread = threading.Thread
        workers = []

        def make_thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            workers.append(thread)
            return thread

        try:
            with (
                patch("app.watcher.Observer", ObserverDouble),
                patch("app.watcher.threading.Thread", side_effect=make_thread),
                patch("app.watcher.time.sleep"),
                patch("app.watcher.smart_classify", return_value=None),
                patch("app.watcher.move_file_with_retries", side_effect=blocked_move),
                patch.object(FileMonitor, "scan_existing_files"),
            ):
                monitor = MultiFolderMonitor(
                    old_config,
                    {".txt": "documents"},
                    file_processed_callback=lambda *_args: old_callback_done.set(),
                )
                old_folder_monitor = next(iter(monitor._monitors.values()))
                old_folder_monitor.is_running = True
                old_folder_monitor.event_handler.on_created(self._event(source))
                self.assertTrue(old_worker_entered.wait(timeout=5))

                monitor.reload_config(new_config, {".txt": "documents"})
                new_folder_monitor = next(iter(monitor._monitors.values()))
                monitor.start_all()

                self.assertIsNot(new_folder_monitor, old_folder_monitor)
                self.assertTrue(new_folder_monitor.is_running)
                self.assertFalse(old_folder_monitor.is_running)
                self.assertEqual(old_folder_monitor.observer.join_calls, 1)
                self.assertEqual(len(workers), 1)
                self.assertTrue(workers[0].is_alive())
                self.assertFalse(old_callback_done.is_set())

                release_old_worker.set()
                workers[0].join(timeout=5)
        finally:
            release_old_worker.set()
            for worker in workers:
                worker.join(timeout=5)

        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertTrue(old_callback_done.is_set())

    def test_current_multi_folder_monitor_builds_per_folder_shallow_views(self):
        """Nested runtime dictionaries are shared by every folder monitor."""
        first = self.root / "first-watch"
        second = self.root / "second-watch"
        first.mkdir()
        second.mkdir()
        config = self._multi_config([
            {"path": str(first), "label": "First", "active": True},
            {"path": str(second), "label": "Second", "active": False},
        ])
        callback = object()
        created_monitors = []

        class MonitorDouble:
            def __init__(self, config, extension_lookup, plugin_manager=None,
                         file_processed_callback=None):
                self.config = config
                self.extension_lookup = extension_lookup
                self.plugin_manager = plugin_manager
                self.callback = file_processed_callback
                self.is_running = False
                self.start_calls = 0
                self.stop_calls = 0
                created_monitors.append(self)

            def start(self):
                self.start_calls += 1
                self.is_running = True

            def stop(self):
                self.stop_calls += 1
                self.is_running = False

            def set_file_processed_callback(self, new_callback):
                self.callback = new_callback

        with patch("app.multi_watcher.FileMonitor", MonitorDouble):
            monitor = MultiFolderMonitor(
                config,
                {".txt": "documents"},
                file_processed_callback=callback,
            )
            monitor.start_all()
            monitor.stop_all()

        self.assertEqual(len(created_monitors), 2)
        by_path = {item.config["source_folder"]: item for item in created_monitors}
        first_monitor = by_path[str(first.resolve())]
        second_monitor = by_path[str(second.resolve())]
        self.assertEqual(first_monitor.start_calls, 1)
        self.assertEqual(first_monitor.stop_calls, 1)
        self.assertEqual(second_monitor.start_calls, 0)
        self.assertEqual(second_monitor.stop_calls, 0)
        self.assertIs(first_monitor.config["rules"], config["rules"])
        self.assertIs(second_monitor.config["rules"], config["rules"])
        self.assertIs(
            first_monitor.config["destination_folders"],
            second_monitor.config["destination_folders"],
        )
        self.assertIs(first_monitor.callback, callback)
        self.assertIs(second_monitor.callback, callback)

    def test_current_classification_exception_is_logged_and_reported_only_by_callback(self):
        source = self.incoming / "classification-error.txt"
        source.write_text("content", encoding="utf-8")
        callback = MagicMock()
        handler = NewFileHandler(
            self._config(),
            {".txt": "documents"},
            file_processed_callback=callback,
        )

        with (
            patch("app.watcher.time.sleep"),
            patch(
                "app.watcher.smart_classify",
                side_effect=RuntimeError("classification failed"),
            ),
            patch("app.watcher.move_file_with_retries") as move_file,
            patch("app.watcher.logging.error") as log_error,
        ):
            handler._process_file_thread(str(source), "created")

        move_file.assert_not_called()
        callback.assert_called_once_with(source.name, "unknown", "error")
        self.assertEqual(handler.last_processed_file, source.name)
        self.assertEqual(log_error.call_count, 1)
        self.assertTrue(log_error.call_args.kwargs["exc_info"])

    def test_current_unexpected_mover_exception_is_logged_and_reported_as_error(self):
        source = self.incoming / "move-error.txt"
        source.write_text("content", encoding="utf-8")
        callback = MagicMock()
        handler = NewFileHandler(
            self._config(),
            {".txt": "documents"},
            file_processed_callback=callback,
        )

        with (
            patch("app.watcher.time.sleep"),
            patch("app.watcher.smart_classify", return_value=None),
            patch(
                "app.watcher.move_file_with_retries",
                side_effect=RuntimeError("move exploded"),
            ),
            patch("app.watcher.logging.error") as log_error,
        ):
            handler._process_file_thread(str(source), "created")

        callback.assert_called_once_with(source.name, "documents", "error")
        self.assertEqual(log_error.call_count, 1)
        self.assertTrue(log_error.call_args.kwargs["exc_info"])

    def test_current_callback_exception_is_silently_swallowed(self):
        source = self.incoming / "callback-error.txt"
        source.write_text("content", encoding="utf-8")
        callback = MagicMock(side_effect=RuntimeError("callback failed"))
        handler = NewFileHandler(
            self._config(),
            {".txt": "documents"},
            file_processed_callback=callback,
        )

        with (
            patch("app.watcher.time.sleep"),
            patch("app.watcher.smart_classify", return_value=None),
            patch(
                "app.watcher.move_file_with_retries",
                return_value=self._moved(source),
            ),
            patch("app.watcher.logging.error") as log_error,
        ):
            handler._process_file_thread(str(source), "created")

        callback.assert_called_once_with(source.name, "documents", "moved")
        log_error.assert_not_called()

    def test_current_gui_exit_swallows_missing_multi_monitor_stop(self):
        """GUI exit destroys the window without calling MultiFolderMonitor.stop_all()."""

        class RunningMultiMonitor:
            def __init__(self):
                self.is_running = True
                self.stop_all_calls = 0

            def stop_all(self):
                self.stop_all_calls += 1
                self.is_running = False

        gui = object.__new__(MonitoringMixin)
        gui.monitor = RunningMultiMonitor()
        gui._stop_dot_pulse = MagicMock()
        gui._stop_auto_refresh = MagicMock()
        gui.tray_icon = None
        gui.close_logs_viewer = MagicMock()
        gui.root = MagicMock()

        MonitoringMixin.exit_application(gui)

        self.assertTrue(gui.monitor.is_running)
        self.assertEqual(gui.monitor.stop_all_calls, 0)
        gui.root.destroy.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
