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


class ObserverDouble:
    instances = []

    def __init__(self):
        self.schedule_calls = []
        self.start_calls = 0
        self.stop_calls = 0
        self.join_calls = 0
        self.stopped = threading.Event()
        self.__class__.instances.append(self)

    def schedule(self, handler, path, recursive=False):
        self.schedule_calls.append((handler, path, recursive))

    def start(self):
        self.start_calls += 1

    def stop(self):
        self.stop_calls += 1
        self.stopped.set()

    def join(self):
        self.join_calls += 1


class ProcessingReliabilityTests(unittest.TestCase):
    def setUp(self):
        ObserverDouble.instances = []
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.incoming = self.root / "incoming"
        self.incoming.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _config(self, processing_wait_seconds=0, **overrides):
        config = {
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
        config.update(overrides)
        return config

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

    def _start_monitor(self, callback=None, max_workers=2):
        monitor = FileMonitor(
            self._config(),
            {".txt": "documents"},
            file_processed_callback=callback,
            max_processing_workers=max_workers,
        )
        with (
            patch("app.watcher.Observer", ObserverDouble),
            patch.object(monitor, "scan_existing_files"),
        ):
            monitor.start()
        self.addCleanup(monitor.stop)
        return monitor

    def _wait_for_idle(self, monitor):
        with monitor._condition:
            completed = monitor._condition.wait_for(
                lambda: not monitor._pending_paths and not monitor._futures,
                timeout=5,
            )
        self.assertTrue(completed, "processing did not become idle")

    def test_directory_created_event_is_ignored(self):
        submit = MagicMock()
        handler = NewFileHandler(
            self._config(),
            {".txt": "documents"},
            submit_file=submit,
        )

        handler.on_created(self._event(self.incoming, is_directory=True))

        submit.assert_not_called()

    def test_created_event_submits_without_ad_hoc_thread(self):
        source = self.incoming / "one.txt"
        submit = MagicMock()
        handler = NewFileHandler(
            self._config(),
            {".txt": "documents"},
            submit_file=submit,
        )

        with patch("app.watcher.threading.Thread") as thread_type:
            handler.on_created(self._event(source))

        submit.assert_called_once_with(str(source), "created")
        thread_type.assert_not_called()

    def test_rapid_events_use_bounded_owned_workers(self):
        monitor = self._start_monitor(max_workers=2)
        release = threading.Event()
        two_started = threading.Event()
        lock = threading.Lock()
        started = []
        active = 0
        maximum_active = 0

        def process(file_path, _source_event, _stop_event):
            nonlocal active, maximum_active
            with lock:
                started.append(Path(file_path).name)
                active += 1
                maximum_active = max(maximum_active, active)
                if active == 2:
                    two_started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("workers were not released")
            with lock:
                active -= 1

        monitor.event_handler._process_file_thread = process

        try:
            for index in range(8):
                monitor.event_handler.on_created(
                    self._event(self.incoming / f"file-{index}.txt")
                )
            self.assertTrue(two_started.wait(timeout=5))
            with lock:
                self.assertEqual(len(started), 2)
                self.assertEqual(maximum_active, 2)
            self.assertEqual(len(monitor._pending_paths), 8)
        finally:
            release.set()
            monitor.stop()

        self.assertCountEqual(started, [f"file-{index}.txt" for index in range(8)])
        self.assertEqual(maximum_active, 2)
        self.assertIsNone(monitor._executor)
        self.assertFalse(monitor._pending_paths)
        self.assertFalse(monitor._futures)

    def test_processing_settings_use_safe_defaults_and_config_overrides(self):
        default_monitor = FileMonitor(
            self._config(),
            {".txt": "documents"},
        )
        configured_monitor = FileMonitor(
            self._config(
                processing_max_workers=4,
                processing_stability_interval_seconds=0.2,
                processing_stability_checks=3,
                processing_stability_timeout_seconds=9,
            ),
            {".txt": "documents"},
        )

        self.assertEqual(default_monitor.max_processing_workers, 2)
        self.assertEqual(configured_monitor.max_processing_workers, 4)
        self.assertEqual(
            configured_monitor.event_handler.stability_interval_seconds,
            0.2,
        )
        self.assertEqual(configured_monitor.event_handler.stable_intervals, 3)
        self.assertEqual(
            configured_monitor.event_handler.stability_timeout_seconds,
            9,
        )

    def test_same_path_events_coalesce_while_pending_or_running(self):
        monitor = self._start_monitor()
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def process(file_path, source_event, _stop_event):
            calls.append((file_path, source_event))
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("worker was not released")

        monitor.event_handler._process_file_thread = process
        source = self.incoming / "same.txt"

        try:
            monitor.event_handler.on_created(self._event(source))
            self.assertTrue(entered.wait(timeout=5))
            monitor.event_handler.on_created(self._event(source))
            monitor.event_handler.on_modified(self._event(source))
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(monitor._pending_paths), 1)
        finally:
            release.set()
            monitor.stop()

        self.assertEqual(calls, [(str(source), "created")])

    def test_different_paths_process_concurrently(self):
        monitor = self._start_monitor(max_workers=2)
        rendezvous = threading.Barrier(3)
        completed = []
        lock = threading.Lock()

        def process(file_path, _source_event, _stop_event):
            rendezvous.wait(timeout=5)
            with lock:
                completed.append(Path(file_path).name)

        monitor.event_handler._process_file_thread = process

        try:
            monitor.submit(str(self.incoming / "first.txt"), "created")
            monitor.submit(str(self.incoming / "second.txt"), "created")
            rendezvous.wait(timeout=5)
            self._wait_for_idle(monitor)
        finally:
            rendezvous.abort()
            monitor.stop()

        self.assertCountEqual(completed, ["first.txt", "second.txt"])

    def test_processing_begins_only_after_stability_is_established(self):
        source = self.incoming / "stable.txt"
        source.write_text("content", encoding="utf-8")
        handler = NewFileHandler(
            self._config(processing_wait_seconds=1),
            {".txt": "documents"},
            stability_interval_seconds=0.25,
            stable_intervals=2,
        )
        order = []

        def sample(_source):
            order.append("sample")
            return 7, 10

        def classify(_source):
            order.append("classify")
            return None

        def move(**kwargs):
            order.append("move")
            return self._moved(kwargs["source_file"])

        with (
            patch.object(handler, "_sample_file_state", side_effect=sample),
            patch("app.watcher.time.sleep") as sleep_call,
            patch("app.watcher.smart_classify", side_effect=classify),
            patch("app.watcher.move_file_with_retries", side_effect=move),
        ):
            handler._process_file_thread(str(source), "created")

        self.assertEqual(order, ["sample", "sample", "sample", "classify", "move"])
        self.assertEqual(sleep_call.call_count, 2)

    def test_changing_file_is_not_ready_after_one_wait(self):
        source = self.incoming / "changing.txt"
        source.write_text("content", encoding="utf-8")
        callback = MagicMock()
        handler = NewFileHandler(
            self._config(processing_wait_seconds=1),
            {".txt": "documents"},
            file_processed_callback=callback,
            stability_interval_seconds=0.25,
            stable_intervals=2,
        )
        states = iter([(1, 1), (2, 2), (2, 2), (3, 3), (3, 3)])

        with (
            patch.object(handler, "_sample_file_state", side_effect=states),
            patch("app.watcher.time.sleep") as sleep_call,
            patch("app.watcher.smart_classify") as classify,
            patch("app.watcher.move_file_with_retries") as move_file,
        ):
            handler._process_file_thread(str(source), "created")

        self.assertEqual(sleep_call.call_count, 4)
        classify.assert_not_called()
        move_file.assert_not_called()
        callback.assert_called_once_with(source.name, "unknown", "unknown")
        self.assertTrue(source.exists())

    def test_stability_wait_is_bounded(self):
        source = self.incoming / "never-stable.txt"
        source.write_text("content", encoding="utf-8")
        handler = NewFileHandler(
            self._config(processing_wait_seconds=1),
            {".txt": "documents"},
            stability_interval_seconds=0.25,
            stable_intervals=2,
        )
        sample_count = 0

        def changing_state(_source):
            nonlocal sample_count
            sample_count += 1
            return sample_count, sample_count

        with (
            patch.object(handler, "_sample_file_state", side_effect=changing_state),
            patch("app.watcher.time.sleep") as sleep_call,
        ):
            stable = handler._wait_until_stable(source)

        self.assertFalse(stable)
        self.assertEqual(sample_count, 5)
        self.assertEqual(sleep_call.call_count, 4)

    def test_source_disappearance_during_readiness_is_safe(self):
        source = self.incoming / "disappearing.txt"
        source.write_text("content", encoding="utf-8")
        callback = MagicMock()
        handler = NewFileHandler(
            self._config(processing_wait_seconds=1),
            {".txt": "documents"},
            file_processed_callback=callback,
            stability_interval_seconds=0.25,
        )

        with (
            patch.object(handler, "_sample_file_state", side_effect=[(1, 1), None]),
            patch("app.watcher.time.sleep"),
            patch("app.watcher.smart_classify") as classify,
            patch("app.watcher.move_file_with_retries") as move_file,
        ):
            handler._process_file_thread(str(source), "created")

        classify.assert_not_called()
        move_file.assert_not_called()
        callback.assert_called_once_with(source.name, "unknown", "unknown")

    def test_stop_waits_for_processing_and_callback(self):
        source = self.incoming / "blocked.txt"
        source.write_text("content", encoding="utf-8")
        move_entered = threading.Event()
        release_move = threading.Event()
        callback_done = threading.Event()
        stop_done = threading.Event()
        callbacks = []

        def move(**kwargs):
            move_entered.set()
            if not release_move.wait(timeout=5):
                raise TimeoutError("move was not released")
            return self._moved(kwargs["source_file"])

        def callback(*args):
            callbacks.append(args)
            callback_done.set()

        monitor = self._start_monitor(callback=callback)
        observer = monitor.observer

        with (
            patch.object(monitor.event_handler, "_wait_until_stable", return_value=True),
            patch("app.watcher.smart_classify", return_value=None),
            patch("app.watcher.move_file_with_retries", side_effect=move),
        ):
            monitor.event_handler.on_created(self._event(source))
            self.assertTrue(move_entered.wait(timeout=5))

            stop_thread = threading.Thread(
                target=lambda: (monitor.stop(), stop_done.set())
            )
            stop_thread.start()
            try:
                self.assertTrue(observer.stopped.wait(timeout=5))
                self.assertFalse(stop_done.is_set())
                self.assertTrue(monitor.is_running)
                self.assertFalse(monitor._stop_event.is_set())
                self.assertFalse(callback_done.is_set())
                self.assertFalse(monitor.submit(
                    str(self.incoming / "during-stop.txt"),
                    "created",
                ))
                release_move.set()
                self.assertTrue(stop_done.wait(timeout=5))
                stop_thread.join(timeout=5)
            finally:
                release_move.set()
                stop_thread.join(timeout=5)

        self.assertFalse(stop_thread.is_alive())
        self.assertFalse(monitor.is_running)
        self.assertTrue(callback_done.is_set())
        self.assertEqual(callbacks, [(source.name, "documents", "moved")])
        self.assertIsNone(monitor._executor)
        self.assertFalse(monitor._pending_paths)
        self.assertFalse(monitor._futures)

    def test_no_callback_or_worker_remains_after_stop_returns(self):
        source = self.incoming / "finished.txt"
        source.write_text("content", encoding="utf-8")
        callback_times = []
        monitor = self._start_monitor(
            callback=lambda *_args: callback_times.append("callback")
        )
        thread_prefix = f"filepilot-{id(monitor):x}-g1"

        with (
            patch.object(monitor.event_handler, "_wait_until_stable", return_value=True),
            patch("app.watcher.smart_classify", return_value=None),
            patch(
                "app.watcher.move_file_with_retries",
                return_value=self._moved(source),
            ),
        ):
            monitor.event_handler.on_created(self._event(source))
            monitor.stop()

        callbacks_at_return = len(callback_times)
        self.assertEqual(callbacks_at_return, 1)
        self.assertEqual(len(callback_times), callbacks_at_return)
        self.assertFalse(any(
            thread.is_alive() and thread.name.startswith(thread_prefix)
            for thread in threading.enumerate()
        ))

    def test_start_stop_start_uses_fresh_observer_and_executor(self):
        monitor = FileMonitor(self._config(), {".txt": "documents"})

        with (
            patch("app.watcher.Observer", ObserverDouble),
            patch.object(monitor, "scan_existing_files"),
        ):
            monitor.start()
            first_observer = monitor.observer
            first_executor = monitor._executor
            monitor.stop()
            monitor.start()
            second_observer = monitor.observer
            second_executor = monitor._executor
            monitor.stop()

        self.assertIsNot(first_observer, second_observer)
        self.assertIsNot(first_executor, second_executor)
        self.assertEqual(first_observer.start_calls, 1)
        self.assertEqual(first_observer.stop_calls, 1)
        self.assertEqual(first_observer.join_calls, 1)
        self.assertEqual(second_observer.start_calls, 1)
        self.assertEqual(second_observer.stop_calls, 1)
        self.assertEqual(second_observer.join_calls, 1)
        self.assertEqual(monitor._generation, 2)
        self.assertFalse(monitor.is_running)

    def test_stop_before_start_and_repeated_lifecycle_calls_are_safe(self):
        monitor = FileMonitor(self._config(), {".txt": "documents"})

        monitor.stop()
        monitor.stop()
        with (
            patch("app.watcher.Observer", ObserverDouble),
            patch.object(monitor, "scan_existing_files"),
        ):
            monitor.start()
            monitor.start()
            observer = monitor.observer
            monitor.stop()
            monitor.stop()

        self.assertEqual(observer.start_calls, 1)
        self.assertEqual(observer.stop_calls, 1)
        self.assertEqual(observer.join_calls, 1)
        self.assertFalse(monitor.is_running)

    def test_completed_stop_prevents_old_generation_overlap(self):
        monitor = self._start_monitor(max_workers=1)
        first_entered = threading.Event()
        release_first = threading.Event()
        stop_done = threading.Event()
        calls = []
        active = 0
        maximum_active = 0
        lock = threading.Lock()

        def process(file_path, _source_event, _stop_event):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
                calls.append((monitor._generation, Path(file_path).name))
            if Path(file_path).name == "old.txt":
                first_entered.set()
                if not release_first.wait(timeout=5):
                    raise TimeoutError("old generation was not released")
            with lock:
                active -= 1

        monitor.event_handler._process_file_thread = process
        monitor.submit(str(self.incoming / "old.txt"), "created")
        self.assertTrue(first_entered.wait(timeout=5))
        stop_thread = threading.Thread(
            target=lambda: (monitor.stop(), stop_done.set())
        )
        stop_thread.start()
        try:
            self.assertTrue(monitor.observer is not None)
            self.assertFalse(stop_done.is_set())
            release_first.set()
            self.assertTrue(stop_done.wait(timeout=5))
            stop_thread.join(timeout=5)

            with (
                patch("app.watcher.Observer", ObserverDouble),
                patch.object(monitor, "scan_existing_files"),
            ):
                monitor.start()
            monitor.submit(str(self.incoming / "new.txt"), "created")
            self._wait_for_idle(monitor)
        finally:
            release_first.set()
            stop_thread.join(timeout=5)
            monitor.stop()

        self.assertEqual(calls, [(1, "old.txt"), (2, "new.txt")])
        self.assertEqual(maximum_active, 1)

    def test_callback_exception_releases_path_and_worker_bookkeeping(self):
        source = self.incoming / "callback-error.txt"
        source.write_text("content", encoding="utf-8")
        callback_called = threading.Event()

        def callback(*_args):
            callback_called.set()
            raise RuntimeError("callback failed")

        monitor = self._start_monitor(callback=callback)
        with (
            patch.object(monitor.event_handler, "_wait_until_stable", return_value=True),
            patch("app.watcher.smart_classify", return_value=None),
            patch(
                "app.watcher.move_file_with_retries",
                return_value=self._moved(source),
            ),
            patch("app.watcher.logging.error") as log_error,
        ):
            monitor.event_handler.on_created(self._event(source))
            self.assertTrue(callback_called.wait(timeout=5))
            self._wait_for_idle(monitor)

        self.assertFalse(monitor._pending_paths)
        self.assertFalse(monitor._futures)
        self.assertEqual(log_error.call_count, 1)
        self.assertIn(str(source), log_error.call_args.args[0])
        self.assertTrue(log_error.call_args.kwargs["exc_info"])

    def test_processing_exception_releases_path_bookkeeping(self):
        monitor = self._start_monitor()
        processed = threading.Event()
        source = self.incoming / "processing-error.txt"

        def fail(*_args):
            processed.set()
            raise RuntimeError("processing failed")

        monitor.event_handler._process_file_thread = fail
        with patch("app.watcher.logging.error") as log_error:
            monitor.event_handler.on_created(self._event(source))
            self.assertTrue(processed.wait(timeout=5))
            self._wait_for_idle(monitor)

        self.assertFalse(monitor._pending_paths)
        self.assertFalse(monitor._futures)
        log_error.assert_called_once()

    def test_path_can_be_submitted_again_only_after_event_window(self):
        callback = MagicMock()
        monitor = self._start_monitor(callback=callback)
        source = self.incoming / "again.txt"
        source.write_text("content", encoding="utf-8")
        current_time = [100.0]

        with (
            patch("app.watcher.time.monotonic", side_effect=lambda: current_time[0]),
            patch.object(monitor.event_handler, "_wait_until_stable", return_value=True),
            patch("app.watcher.smart_classify", return_value=None),
            patch(
                "app.watcher.move_file_with_retries",
                return_value=self._moved(source),
            ),
        ):
            self.assertTrue(monitor.submit(str(source), "created"))
            self._wait_for_idle(monitor)
            callback.assert_called_once_with(source.name, "documents", "moved")

            current_time[0] = 101.0
            self.assertFalse(monitor.submit(str(source), "modified"))
            callback.assert_called_once()

            current_time[0] = 131.0
            self.assertTrue(monitor.submit(str(source), "modified"))
            self._wait_for_idle(monitor)

        self.assertEqual(callback.call_count, 2)

    def test_startup_scan_work_is_owned_and_drained(self):
        first = self.incoming / "first.txt"
        second = self.incoming / "second.txt"
        first.write_text("first", encoding="utf-8")
        second.write_text("second", encoding="utf-8")
        release = threading.Event()
        both_started = threading.Event()
        stop_done = threading.Event()
        calls = []
        lock = threading.Lock()

        monitor = FileMonitor(
            self._config(),
            {".txt": "documents"},
            max_processing_workers=2,
        )

        def process(file_path, source_event, _stop_event):
            with lock:
                calls.append((Path(file_path).name, source_event))
                if len(calls) == 2:
                    both_started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("startup scan workers were not released")

        monitor.event_handler._process_file_thread = process
        with patch("app.watcher.Observer", ObserverDouble):
            monitor.start()

        self.assertTrue(both_started.wait(timeout=5))
        stop_thread = threading.Thread(
            target=lambda: (monitor.stop(), stop_done.set())
        )
        stop_thread.start()
        try:
            self.assertTrue(monitor.observer.stopped.wait(timeout=5))
            self.assertFalse(stop_done.is_set())
            release.set()
            self.assertTrue(stop_done.wait(timeout=5))
            stop_thread.join(timeout=5)
        finally:
            release.set()
            stop_thread.join(timeout=5)
            monitor.stop()

        self.assertCountEqual(
            calls,
            [(first.name, "startup_scan"), (second.name, "startup_scan")],
        )
        self.assertIsNone(monitor._executor)
        self.assertFalse(monitor._futures)

    def test_stop_all_drains_every_folder_monitor(self):
        first = self.root / "first-watch"
        second = self.root / "second-watch"
        first.mkdir()
        second.mkdir()
        config = self._multi_config([
            {"path": str(first), "label": "First", "active": True},
            {"path": str(second), "label": "Second", "active": True},
        ])
        release = threading.Event()
        all_entered = threading.Event()
        entered = 0
        lock = threading.Lock()

        def process(*_args):
            nonlocal entered
            with lock:
                entered += 1
                if entered == 2:
                    all_entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("workers were not released")

        with (
            patch("app.watcher.Observer", ObserverDouble),
            patch.object(FileMonitor, "scan_existing_files"),
        ):
            monitor = MultiFolderMonitor(config, {".txt": "documents"})
            for folder_monitor in monitor._monitors.values():
                folder_monitor.event_handler._process_file_thread = process
            monitor.start_all()

        for path, folder_monitor in monitor._monitors.items():
            folder_monitor.submit(str(Path(path) / "file.txt"), "created")
        self.assertTrue(all_entered.wait(timeout=5))

        stop_done = threading.Event()
        stop_thread = threading.Thread(
            target=lambda: (monitor.stop_all(), stop_done.set())
        )
        stop_thread.start()
        try:
            self.assertTrue(ObserverDouble.instances[0].stopped.wait(timeout=5))
            self.assertFalse(stop_done.is_set())
            self.assertTrue(monitor._lifecycle_lock.acquire(timeout=1))
            monitor._lifecycle_lock.release()
            release.set()
            self.assertTrue(stop_done.wait(timeout=5))
            stop_thread.join(timeout=5)
        finally:
            release.set()
            stop_thread.join(timeout=5)
            monitor.stop_all()

        for folder_monitor in monitor._monitors.values():
            self.assertIsNone(folder_monitor._executor)
            self.assertFalse(folder_monitor._pending_paths)
            self.assertFalse(folder_monitor._futures)
        self.assertTrue(all(item.stopped.is_set() for item in ObserverDouble.instances))

    def test_multi_folder_monitor_preserves_per_folder_views(self):
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

        by_path = {item.config["source_folder"]: item for item in created_monitors}
        first_monitor = by_path[str(first.resolve())]
        second_monitor = by_path[str(second.resolve())]
        self.assertEqual(first_monitor.start_calls, 1)
        self.assertEqual(first_monitor.stop_calls, 1)
        self.assertEqual(second_monitor.start_calls, 0)
        self.assertEqual(second_monitor.stop_calls, 1)
        self.assertIs(first_monitor.config["rules"], config["rules"])
        self.assertIs(second_monitor.config["rules"], config["rules"])
        self.assertIs(
            first_monitor.config["destination_folders"],
            second_monitor.config["destination_folders"],
        )
        self.assertIs(first_monitor.callback, callback)
        self.assertIs(second_monitor.callback, callback)

    def test_classification_exception_is_logged_and_reported(self):
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

    def test_unexpected_mover_exception_is_logged_and_reported(self):
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

    def test_classification_and_mover_exceptions_do_not_stop_monitor(self):
        first = self.incoming / "classification-error.txt"
        second = self.incoming / "move-error.txt"
        third = self.incoming / "recovery.txt"
        for source in (first, second, third):
            source.write_text("content", encoding="utf-8")
        callbacks = []
        monitor = self._start_monitor(callback=lambda *args: callbacks.append(args))

        with (
            patch.object(monitor.event_handler, "_wait_until_stable", return_value=True),
            patch(
                "app.watcher.smart_classify",
                side_effect=RuntimeError("classification failed"),
            ),
        ):
            monitor.submit(str(first), "created")
            self._wait_for_idle(monitor)

        self.assertTrue(monitor.is_running)

        with (
            patch.object(monitor.event_handler, "_wait_until_stable", return_value=True),
            patch("app.watcher.smart_classify", return_value=None),
            patch(
                "app.watcher.move_file_with_retries",
                side_effect=RuntimeError("move failed"),
            ),
        ):
            monitor.submit(str(second), "created")
            self._wait_for_idle(monitor)

        self.assertTrue(monitor.is_running)

        with (
            patch.object(monitor.event_handler, "_wait_until_stable", return_value=True),
            patch("app.watcher.smart_classify", return_value=None),
            patch(
                "app.watcher.move_file_with_retries",
                return_value=self._moved(third),
            ),
        ):
            monitor.submit(str(third), "created")
            self._wait_for_idle(monitor)

        self.assertTrue(monitor.is_running)
        self.assertEqual(
            callbacks,
            [
                (first.name, "unknown", "error"),
                (second.name, "documents", "error"),
                (third.name, "documents", "moved"),
            ],
        )

    def test_gui_exit_stops_multi_folder_monitor(self):
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

        self.assertFalse(gui.monitor.is_running)
        self.assertEqual(gui.monitor.stop_all_calls, 1)
        gui.root.destroy.assert_called_once_with()

    def test_worker_callbacks_are_queued_and_generation_checked(self):
        gui = object.__new__(MonitoringMixin)
        gui.root = MagicMock()
        gui._live_callback_job = None
        gui._live_callback_generation = 0
        gui._on_file_processed = MagicMock()
        gui.reload_plugins_from_gui = MagicMock()
        gui._start_live_callback_pump()
        scheduled_calls = gui.root.after.call_count

        retired_callback = gui._make_live_callback()
        current_callback = gui._make_live_callback()
        retired_callback("old.txt", "documents", "moved")
        current_callback("new.txt", "documents", "moved")
        gui._queue_plugin_reload()

        self.assertEqual(gui.root.after.call_count, scheduled_calls)
        gui._drain_live_callbacks()

        gui._on_file_processed.assert_called_once_with(
            "new.txt",
            "documents",
            "moved",
        )
        gui.reload_plugins_from_gui.assert_called_once_with()

    def test_gui_exit_stops_legacy_single_monitor(self):
        monitor = MagicMock(spec=["stop"])
        gui = object.__new__(MonitoringMixin)
        gui.monitor = monitor
        gui._stop_dot_pulse = MagicMock()
        gui._stop_auto_refresh = MagicMock()
        gui.tray_icon = None
        gui.close_logs_viewer = MagicMock()
        gui.root = MagicMock()

        MonitoringMixin.exit_application(gui)

        monitor.stop.assert_called_once_with()
        gui.root.destroy.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
