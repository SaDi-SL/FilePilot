import os
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
except ImportError as error:
    raise unittest.SkipTest(
        "PySide6 is required for Qt tests; install requirements-qt.txt"
    ) from error

from app.application_service import (
    ActivityRecord,
    ActivityStatus,
    MonitorState,
    OperationPreview,
    PreviewStatus,
    ProductDataState,
    ProductMetrics,
    ProductSnapshot,
    RecoveryAction,
    RecoveryActionResult,
    RecoveryActionStatus,
    RecoveryItem,
    RecoverySnapshot,
    SafetyDataState,
    StartupStatus,
    UndoAvailability,
)
from app.ui.qt.pages.activity import ActivityPage
from app.ui.qt.pages.overview import OverviewPage
from app.ui.qt.pages.recovery import RecoveryPage
from app.ui.qt.safety_dialogs import PreviewDialog
from app.ui.qt.service_bridge import ServiceSnapshot


class SafetyBridgeStub(QObject):
    state_changed = Signal(object)
    product_snapshot_changed = Signal(object)
    recovery_snapshot_changed = Signal(object)
    preview_changed = Signal(object)
    undo_availability_changed = Signal(object)
    undo_started = Signal(str)
    undo_completed = Signal(object)
    recovery_action_started = Signal(str)
    recovery_action_completed = Signal(object)
    safety_request_failed = Signal(str, str)
    closed = Signal()
    command_failed = Signal(str, str)

    def __init__(self, product_snapshot, recovery_snapshot):
        super().__init__()
        self.snapshot = ServiceSnapshot(StartupStatus.READY, MonitorState.STOPPED)
        self.product_snapshot = product_snapshot
        self.recovery_snapshot = recovery_snapshot
        self.preview_requests = []
        self.undo_availability_requests = []
        self.undo_requests = []
        self.recovery_refresh_requests = []
        self.recovery_action_requests = []

    def request_product_refresh(self, limit=100):
        pass

    def request_start(self):
        pass

    def request_stop(self):
        pass

    def request_preview(self, source):
        self.preview_requests.append(source)

    def request_undo_availability(self, operation_id):
        self.undo_availability_requests.append(operation_id)

    def request_undo(self, operation_id):
        self.undo_requests.append(operation_id)

    def request_recovery_refresh(self, limit=100, offset=0):
        self.recovery_refresh_requests.append((limit, offset))

    def request_recovery_action(self, operation_id, action):
        self.recovery_action_requests.append((operation_id, action))

    def shutdown(self):
        self.closed.emit()

    def publish_state(self, state):
        self.snapshot = ServiceSnapshot(StartupStatus.READY, state)
        self.state_changed.emit(self.snapshot)


def _record():
    return ActivityRecord(
        "record-1",
        "operation-1",
        datetime.now(timezone.utc),
        Path("C:/Inbox/report.txt"),
        "documents",
        ActivityStatus.COMPLETED,
        actual_destination=Path("C:/Organized/documents/report.txt"),
    )


def _recovery_items():
    actionable = RecoveryItem(
        "recovery-safe",
        "safe.txt",
        Path("C:/Inbox/safe.txt"),
        Path("C:/Organized/safe.txt"),
        "Safe recovery available",
        "SOURCE_UNCHANGED",
        "The original source is unchanged, so the operation can be aborted safely.",
        ("Original source exists", "Destination is absent"),
        (RecoveryAction.APPLY_SAFE_RECOMMENDATION,),
        False,
        True,
    )
    manual = RecoveryItem(
        "recovery-review",
        "review.txt",
        Path("C:/Inbox/review.txt"),
        Path("C:/Organized/review.txt"),
        "Manual review required",
        "AMBIGUOUS_EVIDENCE",
        "File identity cannot be established safely.",
        ("Original source could not be inspected",),
        (),
        True,
        True,
    )
    return actionable, manual


class QtSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            widget.close()
        self.app.processEvents()

    def _bridge(self):
        record = _record()
        product = ProductSnapshot(
            ProductDataState.AVAILABLE,
            ProductMetrics(1, 0, 0, 1),
            (record,),
        )
        recovery = RecoverySnapshot(
            SafetyDataState.AVAILABLE,
            _recovery_items(),
            2,
        )
        return SafetyBridgeStub(product, recovery)

    def test_operation_details_enables_undo_only_after_matching_evaluation(self):
        bridge = self._bridge()
        page = ActivityPage(bridge)
        page.table.setCurrentItem(page.table.topLevelItem(0))
        page._open_details()
        dialog = page._details_dialog
        self.assertIsNotNone(dialog)
        self.assertEqual(bridge.undo_availability_requests, ["operation-1"])
        self.assertFalse(dialog.undo_button.isEnabled())

        bridge.undo_availability_changed.emit(
            UndoAvailability("different-operation", True, "Available")
        )
        self.assertFalse(dialog.undo_button.isEnabled())
        bridge.undo_availability_changed.emit(
            UndoAvailability(
                "operation-1",
                True,
                "Current evidence permits Undo.",
                Path("C:/Organized/documents/report.txt"),
                Path("C:/Inbox/report.txt"),
            )
        )
        self.assertTrue(dialog.undo_button.isEnabled())

        bridge.publish_state(MonitorState.RUNNING)
        self.assertFalse(dialog.undo_button.isEnabled())
        self.assertIn("stop monitoring", dialog.undo_button.text().lower())
        bridge.publish_state(MonitorState.STOPPED)
        self.assertTrue(dialog.undo_button.isEnabled())

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            QTest.mouseClick(dialog.undo_button, Qt.MouseButton.LeftButton)
        self.assertEqual(bridge.undo_requests, ["operation-1"])

    def test_preview_dialog_is_advisory_and_has_no_execute_action(self):
        bridge = self._bridge()
        dialog = PreviewDialog(bridge)
        dialog.path_input.setText("C:/Inbox/report.txt")
        QTest.mouseClick(dialog.preview_button, Qt.MouseButton.LeftButton)
        self.assertEqual(bridge.preview_requests, ["C:/Inbox/report.txt"])

        bridge.preview_changed.emit(
            OperationPreview(
                SafetyDataState.AVAILABLE,
                PreviewStatus.READY,
                Path("C:/Inbox/report.txt"),
                category="documents",
                proposed_destination=Path(
                    "C:/Organized/documents/report (1).txt"
                ),
                destination_collision=True,
                alternative_name_required=True,
                message="A collision-safe destination was selected.",
            )
        )

        self.assertIn("collision", dialog.result_title.text().lower())
        self.assertIn("no folders", dialog.result_copy.text().lower())
        button_text = " ".join(
            button.text().lower() for button in dialog.findChildren(QPushButton)
        )
        self.assertNotIn("execute", button_text)
        self.assertNotIn("move now", button_text)

    def test_unavailable_preview_displays_service_message(self):
        bridge = self._bridge()
        dialog = PreviewDialog(bridge)

        bridge.preview_changed.emit(
            OperationPreview.unavailable(
                "C:/Inbox/report.txt",
                "Preview requires a ready FilePilot configuration.",
            )
        )

        self.assertEqual(dialog.result_title.text(), "Preview unavailable")
        self.assertIn("ready", dialog.result_copy.text().lower())

    def test_recovery_page_exposes_only_approved_action_and_disables_while_running(self):
        bridge = self._bridge()
        page = RecoveryPage(bridge)
        self.assertEqual(page.table.topLevelItemCount(), 2)

        page.table.setCurrentItem(page.table.topLevelItem(0))
        self.assertTrue(page.action_button.isEnabled())
        bridge.publish_state(MonitorState.RUNNING)
        self.assertFalse(page.action_button.isEnabled())
        bridge.publish_state(MonitorState.STOPPED)

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            QTest.mouseClick(page.action_button, Qt.MouseButton.LeftButton)
        self.assertEqual(
            bridge.recovery_action_requests,
            [
                (
                    "recovery-safe",
                    RecoveryAction.APPLY_SAFE_RECOMMENDATION,
                )
            ],
        )

        page.table.setCurrentItem(page.table.topLevelItem(1))
        self.assertFalse(page.action_button.isEnabled())
        self.assertIn("manual", page.action_button.text().lower())
        all_text = " ".join(
            button.text().lower() for button in page.findChildren(QPushButton)
        )
        self.assertNotIn("force", all_text)
        self.assertNotIn("overwrite", all_text)

    def test_safe_recovery_waiting_for_stop_is_not_labeled_manual_review(self):
        bridge = self._bridge()
        page = RecoveryPage(bridge)
        safe = replace(_recovery_items()[0], available_actions=())
        page.render_snapshot(
            RecoverySnapshot(SafetyDataState.AVAILABLE, (safe,), 1)
        )
        page.table.setCurrentItem(page.table.topLevelItem(0))

        self.assertIn("stop monitoring", page.action_button.text().lower())
        self.assertNotIn("manual", page.action_button.text().lower())

        bridge.recovery_snapshot = RecoverySnapshot(
            SafetyDataState.AVAILABLE,
            (safe,),
            1,
        )
        overview = OverviewPage(bridge)
        self.assertIn(
            "after monitoring stops",
            overview.recovery_attention_copy.text().lower(),
        )

    def test_recovery_action_busy_state_prevents_duplicate_submission(self):
        bridge = self._bridge()
        page = RecoveryPage(bridge)
        page.table.setCurrentItem(page.table.topLevelItem(0))
        bridge.recovery_action_started.emit("recovery-safe")
        self.assertFalse(page.action_button.isEnabled())

        bridge.recovery_action_completed.emit(
            RecoveryActionResult(
                "recovery-safe",
                RecoveryActionStatus.RECONCILED,
                "Recovered safely.",
            )
        )
        self.assertTrue(page.action_button.isEnabled())
        self.assertIn("safely", page.outcome_label.text().lower())

    def test_recovery_pagination_requests_bounded_next_page(self):
        bridge = self._bridge()
        page = RecoveryPage(bridge)
        first_item = _recovery_items()[0]
        page.render_snapshot(
            RecoverySnapshot(
                SafetyDataState.AVAILABLE,
                (first_item,),
                201,
                True,
                offset=100,
            )
        )

        QTest.mouseClick(page.next_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(page.previous_button, Qt.MouseButton.LeftButton)

        self.assertEqual(
            bridge.recovery_refresh_requests,
            [(100, 200), (100, 0)],
        )
        self.assertIn("101-101 of 201", page.inventory_label.text())

    def test_overview_surfaces_recovery_attention_without_raw_controls(self):
        bridge = self._bridge()
        page = OverviewPage(bridge)

        self.assertFalse(page.recovery_attention.isHidden())
        self.assertIn("2 interrupted", page.recovery_attention_title.text())
        self.assertIn("manual review", page.recovery_attention_copy.text())


if __name__ == "__main__":
    unittest.main()
