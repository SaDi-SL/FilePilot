import json
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.application_service import (
    ActivityEvent,
    FilePilotService,
    MonitorState,
    StartupStatus,
)
from app.headless import HeadlessApp
from app.gui_monitoring import MonitoringMixin
from app.mover import MoveResult, MoveStatus
from app.recovery import RecoveryBlockedError, RecoveryReport
from app.watcher import NewFileHandler


class MonitorDouble:
    def __init__(self, folders, *, start_error=None, stop_error=None, partial=None):
        self.folders = tuple(str(Path(path).resolve()) for path in folders)
        self.start_error = start_error
        self.stop_error = stop_error
        self.partial = partial
        self.running_folders = []
        self.is_running = False
        self.start_calls = 0
        self.stop_calls = 0
        self.activity_callback = None
        self.config = None

    def set_activity_callback(self, callback):
        self.activity_callback = callback

    def start_all(self):
        self.start_calls += 1
        selected = self.folders if self.partial is None else self.folders[:self.partial]
        self.running_folders = list(selected)
        self.is_running = bool(self.running_folders)
        if self.start_error is not None:
            raise self.start_error

    def stop_all(self):
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error
        self.running_folders = []
        self.is_running = False

    def set_folder_active(self, path, active):
        target = str(Path(path).resolve())
        for item in self.config.get("watch_folders", []):
            if str(Path(item["path"]).resolve()) == target:
                item["active"] = active

    def folder_status(self, path):
        target = str(Path(path).resolve())
        return "running" if target in self.running_folders else "stopped"

    def start_folder(self, path):
        target = str(Path(path).resolve())
        if target not in self.running_folders:
            self.running_folders.append(target)
        self.is_running = True
        return True

    def stop_folder(self, path):
        target = str(Path(path).resolve())
        if target in self.running_folders:
            self.running_folders.remove(target)
        self.is_running = bool(self.running_folders)
        return True


class BlockingMonitor(MonitorDouble):
    def __init__(self, folders):
        super().__init__(folders)
        self.start_entered = threading.Event()
        self.release_start = threading.Event()
        self.stop_entered = threading.Event()
        self.release_stop = threading.Event()

    def start_all(self):
        self.start_calls += 1
        self.start_entered.set()
        if not self.release_start.wait(5):
            raise RuntimeError("start test timed out")
        self.running_folders = list(self.folders)
        self.is_running = True

    def stop_all(self):
        self.stop_calls += 1
        self.stop_entered.set()
        if not self.release_stop.wait(5):
            raise RuntimeError("stop test timed out")
        self.running_folders = []
        self.is_running = False


class ApplicationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.config_path = self.root / "config.json"
        self.incoming = self.root / "incoming"
        self.second = self.root / "second"
        self.organized = self.root / "organized"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _config(self, *, completed=True, folders=None, auto_start=False):
        if folders is None:
            folders = [self.incoming]
        return {
            "first_run_completed": completed,
            "source_folder": str(folders[0]) if folders else "",
            "watch_folders": [
                {"path": str(path), "label": path.name, "active": True}
                for path in folders
            ],
            "organized_base_folder": str(self.organized),
            "rules": {"documents": [".txt"]},
            "log_file": str(self.root / "logs" / "filepilot.log"),
            "stats_file": str(self.root / "reports" / "stats.json"),
            "history_file": str(self.root / "reports" / "history.csv"),
            "hash_db_file": str(self.root / "reports" / "hashes.json"),
            "auto_start_monitoring": auto_start,
        }

    def _write_config(self, config=None):
        self.config_path.write_text(
            json.dumps(config or self._config()),
            encoding="utf-8",
        )

    def _service(self, monitor, config=None):
        config = config or self._config()
        self._write_config(config)
        builder = MagicMock(return_value=(config, monitor))
        monitor.config = config
        return FilePilotService(
            config_path=self.config_path,
            monitor_builder=builder,
        ), builder

    def test_valid_configuration_bootstraps_ready(self):
        monitor = MonitorDouble([self.incoming])
        service, builder = self._service(monitor)

        result = service.bootstrap()

        self.assertEqual(result.status, StartupStatus.READY)
        self.assertEqual(service.monitor_state, MonitorState.STOPPED)
        self.assertIs(service.monitor, monitor)
        builder.assert_called_once_with()

    def test_missing_config_requires_setup_without_runtime_construction(self):
        builder = MagicMock()
        service = FilePilotService(
            config_path=self.config_path,
            monitor_builder=builder,
        )

        result = service.bootstrap()

        self.assertEqual(result.status, StartupStatus.SETUP_REQUIRED)
        self.assertEqual(service.monitor_state, MonitorState.STOPPED)
        builder.assert_not_called()
        self.assertFalse(self.incoming.exists())
        self.assertFalse(self.organized.exists())
        self.assertFalse((self.root / "logs").exists())
        self.assertFalse((self.root / "reports").exists())

    def test_incomplete_config_requires_setup_without_runtime_construction(self):
        self._write_config(self._config(completed=False))
        builder = MagicMock()
        service = FilePilotService(
            config_path=self.config_path,
            monitor_builder=builder,
        )

        result = service.bootstrap()

        self.assertEqual(result.status, StartupStatus.SETUP_REQUIRED)
        builder.assert_not_called()
        self.assertFalse(self.incoming.exists())
        self.assertFalse(self.organized.exists())

    def test_completed_config_without_folder_requires_setup(self):
        config = self._config(folders=[])
        config.pop("watch_folders")
        self._write_config(config)
        builder = MagicMock()
        service = FilePilotService(
            config_path=self.config_path,
            monitor_builder=builder,
        )

        result = service.bootstrap()

        self.assertEqual(result.status, StartupStatus.SETUP_REQUIRED)
        builder.assert_not_called()

    def test_invalid_config_is_error_not_setup(self):
        self.config_path.write_text("{", encoding="utf-8")
        builder = MagicMock()
        service = FilePilotService(
            config_path=self.config_path,
            monitor_builder=builder,
        )

        result = service.bootstrap()

        self.assertEqual(result.status, StartupStatus.ERROR)
        self.assertEqual(service.monitor_state, MonitorState.ERROR)
        builder.assert_not_called()

    def test_recovery_block_is_structured_and_cannot_start(self):
        self._write_config()
        assessment = MagicMock()
        assessment.reason = "destination identity is ambiguous"
        assessment.operation.operation_id = "operation-1"
        report = RecoveryReport((assessment,), (), ("operation-1",))
        builder = MagicMock(side_effect=RecoveryBlockedError(
            "review required",
            report,
            ("operation-1",),
        ))
        service = FilePilotService(
            config_path=self.config_path,
            monitor_builder=builder,
        )

        result = service.bootstrap()

        self.assertEqual(result.status, StartupStatus.BLOCKED)
        self.assertEqual(result.blocking_operation_ids, ("operation-1",))
        self.assertEqual(
            result.blocking_reasons,
            ("destination identity is ambiguous",),
        )
        self.assertEqual(service.monitor_state, MonitorState.BLOCKED)
        self.assertIsNone(service.monitor)
        self.assertEqual(service.start(), MonitorState.BLOCKED)
        builder.assert_called_once_with()

    def test_successful_start_reports_starting_before_running(self):
        monitor = BlockingMonitor([self.incoming])
        service, _ = self._service(monitor)
        service.bootstrap()
        states = []
        service.subscribe_state(states.append)

        worker = threading.Thread(target=service.start)
        worker.start()
        self.assertTrue(monitor.start_entered.wait(2))

        self.assertEqual(service.monitor_state, MonitorState.STARTING)
        self.assertNotIn(MonitorState.RUNNING, states)

        monitor.release_start.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(service.monitor_state, MonitorState.RUNNING)
        self.assertEqual(states, [MonitorState.STARTING, MonitorState.RUNNING])

        monitor.release_stop.set()
        service.stop()

    def test_start_failure_is_error_and_partial_runtime_is_stopped(self):
        monitor = MonitorDouble(
            [self.incoming],
            start_error=RuntimeError("observer failed"),
        )
        service, _ = self._service(monitor)
        service.bootstrap()

        state = service.start()

        self.assertEqual(state, MonitorState.ERROR)
        self.assertNotEqual(service.monitor_state, MonitorState.RUNNING)
        self.assertEqual(monitor.stop_calls, 1)
        self.assertFalse(monitor.is_running)
        self.assertIn("observer failed", service.last_error)

    def test_setup_required_never_starts_monitoring(self):
        monitor = MonitorDouble([self.incoming])
        builder = MagicMock(return_value=(self._config(), monitor))
        service = FilePilotService(
            config_path=self.config_path,
            monitor_builder=builder,
        )
        service.bootstrap()

        state = service.start()

        self.assertEqual(state, MonitorState.STOPPED)
        self.assertEqual(monitor.start_calls, 0)
        builder.assert_not_called()

    def test_stop_reports_stopping_before_stopped(self):
        monitor = BlockingMonitor([self.incoming])
        monitor.release_start.set()
        service, _ = self._service(monitor)
        service.bootstrap()
        service.start()
        states = []
        service.subscribe_state(states.append)

        worker = threading.Thread(target=service.stop)
        worker.start()
        self.assertTrue(monitor.stop_entered.wait(2))

        self.assertEqual(service.monitor_state, MonitorState.STOPPING)
        self.assertNotIn(MonitorState.STOPPED, states)

        monitor.release_stop.set()
        worker.join(2)
        self.assertEqual(service.monitor_state, MonitorState.STOPPED)
        self.assertEqual(states, [MonitorState.STOPPING, MonitorState.STOPPED])

    def test_partial_multi_folder_start_fails_closed(self):
        config = self._config(folders=[self.incoming, self.second])
        monitor = MonitorDouble([self.incoming, self.second], partial=1)
        service, _ = self._service(monitor, config)
        service.bootstrap()

        state = service.start()

        self.assertEqual(state, MonitorState.ERROR)
        self.assertEqual(service.failed_folders, (str(self.second.resolve()),))
        self.assertEqual(monitor.stop_calls, 1)
        self.assertFalse(monitor.is_running)

    def test_no_active_folder_cannot_report_running(self):
        config = self._config()
        config["watch_folders"][0]["active"] = False
        monitor = MonitorDouble([])
        service, _ = self._service(monitor, config)
        service.bootstrap()

        state = service.start()

        self.assertEqual(state, MonitorState.ERROR)
        self.assertNotEqual(service.monitor_state, MonitorState.RUNNING)

    def test_activity_event_preserves_move_result_evidence(self):
        monitor = MonitorDouble([self.incoming])
        service, _ = self._service(monitor)
        service.bootstrap()
        events = []
        service.subscribe_activity(events.append)
        source = self.incoming / "report.txt"
        destination = self.organized / "documents" / "report.txt"
        duplicate = self.organized / "documents" / "original.txt"
        result = MoveResult(
            MoveStatus.MOVED,
            source,
            destination=destination,
            duplicate_of=duplicate,
            error="move warning",
            metadata_error="history unavailable",
            operation_id="operation-7",
        )

        monitor.activity_callback(
            source,
            "documents",
            "moved",
            result,
            None,
        )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIsInstance(event, ActivityEvent)
        self.assertIs(event.move_result, result)
        self.assertEqual(event.filename, "report.txt")
        self.assertEqual(event.actual_destination, destination)
        self.assertEqual(event.operation_id, "operation-7")
        self.assertEqual(event.duplicate_target, duplicate)
        self.assertEqual(event.error, "move warning")
        self.assertEqual(event.metadata_warning, "history unavailable")

    def test_processing_error_is_preserved_without_move_result(self):
        monitor = MonitorDouble([self.incoming])
        service, _ = self._service(monitor)
        service.bootstrap()
        events = []
        service.subscribe_activity(events.append)

        monitor.activity_callback(
            self.incoming / "bad.txt",
            "unknown",
            "error",
            None,
            "classification failed",
        )

        self.assertEqual(events[0].error, "classification failed")
        self.assertIsNone(events[0].operation_id)

    def test_watcher_rich_callback_preserves_real_move_result(self):
        self.incoming.mkdir()
        source = self.incoming / "report.txt"
        source.write_text("content", encoding="utf-8")
        config = self._config()
        config["destination_folders"] = {
            "documents": str(self.organized / "documents"),
            "others": str(self.organized / "others"),
        }
        legacy = MagicMock()
        rich = MagicMock()
        result = MoveResult(
            MoveStatus.MOVED,
            source,
            destination=self.organized / "documents" / source.name,
            operation_id="operation-rich",
        )
        handler = NewFileHandler(
            config,
            {".txt": "documents"},
            file_processed_callback=legacy,
            activity_callback=rich,
        )

        with (
            patch.object(handler, "_wait_until_stable", return_value=True),
            patch("app.watcher.smart_classify", return_value=None),
            patch("app.watcher.move_file_with_retries", return_value=result),
        ):
            handler._process_file_thread(str(source), "created")

        rich.assert_called_once_with(source, "documents", "moved", result, None)
        legacy.assert_called_once_with(source.name, "documents", "moved")

    def test_legacy_source_folder_config_remains_compatible(self):
        config = self._config()
        config.pop("watch_folders")
        monitor = MonitorDouble([self.incoming])
        service, builder = self._service(monitor, config)

        result = service.bootstrap()

        self.assertEqual(result.status, StartupStatus.READY)
        builder.assert_called_once_with()

    def test_complete_legacy_config_without_first_run_marker_is_ready(self):
        config = self._config()
        config.pop("first_run_completed")
        monitor = MonitorDouble([self.incoming])
        service, builder = self._service(monitor, config)

        result = service.bootstrap()

        self.assertEqual(result.status, StartupStatus.READY)
        builder.assert_called_once_with()

    def test_force_bootstrap_rejects_running_monitor(self):
        monitor = MonitorDouble([self.incoming])
        service, builder = self._service(monitor)
        service.bootstrap()
        service.start()

        result = service.bootstrap(force=True)

        self.assertEqual(result.status, StartupStatus.ERROR)
        self.assertIs(service.monitor, monitor)
        self.assertTrue(monitor.is_running)
        builder.assert_called_once_with()
        service.stop()

    def test_reload_reports_restart_failure(self):
        config = self._config()
        self._write_config(config)
        old_monitor = MonitorDouble([self.incoming])
        new_monitor = MonitorDouble(
            [self.incoming],
            start_error=RuntimeError("restart failed"),
        )
        old_monitor.config = config
        new_monitor.config = config
        builder = MagicMock(side_effect=[
            (config, old_monitor),
            (config, new_monitor),
        ])
        service = FilePilotService(
            config_path=self.config_path,
            monitor_builder=builder,
        )
        service.bootstrap()
        service.start()

        result = service.reload(preserve_running=True)

        self.assertEqual(result.status, StartupStatus.ERROR)
        self.assertEqual(service.monitor_state, MonitorState.ERROR)
        self.assertIn("restart failed", result.error)
        self.assertFalse(new_monitor.is_running)

    def test_reload_reports_stop_failure_without_rebuilding(self):
        monitor = MonitorDouble(
            [self.incoming],
            stop_error=RuntimeError("stop failed"),
        )
        service, builder = self._service(monitor)
        service.bootstrap()
        service.start()

        result = service.reload(preserve_running=True)

        self.assertEqual(result.status, StartupStatus.ERROR)
        self.assertEqual(service.monitor_state, MonitorState.ERROR)
        self.assertIn("stop failed", result.error)
        builder.assert_called_once_with()

    def test_recovery_blocked_reload_leaves_no_monitor_to_start(self):
        config = self._config()
        self._write_config(config)
        old_monitor = MonitorDouble([self.incoming])
        old_monitor.config = config
        assessment = MagicMock()
        assessment.reason = "ambiguous"
        report = RecoveryReport((assessment,), (), ("blocked-op",))
        builder = MagicMock(side_effect=[
            (config, old_monitor),
            RecoveryBlockedError("blocked", report, ("blocked-op",)),
        ])
        service = FilePilotService(
            config_path=self.config_path,
            monitor_builder=builder,
        )
        service.bootstrap()
        service.start()

        result = service.reload(preserve_running=True)

        self.assertEqual(result.status, StartupStatus.BLOCKED)
        self.assertEqual(service.monitor_state, MonitorState.BLOCKED)
        self.assertIsNone(service.monitor)
        self.assertFalse(old_monitor.is_running)
        self.assertEqual(service.start_folder(str(self.incoming)), MonitorState.BLOCKED)

    def test_headless_gui_handoff_reuses_service_and_monitor(self):
        config = self._config(auto_start=False)
        monitor = MonitorDouble([self.incoming])
        service, builder = self._service(monitor, config)
        app = HeadlessApp(service=service)

        def request_gui():
            app._open_gui_requested = True

        app._run_tray = request_gui
        with patch("app.gui.launch_gui") as launch_gui:
            app.run()

        builder.assert_called_once_with()
        launch_gui.assert_called_once_with(service=service, owns_service=False)
        self.assertIs(service.monitor, monitor)

    def test_headless_gui_failure_still_stops_shared_monitor(self):
        config = self._config(auto_start=True)
        monitor = MonitorDouble([self.incoming])
        service, _ = self._service(monitor, config)
        app = HeadlessApp(service=service)

        def request_gui():
            app._open_gui_requested = True

        app._run_tray = request_gui
        with patch("app.gui.launch_gui", side_effect=RuntimeError("Tk failed")):
            with self.assertRaisesRegex(RuntimeError, "Tk failed"):
                app.run()

        self.assertFalse(monitor.is_running)
        self.assertEqual(service.monitor_state, MonitorState.STOPPED)

    def test_gui_start_then_stop_race_honors_latest_request(self):
        monitor = BlockingMonitor([self.incoming])
        service, _ = self._service(monitor)
        service.bootstrap()
        gui = object.__new__(MonitoringMixin)
        gui.service = service
        gui._monitor_error_queue = queue.SimpleQueue()
        monitor.release_stop.set()

        gui.start_monitoring()
        gui.stop_monitoring()
        monitor.release_start.set()

        self._wait_for_gui_commands(gui)
        self.assertEqual(service.monitor_state, MonitorState.STOPPED)
        self.assertFalse(monitor.is_running)

    def test_gui_stop_then_start_race_honors_latest_request(self):
        monitor = BlockingMonitor([self.incoming])
        monitor.release_start.set()
        service, _ = self._service(monitor)
        service.bootstrap()
        service.start()
        gui = object.__new__(MonitoringMixin)
        gui.service = service
        gui._monitor_error_queue = queue.SimpleQueue()

        gui.stop_monitoring()
        gui.start_monitoring()
        monitor.release_stop.set()

        self._wait_for_gui_commands(gui)
        self.assertEqual(service.monitor_state, MonitorState.RUNNING)
        self.assertTrue(monitor.is_running)
        monitor.release_stop.set()
        service.stop()

    def _wait_for_gui_commands(self, gui):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            lock = getattr(gui, "_service_command_lock", None)
            if lock is not None:
                with lock:
                    if not gui._service_command_active:
                        return
            time.sleep(0.01)
        self.fail("GUI lifecycle command worker did not finish")


if __name__ == "__main__":
    unittest.main()
