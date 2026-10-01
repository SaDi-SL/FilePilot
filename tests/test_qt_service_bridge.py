import os
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QEventLoop
    from PySide6.QtTest import QSignalSpy
    from PySide6.QtWidgets import QApplication
except ImportError as error:
    raise unittest.SkipTest(
        "PySide6 is required for Qt tests; install requirements-qt.txt"
    ) from error

from app.application_service import (
    ActivityEvent,
    MonitorState,
    StartupResult,
    StartupStatus,
)
from app.ui.qt.service_bridge import QtServiceBridge


class FakeService:
    def __init__(
        self,
        startup_status=StartupStatus.READY,
        monitor_state=MonitorState.STOPPED,
    ):
        self.startup_result = None
        self.startup_status_to_return = startup_status
        self.monitor_state = monitor_state
        self.last_error = None
        self.failed_folders = ()
        self.blocking_ids = ()
        self.blocking_reasons = ()
        self.state_subscribers = []
        self.activity_subscribers = []
        self.bootstrap_thread_id = None
        self.start_calls = 0
        self.stop_calls = 0
        self.shutdown_calls = 0
        self.shutdown_state = MonitorState.STOPPED
        self.start_entered = threading.Event()
        self.release_start = threading.Event()
        self.release_start.set()
        self._operation_lock = threading.Lock()
        self._active_operations = 0
        self.max_active_operations = 0

    def subscribe_state(self, callback):
        self.state_subscribers.append(callback)
        return lambda: self.state_subscribers.remove(callback)

    def subscribe_activity(self, callback):
        self.activity_subscribers.append(callback)
        return lambda: self.activity_subscribers.remove(callback)

    def bootstrap(self):
        self.bootstrap_thread_id = threading.get_ident()
        self.startup_result = StartupResult(
            self.startup_status_to_return,
            blocking_operation_ids=self.blocking_ids,
            blocking_reasons=self.blocking_reasons,
            error=self.last_error,
        )
        if self.startup_status_to_return is StartupStatus.BLOCKED:
            self.monitor_state = MonitorState.BLOCKED
        elif self.startup_status_to_return is StartupStatus.ERROR:
            self.monitor_state = MonitorState.ERROR
        else:
            self.monitor_state = MonitorState.STOPPED
        self.emit_state(self.monitor_state)
        return self.startup_result

    def start(self):
        with self._operation():
            self.start_calls += 1
            self.monitor_state = MonitorState.STARTING
            self.emit_state(self.monitor_state)
            self.start_entered.set()
            if not self.release_start.wait(3):
                raise RuntimeError("start timed out")
            self.monitor_state = MonitorState.RUNNING
            self.emit_state(self.monitor_state)
            return self.monitor_state

    def stop(self):
        with self._operation():
            self.stop_calls += 1
            self.monitor_state = MonitorState.STOPPING
            self.emit_state(self.monitor_state)
            self.monitor_state = MonitorState.STOPPED
            self.emit_state(self.monitor_state)
            return self.monitor_state

    def shutdown(self):
        with self._operation():
            self.shutdown_calls += 1
            self.monitor_state = self.shutdown_state
            if self.shutdown_state is MonitorState.ERROR:
                self.last_error = "watch folder remained active"
            return self.monitor_state

    def emit_state(self, state):
        for callback in tuple(self.state_subscribers):
            callback(state)

    def emit_activity(self, event):
        for callback in tuple(self.activity_subscribers):
            callback(event)

    class _Operation:
        def __init__(self, owner):
            self.owner = owner

        def __enter__(self):
            with self.owner._operation_lock:
                self.owner._active_operations += 1
                self.owner.max_active_operations = max(
                    self.owner.max_active_operations,
                    self.owner._active_operations,
                )

        def __exit__(self, exc_type, exc, traceback):
            with self.owner._operation_lock:
                self.owner._active_operations -= 1

    def _operation(self):
        return self._Operation(self)


class QtServiceBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.bridges = []

    def tearDown(self):
        for bridge in self.bridges:
            if not bridge.is_closed:
                bridge.shutdown()
                bridge.wait_for_shutdown()
        self.app.processEvents()

    def _bridge(self, service):
        bridge = QtServiceBridge(service)
        self.bridges.append(bridge)
        return bridge

    def _wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 25)
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Timed out waiting for Qt condition")

    def _bootstrap(self, bridge):
        spy = QSignalSpy(bridge.startup_completed)
        bridge.bootstrap()
        self._wait_until(lambda: spy.count() == 1)

    def test_bootstrap_runs_outside_gui_thread(self):
        service = FakeService()
        bridge = self._bridge(service)
        spy = QSignalSpy(bridge.operation_thread_observed)

        self._bootstrap(bridge)

        self.assertNotEqual(service.bootstrap_thread_id, threading.get_ident())
        self.assertTrue(
            any(spy.at(index)[0] == "bootstrap" for index in range(spy.count()))
        )

    def test_lifecycle_commands_are_serialized(self):
        service = FakeService()
        service.release_start.clear()
        bridge = self._bridge(service)
        self._bootstrap(bridge)

        bridge.request_start()
        self.assertTrue(service.start_entered.wait(1))
        bridge.request_stop()
        service.release_start.set()

        self._wait_until(
            lambda: service.stop_calls == 1
            and bridge.snapshot.monitor_state is MonitorState.STOPPED
        )
        self.assertEqual(service.start_calls, 1)
        self.assertEqual(service.stop_calls, 1)
        self.assertEqual(service.max_active_operations, 1)

    def test_service_callbacks_arrive_on_gui_thread(self):
        service = FakeService()
        bridge = self._bridge(service)
        self._bootstrap(bridge)
        callback_threads = []
        bridge.state_changed.connect(
            lambda snapshot: callback_threads.append(threading.get_ident())
        )

        worker = threading.Thread(
            target=lambda: service.emit_state(MonitorState.ERROR)
        )
        worker.start()
        worker.join()

        self._wait_until(lambda: bool(callback_threads))
        self.assertEqual(callback_threads[-1], threading.get_ident())

    def test_activity_callback_is_queued_without_losing_event(self):
        service = FakeService()
        bridge = self._bridge(service)
        self._bootstrap(bridge)
        event = ActivityEvent(Path("report.txt"), "documents", "moved")
        received = []
        callback_threads = []
        bridge.activity_received.connect(
            lambda item: (
                received.append(item),
                callback_threads.append(threading.get_ident()),
            )
        )

        worker = threading.Thread(target=lambda: service.emit_activity(event))
        worker.start()
        worker.join()

        self._wait_until(lambda: bool(received))
        self.assertIs(received[0], event)
        self.assertEqual(callback_threads[0], threading.get_ident())

    def test_latest_start_request_avoids_redundant_stop(self):
        service = FakeService()
        service.release_start.clear()
        bridge = self._bridge(service)
        self._bootstrap(bridge)

        bridge.request_start()
        self.assertTrue(service.start_entered.wait(1))
        bridge.request_stop()
        bridge.request_start()
        service.release_start.set()

        self._wait_until(
            lambda: bridge.snapshot.monitor_state is MonitorState.RUNNING
        )
        self.assertEqual(service.start_calls, 1)
        self.assertEqual(service.stop_calls, 0)

    def test_blocked_bootstrap_rejects_start(self):
        service = FakeService(StartupStatus.BLOCKED, MonitorState.BLOCKED)
        service.blocking_ids = ("operation-1",)
        service.blocking_reasons = ("Ambiguous destination",)
        bridge = self._bridge(service)

        self._bootstrap(bridge)
        bridge.request_start()
        self.app.processEvents()

        self.assertEqual(service.start_calls, 0)
        self.assertEqual(
            bridge.snapshot.blocking_operation_ids,
            ("operation-1",),
        )

    def test_duplicate_bootstrap_requests_build_once(self):
        service = FakeService()
        bootstrap_calls = 0
        original = service.bootstrap

        def counted_bootstrap():
            nonlocal bootstrap_calls
            bootstrap_calls += 1
            return original()

        service.bootstrap = counted_bootstrap
        bridge = self._bridge(service)
        spy = QSignalSpy(bridge.startup_completed)

        bridge.bootstrap()
        bridge.bootstrap()

        self._wait_until(lambda: spy.count() == 1)
        self.assertEqual(bootstrap_calls, 1)

    def test_bootstrap_exception_publishes_startup_error(self):
        service = FakeService()

        def failed_bootstrap():
            raise RuntimeError("configuration failed")

        service.bootstrap = failed_bootstrap
        bridge = self._bridge(service)
        failed = QSignalSpy(bridge.command_failed)

        bridge.bootstrap()
        self._wait_until(lambda: failed.count() == 1)

        self.assertIs(bridge.snapshot.startup_status, StartupStatus.ERROR)
        self.assertIs(bridge.snapshot.monitor_state, MonitorState.ERROR)
        self.assertEqual(bridge.snapshot.error, "configuration failed")

    def test_failed_shutdown_keeps_bridge_open_for_retry(self):
        service = FakeService()
        bridge = self._bridge(service)
        self._bootstrap(bridge)
        service.shutdown_state = MonitorState.ERROR
        failed = QSignalSpy(bridge.command_failed)

        bridge.shutdown()
        self._wait_until(lambda: failed.count() == 1)

        self.assertFalse(bridge.is_closed)
        self.assertTrue(bridge._thread.isRunning())
        self.assertEqual(failed.at(0)[0], "shutdown")
        self.assertEqual(len(service.state_subscribers), 1)
        self.assertEqual(len(service.activity_subscribers), 1)

        service.shutdown_state = MonitorState.STOPPED
        bridge.shutdown()
        self._wait_until(lambda: bridge.is_closed)

    def test_shutdown_unsubscribes_and_stops_worker(self):
        service = FakeService()
        bridge = self._bridge(service)
        self._bootstrap(bridge)
        closed = QSignalSpy(bridge.closed)

        bridge.shutdown()
        self._wait_until(lambda: closed.count() == 1)

        self.assertTrue(bridge.is_closed)
        self.assertEqual(service.shutdown_calls, 1)
        self.assertEqual(service.state_subscribers, [])
        self.assertEqual(service.activity_subscribers, [])
        self.assertFalse(bridge._thread.isRunning())


if __name__ == "__main__":
    unittest.main()
