import os
import threading
import time
import unittest
from datetime import datetime, timezone
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
    ActivityRecord,
    ActivityStatus,
    MonitorState,
    ProductDataState,
    ProductMetrics,
    ProductSnapshot,
    OperationPreview,
    PreviewStatus,
    RecoveryAction,
    RecoveryActionResult,
    RecoveryActionStatus,
    RecoverySnapshot,
    SafetyDataState,
    StartupResult,
    StartupStatus,
    UndoAvailability,
    UndoResult,
    UndoStatus,
)
from app.ui.qt.service_bridge import QtServiceBridge
from app.product_configuration import (
    ConfigurationDataState,
    ConfigurationSaveResult,
    ConfigurationSaveStatus,
    ConfigurationValidationResult,
    ProductConfigurationCandidate,
    ProductConfigurationSnapshot,
    ProductRule,
    ProductWatchFolder,
)


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
        self.product_read_calls = 0
        self.product_read_thread_ids = []
        self.product_read_entered = threading.Event()
        self.release_product_read = threading.Event()
        self.release_product_read.set()
        self.product_read_error = None
        self.product_snapshot_to_return = ProductSnapshot(
            ProductDataState.AVAILABLE,
            ProductMetrics(0, 0, 0, 0),
        )
        self.preview_calls = []
        self.preview_thread_ids = []
        self.preview_entered = threading.Event()
        self.release_preview = threading.Event()
        self.release_preview.set()
        self.undo_availability_calls = []
        self.undo_calls = []
        self.undo_entered = threading.Event()
        self.release_undo = threading.Event()
        self.release_undo.set()
        self.recovery_read_calls = 0
        self.recovery_action_calls = []
        self.recovery_snapshot_to_return = RecoverySnapshot(
            SafetyDataState.AVAILABLE
        )
        self.configuration_snapshot_to_return = ProductConfigurationSnapshot(
            ConfigurationDataState.UNAVAILABLE,
            error="Configuration unavailable in test double",
        )
        self.configuration_read_calls = 0
        self.configuration_read_thread_ids = []
        self.configuration_validation_calls = []
        self.configuration_validation_entered = threading.Event()
        self.release_configuration_validation = threading.Event()
        self.release_configuration_validation.set()
        self.candidate_classification_calls = []
        self.configuration_save_calls = []

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

    def get_product_snapshot(self, limit=20):
        with self._operation():
            self.product_read_calls += 1
            self.product_read_thread_ids.append(threading.get_ident())
            result = self.product_snapshot_to_return
            error = self.product_read_error
            self.product_read_entered.set()
            if not self.release_product_read.wait(3):
                raise RuntimeError("product read timed out")
            if error is not None:
                raise error
            return result

    def preview_file(self, source):
        with self._operation():
            self.preview_calls.append(source)
            self.preview_thread_ids.append(threading.get_ident())
            self.preview_entered.set()
            if not self.release_preview.wait(3):
                raise RuntimeError("preview timed out")
            return OperationPreview(
                SafetyDataState.AVAILABLE,
                PreviewStatus.READY,
                Path(source),
                message="Safe preview",
            )

    def get_undo_availability(self, operation_id):
        with self._operation():
            self.undo_availability_calls.append(operation_id)
            return UndoAvailability(operation_id, True, "Undo is available")

    def undo_operation(self, operation_id):
        with self._operation():
            self.undo_calls.append(operation_id)
            self.undo_entered.set()
            if not self.release_undo.wait(3):
                raise RuntimeError("undo timed out")
            return UndoResult(UndoStatus.SUCCESS, operation_id)

    def get_recovery_snapshot(self, limit=100, offset=0):
        with self._operation():
            self.recovery_read_calls += 1
            return self.recovery_snapshot_to_return

    def reconcile_recovery_item(self, operation_id, action):
        with self._operation():
            self.recovery_action_calls.append((operation_id, action))
            return RecoveryActionResult(
                operation_id,
                RecoveryActionStatus.RECONCILED,
                "Recovered safely",
            )

    def get_product_configuration(self):
        with self._operation():
            self.configuration_read_calls += 1
            self.configuration_read_thread_ids.append(threading.get_ident())
            return self.configuration_snapshot_to_return

    def validate_product_configuration(self, candidate):
        with self._operation():
            self.configuration_validation_calls.append(candidate)
            self.configuration_validation_entered.set()
            if not self.release_configuration_validation.wait(3):
                raise RuntimeError("configuration validation timed out")
            return ConfigurationValidationResult(True, candidate)

    def preview_candidate_classification(self, rules, filename):
        with self._operation():
            self.candidate_classification_calls.append((rules, filename))
            return (rules, filename)

    def save_product_configuration(self, candidate, expected_revision):
        with self._operation():
            self.configuration_save_calls.append((candidate, expected_revision))
            return ConfigurationSaveResult(
                ConfigurationSaveStatus.SAVED,
                "Saved",
                self.configuration_snapshot_to_return,
            )

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

    @staticmethod
    def _configuration_candidate(extension=".txt"):
        return ProductConfigurationCandidate(
            watch_folders=(
                ProductWatchFolder(Path("C:/Inbox"), "Incoming", True),
            ),
            organized_folder=Path("C:/Organized"),
            archive_by_date=False,
            rules=(ProductRule("documents", (extension,)),),
        )

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

    def test_failed_shutdown_after_active_read_does_not_stall_refresh(self):
        service = FakeService()
        service.release_product_read.clear()
        bridge = self._bridge(service)
        self._bootstrap(bridge)
        service.shutdown_state = MonitorState.ERROR
        failed = QSignalSpy(bridge.command_failed)

        bridge.request_product_refresh()
        self.assertTrue(service.product_read_entered.wait(1))
        bridge.shutdown()
        service.release_product_read.set()
        self._wait_until(lambda: failed.count() == 1)
        self._wait_until(
            lambda: service.product_read_calls >= 2
            and not bridge._product_request_active
        )

        self.assertFalse(bridge._product_request_active)
        self.assertFalse(bridge._command_active)
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

    def test_product_read_runs_off_gui_thread_and_returns_on_gui_thread(self):
        service = FakeService()
        bridge = self._bridge(service)
        received_threads = []
        bridge.product_snapshot_changed.connect(
            lambda _snapshot: received_threads.append(threading.get_ident())
        )

        bridge.request_product_refresh(20)
        self._wait_until(lambda: bool(received_threads))

        self.assertNotEqual(service.product_read_thread_ids[-1], threading.get_ident())
        self.assertEqual(received_threads[-1], threading.get_ident())

    def test_rapid_product_refreshes_are_coalesced(self):
        service = FakeService()
        service.product_snapshot_to_return = ProductSnapshot(
            ProductDataState.AVAILABLE,
            ProductMetrics(1, 0, 0, 0),
        )
        service.release_product_read.clear()
        bridge = self._bridge(service)
        published = []
        bridge.product_snapshot_changed.connect(published.append)

        bridge.request_product_refresh(20)
        self.assertTrue(service.product_read_entered.wait(1))
        for _ in range(20):
            bridge.request_product_refresh(100)
        service.product_snapshot_to_return = ProductSnapshot(
            ProductDataState.AVAILABLE,
            ProductMetrics(2, 0, 0, 0),
        )
        service.release_product_read.set()

        self._wait_until(
            lambda: service.product_read_calls == 2
            and bridge.product_snapshot.metrics.total_processed == 2
        )
        self.app.processEvents()
        self.assertEqual(service.product_read_calls, 2)
        self.assertNotIn(1, [item.metrics.total_processed for item in published])

    def test_live_and_durable_activity_converge_by_operation_id(self):
        service = FakeService()
        service.release_product_read.clear()
        bridge = self._bridge(service)
        bridge.request_product_refresh(100)
        self.assertTrue(service.product_read_entered.wait(1))

        operation_id = "operation-7"
        event = ActivityEvent(
            Path("report.txt"),
            "documents",
            "moved",
            occurred_at_utc=datetime.now(timezone.utc),
        )
        event = ActivityEvent(
            event.source,
            event.category,
            event.status,
            move_result=type(
                "Result",
                (),
                {
                    "operation_id": operation_id,
                    "destination": Path("C:/Organized/report.txt"),
                    "duplicate_of": None,
                    "error": None,
                    "metadata_error": None,
                },
            )(),
            occurred_at_utc=event.occurred_at_utc,
        )
        durable = ActivityRecord(
            "durable-record-7",
            operation_id,
            event.occurred_at_utc,
            Path("C:/Inbox/report.txt"),
            None,
            ActivityStatus.COMPLETED,
            actual_destination=Path("C:/Organized/report.txt"),
        )
        service.emit_activity(event)
        self.app.processEvents()
        service.product_snapshot_to_return = ProductSnapshot(
            ProductDataState.AVAILABLE,
            ProductMetrics(1, 0, 0, 0),
            (durable,),
        )
        service.release_product_read.set()

        self._wait_until(
            lambda: service.product_read_calls == 2
            and bridge.product_snapshot.metrics.total_processed == 1
        )
        self.assertEqual(len(bridge.product_snapshot.activity), 1)
        self.assertEqual(
            bridge.product_snapshot.activity[0].operation_id,
            operation_id,
        )
        self.assertIsNone(bridge.product_snapshot.activity[0].category)

    def test_shutdown_waits_for_active_product_read_without_late_publication(self):
        service = FakeService()
        service.release_product_read.clear()
        bridge = self._bridge(service)
        published = []
        bridge.product_snapshot_changed.connect(published.append)

        bridge.request_product_refresh()
        self.assertTrue(service.product_read_entered.wait(1))
        bridge.shutdown()
        self.assertEqual(len(service.state_subscribers), 1)
        self.assertEqual(len(service.activity_subscribers), 1)
        service.release_product_read.set()
        self._wait_until(lambda: bridge.is_closed)

        self.assertEqual(published, [])
        self.assertEqual(service.state_subscribers, [])
        self.assertEqual(service.activity_subscribers, [])
        self.assertFalse(bridge._thread.isRunning())

    def test_product_read_failure_does_not_change_monitor_state(self):
        service = FakeService(monitor_state=MonitorState.STOPPED)
        service.product_read_error = RuntimeError("journal unavailable")
        bridge = self._bridge(service)
        failed = QSignalSpy(bridge.product_read_failed)

        bridge.request_product_refresh()
        self._wait_until(lambda: failed.count() == 1)

        self.assertIs(bridge.snapshot.monitor_state, MonitorState.STOPPED)
        self.assertIs(bridge.product_snapshot.state, ProductDataState.ERROR)

    def test_transient_read_failure_retries_live_confirmation_once(self):
        service = FakeService()
        service.product_read_error = RuntimeError("journal temporarily busy")
        bridge = self._bridge(service)
        failed = QSignalSpy(bridge.product_read_failed)
        operation_id = "operation-transient"
        event = ActivityEvent(
            Path("C:/Inbox/transient.txt"),
            "documents",
            "moved",
            move_result=type(
                "Result",
                (),
                {
                    "operation_id": operation_id,
                    "destination": Path("C:/Organized/transient.txt"),
                    "duplicate_of": None,
                    "error": None,
                    "metadata_error": None,
                },
            )(),
        )

        service.emit_activity(event)
        self._wait_until(lambda: failed.count() == 1)
        self.assertEqual(len(bridge.product_snapshot.activity), 1)
        self.assertFalse(bridge.product_snapshot.activity[0].durable)
        service.product_read_error = None
        service.product_snapshot_to_return = ProductSnapshot(
            ProductDataState.AVAILABLE,
            ProductMetrics(1, 0, 0, 0),
            (
                ActivityRecord(
                    "durable-transient",
                    operation_id,
                    event.occurred_at_utc,
                    event.source,
                    "documents",
                    ActivityStatus.COMPLETED,
                    actual_destination=Path("C:/Organized/transient.txt"),
                ),
            ),
        )

        self._wait_until(
            lambda: service.product_read_calls == 2
            and bridge.product_snapshot.metrics.total_processed == 1
        )

        self.assertEqual(len(bridge.product_snapshot.activity), 1)
        self.assertTrue(bridge.product_snapshot.activity[0].durable)

    def test_preview_runs_off_gui_thread_and_coalesces_to_latest_request(self):
        service = FakeService()
        service.release_preview.clear()
        bridge = self._bridge(service)
        previews = QSignalSpy(bridge.preview_changed)

        bridge.request_preview("C:/Inbox/first.txt")
        self.assertTrue(service.preview_entered.wait(1))
        bridge.request_preview("C:/Inbox/second.txt")
        bridge.request_preview("C:/Inbox/latest.txt")
        service.release_preview.set()

        self._wait_until(
            lambda: len(service.preview_calls) == 2 and previews.count() == 1
        )
        self.assertEqual(
            service.preview_calls,
            ["C:/Inbox/first.txt", "C:/Inbox/latest.txt"],
        )
        self.assertNotEqual(service.preview_thread_ids[0], threading.get_ident())
        self.assertEqual(
            previews.at(0)[0].source,
            Path("C:/Inbox/latest.txt"),
        )

    def test_mutating_safety_requests_are_serialized_and_not_duplicated(self):
        service = FakeService()
        service.release_undo.clear()
        bridge = self._bridge(service)
        undo_completed = QSignalSpy(bridge.undo_completed)
        recovery_completed = QSignalSpy(bridge.recovery_action_completed)

        bridge.request_undo("operation-undo")
        bridge.request_undo("operation-undo")
        self.assertTrue(service.undo_entered.wait(1))
        bridge.request_recovery_action(
            "operation-recovery",
            RecoveryAction.APPLY_SAFE_RECOMMENDATION,
        )
        bridge.request_recovery_action(
            "operation-recovery",
            RecoveryAction.APPLY_SAFE_RECOMMENDATION,
        )
        service.release_undo.set()

        self._wait_until(
            lambda: undo_completed.count() == 1
            and recovery_completed.count() == 1
        )
        self.assertEqual(service.undo_calls, ["operation-undo"])
        self.assertEqual(
            service.recovery_action_calls,
            [
                (
                    "operation-recovery",
                    RecoveryAction.APPLY_SAFE_RECOMMENDATION,
                )
            ],
        )
        self.assertEqual(service.max_active_operations, 1)

    def test_undo_availability_is_evaluated_on_worker_thread(self):
        service = FakeService()
        bridge = self._bridge(service)
        availability = QSignalSpy(bridge.undo_availability_changed)
        observed = QSignalSpy(bridge.operation_thread_observed)

        bridge.request_undo_availability("operation-7")
        self._wait_until(lambda: availability.count() == 1)

        self.assertEqual(service.undo_availability_calls, ["operation-7"])
        self.assertEqual(availability.at(0)[0].operation_id, "operation-7")
        worker_ids = [
            observed.at(index)[1]
            for index in range(observed.count())
            if observed.at(index)[0] == "undo_availability"
        ]
        self.assertEqual(len(worker_ids), 1)
        self.assertNotEqual(worker_ids[0], threading.get_ident())

    def test_configuration_read_runs_off_gui_thread_after_bootstrap(self):
        service = FakeService()
        service.configuration_snapshot_to_return = ProductConfigurationSnapshot(
            ConfigurationDataState.AVAILABLE,
            "revision",
        )
        bridge = self._bridge(service)
        changed = QSignalSpy(bridge.configuration_snapshot_changed)

        self._bootstrap(bridge)
        self._wait_until(lambda: changed.count() == 1)

        self.assertEqual(service.configuration_read_calls, 1)
        self.assertNotEqual(
            service.configuration_read_thread_ids[0],
            threading.get_ident(),
        )
        self.assertEqual(bridge.configuration_snapshot.revision, "revision")

    def test_configuration_validation_ignores_stale_response_per_page(self):
        service = FakeService()
        service.release_configuration_validation.clear()
        bridge = self._bridge(service)
        changed = QSignalSpy(bridge.configuration_validation_changed)
        first = self._configuration_candidate(".txt")
        latest = self._configuration_candidate(".pdf")

        bridge.request_configuration_validation("rules", first)
        self.assertTrue(service.configuration_validation_entered.wait(1))
        bridge.request_configuration_validation("rules", latest)
        service.release_configuration_validation.set()

        self._wait_until(
            lambda: len(service.configuration_validation_calls) == 2
            and changed.count() == 1
        )
        self.assertIs(service.configuration_validation_calls[0], first)
        self.assertIs(service.configuration_validation_calls[1], latest)
        self.assertEqual(changed.at(0)[0], "rules")
        self.assertIs(changed.at(0)[1].candidate, latest)

    def test_configuration_validation_keeps_page_contexts_independent(self):
        service = FakeService()
        bridge = self._bridge(service)
        changed = QSignalSpy(bridge.configuration_validation_changed)
        rules_candidate = self._configuration_candidate(".txt")
        folders_candidate = self._configuration_candidate(".pdf")

        bridge.request_configuration_validation("rules", rules_candidate)
        bridge.request_configuration_validation("folders", folders_candidate)

        self._wait_until(lambda: changed.count() == 2)
        contexts = {changed.at(index)[0] for index in range(changed.count())}
        self.assertEqual(contexts, {"rules", "folders"})

    def test_configuration_save_is_serialized_and_not_duplicated(self):
        service = FakeService()
        bridge = self._bridge(service)
        started = QSignalSpy(bridge.configuration_save_started)
        completed = QSignalSpy(bridge.configuration_save_completed)
        candidate = self._configuration_candidate()

        bridge.request_configuration_save("rules", candidate, "revision")
        bridge.request_configuration_save("rules", candidate, "revision")

        self._wait_until(lambda: completed.count() == 1)
        self.assertEqual(started.count(), 1)
        self.assertEqual(len(service.configuration_save_calls), 1)
        self.assertEqual(completed.at(0)[0], "rules")
        self.assertEqual(
            completed.at(0)[1].status,
            ConfigurationSaveStatus.SAVED,
        )


if __name__ == "__main__":
    unittest.main()
