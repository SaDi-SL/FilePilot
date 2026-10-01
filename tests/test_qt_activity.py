import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ImportError as error:
    raise unittest.SkipTest(
        "PySide6 is required for Qt tests; install requirements-qt.txt"
    ) from error

from app.application_service import (
    ActivityRecord,
    ActivityStatus,
    MonitorState,
    ProductDataState,
    ProductMetrics,
    ProductSnapshot,
    StartupStatus,
)
from app.ui.qt.application import create_application
from app.ui.qt.main_window import MainWindow
from app.ui.qt.pages.activity import ActivityPage
from app.ui.qt.pages.placeholder import PlaceholderPage
from app.ui.qt.service_bridge import ServiceSnapshot


class ActivityBridgeStub(QObject):
    state_changed = Signal(object)
    product_snapshot_changed = Signal(object)
    closed = Signal()
    command_failed = Signal(str, str)

    def __init__(self, product_snapshot=None, service_snapshot=None):
        super().__init__()
        self.snapshot = service_snapshot or ServiceSnapshot(
            StartupStatus.READY,
            MonitorState.STOPPED,
        )
        self.product_snapshot = product_snapshot or ProductSnapshot.loading()
        self.refresh_calls = []
        self.start_calls = 0
        self.stop_calls = 0

    def request_product_refresh(self, limit=100):
        self.refresh_calls.append(limit)

    def request_start(self):
        self.start_calls += 1

    def request_stop(self):
        self.stop_calls += 1

    def shutdown(self):
        self.closed.emit()

    def publish(self, snapshot):
        self.product_snapshot = snapshot
        self.product_snapshot_changed.emit(snapshot)


def activity_record(
    operation_id,
    *,
    minutes=0,
    status=ActivityStatus.COMPLETED,
    filename=None,
    destination=None,
    category="documents",
):
    filename = filename or f"{operation_id}.txt"
    return ActivityRecord(
        record_id=operation_id,
        operation_id=operation_id,
        occurred_at_utc=datetime.now(timezone.utc) + timedelta(minutes=minutes),
        source_path=Path("C:/Inbox") / filename,
        category=category,
        status=status,
        actual_destination=(
            Path(destination)
            if destination is not None
            else Path("C:/Organized") / category / filename
        ),
    )


def available_snapshot(*records, metrics=None, has_more=False):
    return ProductSnapshot(
        ProductDataState.AVAILABLE,
        metrics or ProductMetrics(0, 0, 0, 0),
        tuple(records),
        has_more=has_more,
    )


class QtActivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_application([])

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            widget.close()
        self.app.processEvents()

    def test_overview_renders_authoritative_metrics(self):
        snapshot = available_snapshot(metrics=ProductMetrics(12, 2, 3, 1))
        bridge = ActivityBridgeStub(snapshot)
        window = MainWindow(bridge)

        self.assertEqual(window.overview_page.processed_card.value_label.text(), "12")
        self.assertEqual(window.overview_page.failed_card.value_label.text(), "2")
        self.assertEqual(window.overview_page.duplicates_card.value_label.text(), "3")
        self.assertEqual(window.overview_page.review_card.value_label.text(), "1")

    def test_overview_does_not_fabricate_unavailable_metrics(self):
        snapshot = ProductSnapshot.unavailable("Journal is unavailable")
        bridge = ActivityBridgeStub(snapshot)
        window = MainWindow(bridge)

        for card in window.overview_page._metric_cards:
            self.assertEqual(card.value_label.text(), "Unavailable")
            self.assertNotEqual(card.value_label.text(), "0")

    def test_overview_recent_activity_is_authoritative_and_limited(self):
        records = tuple(activity_record(str(index), minutes=-index) for index in range(9))
        bridge = ActivityBridgeStub(available_snapshot(*records))
        window = MainWindow(bridge)

        table = window.overview_page.recent_table
        self.assertEqual(table.topLevelItemCount(), 6)
        self.assertEqual(table.topLevelItem(0).text(table.FILE_COLUMN), "0.txt")

    def test_empty_and_error_states_are_useful(self):
        empty_bridge = ActivityBridgeStub(available_snapshot())
        empty_page = ActivityPage(empty_bridge)
        self.assertFalse(empty_page.state_label.isHidden())
        self.assertIn("No durable", empty_page.state_label.text())

        empty_bridge.publish(ProductSnapshot.unavailable("Journal unavailable"))
        self.assertIn("Journal unavailable", empty_page.state_label.text())
        self.assertTrue(empty_page.table.isHidden())

    def test_activity_navigation_replaces_placeholder_and_view_all_works(self):
        bridge = ActivityBridgeStub(available_snapshot(activity_record("one")))
        window = MainWindow(bridge)

        self.assertIsInstance(window.activity_page, ActivityPage)
        self.assertNotIsInstance(window.activity_page, PlaceholderPage)
        window.overview_page.view_all_button.click()

        self.assertEqual(
            window.page_stack.currentIndex(),
            window._page_indexes["activity"],
        )
        self.assertGreaterEqual(len(bridge.refresh_calls), 1)

    def test_activity_preserves_newest_first_order_and_operation_id(self):
        newest = activity_record("newest", minutes=2)
        older = activity_record("older", minutes=-2)
        bridge = ActivityBridgeStub(available_snapshot(newest, older))
        page = ActivityPage(bridge)

        self.assertEqual(page.table.topLevelItem(0).text(page.table.FILE_COLUMN), "newest.txt")
        page.table.setCurrentItem(page.table.topLevelItem(0))
        self.assertEqual(page.table.selected_operation_id(), "newest")

    def test_statuses_render_with_text_and_indicators(self):
        statuses = (
            ActivityStatus.COMPLETED,
            ActivityStatus.DUPLICATE,
            ActivityStatus.FAILED,
            ActivityStatus.NEEDS_REVIEW,
            ActivityStatus.WARNING,
            ActivityStatus.UNKNOWN,
        )
        records = tuple(
            activity_record(str(index), status=status)
            for index, status in enumerate(statuses)
        )
        page = ActivityPage(ActivityBridgeStub(available_snapshot(*records)))

        rendered = {
            page.table.topLevelItem(index).text(page.table.RESULT_COLUMN)
            for index in range(page.table.topLevelItemCount())
        }
        self.assertEqual(
            rendered,
            {"Completed", "Duplicate", "Failed", "Needs review", "Warning", "Unknown"},
        )
        for index in range(page.table.topLevelItemCount()):
            self.assertFalse(
                page.table.topLevelItem(index).icon(page.table.RESULT_COLUMN).isNull()
            )

    def test_long_paths_are_elided_without_losing_tooltips(self):
        long_name = ("quarterly-financial-report-" * 8) + ".pdf"
        destination = "C:/Organized/Very/Long/Category/" + long_name
        page = ActivityPage(
            ActivityBridgeStub(
                available_snapshot(
                    activity_record(
                        "long-path",
                        filename=long_name,
                        destination=destination,
                    )
                )
            )
        )
        page.resize(800, 600)
        page.show()
        self.app.processEvents()

        item = page.table.topLevelItem(0)
        self.assertIn(long_name, item.toolTip(page.table.FILE_COLUMN))
        self.assertEqual(
            item.toolTip(page.table.DESTINATION_COLUMN),
            str(Path(destination)),
        )
        self.assertEqual(
            page.table.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )

    def test_live_product_snapshot_updates_both_surfaces_once(self):
        bridge = ActivityBridgeStub(available_snapshot())
        window = MainWindow(bridge)
        record = activity_record("operation-live")

        bridge.publish(available_snapshot(record, metrics=ProductMetrics(1, 0, 0, 0)))

        self.assertEqual(window.overview_page.recent_table.topLevelItemCount(), 1)
        self.assertEqual(window.activity_page.table.topLevelItemCount(), 1)
        self.assertEqual(
            window.activity_page.table.topLevelItem(0).data(
                0, Qt.ItemDataRole.UserRole
            ),
            "operation-live",
        )

    def test_live_rows_are_visibly_distinct_from_durable_history(self):
        record = activity_record("live-only")
        record = ActivityRecord(
            record.record_id,
            record.operation_id,
            record.occurred_at_utc,
            record.source_path,
            record.category,
            record.status,
            actual_destination=record.actual_destination,
            durable=False,
        )
        page = ActivityPage(ActivityBridgeStub(available_snapshot(record)))

        self.assertIn(
            "Live",
            page.table.topLevelItem(0).text(page.table.RESULT_COLUMN),
        )

    def test_live_rows_remain_visible_when_durable_history_is_unavailable(self):
        record = activity_record("live-unavailable")
        live = ActivityRecord(
            record.record_id,
            record.operation_id,
            record.occurred_at_utc,
            record.source_path,
            record.category,
            record.status,
            actual_destination=record.actual_destination,
            durable=False,
        )
        snapshot = ProductSnapshot(
            ProductDataState.ERROR,
            ProductMetrics.unavailable(),
            (live,),
            error="Journal temporarily busy",
        )
        bridge = ActivityBridgeStub(snapshot)
        window = MainWindow(bridge)

        self.assertFalse(window.activity_page.table.isHidden())
        self.assertEqual(window.activity_page.table.topLevelItemCount(), 1)
        self.assertIn("unavailable", window.activity_page.summary_label.text())
        self.assertFalse(window.overview_page.recent_table.isHidden())
        self.assertEqual(window.overview_page.recent_table.topLevelItemCount(), 1)

    def test_filtering_is_bounded_and_non_destructive(self):
        records = (
            activity_record("complete"),
            activity_record("duplicate", status=ActivityStatus.DUPLICATE),
        )
        page = ActivityPage(ActivityBridgeStub(available_snapshot(*records)))

        page.filter_combo.setCurrentIndex(2)

        self.assertEqual(page.table.topLevelItemCount(), 1)
        self.assertEqual(
            page.table.topLevelItem(0).text(page.table.RESULT_COLUMN),
            "Duplicate",
        )

    def test_filtered_empty_state_discloses_bounded_history(self):
        page = ActivityPage(
            ActivityBridgeStub(
                available_snapshot(activity_record("complete"), has_more=True)
            )
        )

        page.filter_combo.setCurrentIndex(3)

        self.assertIn("Older activity was not searched", page.state_label.text())
        self.assertIn("bounded history", page.summary_label.text())

    def test_activity_is_viewable_while_stopped_and_blocked_remains_fail_closed(self):
        blocked = ServiceSnapshot(
            StartupStatus.BLOCKED,
            MonitorState.BLOCKED,
            error="Recovery review required",
        )
        bridge = ActivityBridgeStub(
            available_snapshot(activity_record("blocked-visible")),
            blocked,
        )
        window = MainWindow(bridge)
        window.show_page("activity")

        self.assertEqual(window.activity_page.table.topLevelItemCount(), 1)
        window.show_page("overview")
        self.assertFalse(window.overview_page.state_panel.start_button.isEnabled())

    def test_refresh_and_activity_rows_are_keyboard_accessible(self):
        bridge = ActivityBridgeStub(available_snapshot(activity_record("keyboard")))
        page = ActivityPage(bridge)
        page.show()
        page.refresh_button.setFocus()
        self.app.processEvents()

        self.assertTrue(page.refresh_button.hasFocus())
        QTest.keyClick(page.refresh_button, Qt.Key.Key_Space)
        self.assertGreaterEqual(len(bridge.refresh_calls), 1)
        self.assertNotEqual(page.table.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertTrue(page.table.accessibleName())

    def test_1366x768_activity_layout_has_no_horizontal_scroll(self):
        records = tuple(activity_record(str(index)) for index in range(12))
        bridge = ActivityBridgeStub(available_snapshot(*records, has_more=True))
        window = MainWindow(bridge)
        window.resize(1366, 768)
        window.show()
        window.show_page("activity")
        self.app.processEvents()

        self.assertEqual(
            window.activity_page.table.horizontalScrollBar().maximum(),
            0,
        )
        self.assertGreater(window.activity_page.table.viewport().width(), 0)
        self.assertIn(
            "more activity is available",
            window.activity_page.summary_label.text(),
        )


if __name__ == "__main__":
    unittest.main()
