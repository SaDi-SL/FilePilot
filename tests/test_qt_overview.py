import os
import re
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication
except ImportError as error:
    raise unittest.SkipTest(
        "PySide6 is required for Qt tests; install requirements-qt.txt"
    ) from error

from app.application_service import (
    MonitorState,
    ProductSnapshot,
    StartupStatus,
)
from app.ui.qt.application import create_application
from app.ui.qt.main_window import MainWindow
from app.ui.qt.pages.overview import OverviewPage
from app.ui.qt.service_bridge import ServiceSnapshot
from app.ui.qt.theme.tokens import COLORS


class BridgeStub(QObject):
    state_changed = Signal(object)
    product_snapshot_changed = Signal(object)
    closed = Signal()
    command_failed = Signal(str, str)

    def __init__(self, snapshot=None):
        super().__init__()
        self.snapshot = snapshot or ServiceSnapshot(None, MonitorState.STOPPED)
        self.product_snapshot = ProductSnapshot.loading()
        self.start_calls = 0
        self.stop_calls = 0
        self.shutdown_calls = 0
        self.auto_close = True
        self.refresh_calls = 0

    def request_start(self):
        self.start_calls += 1

    def request_stop(self):
        self.stop_calls += 1

    def request_product_refresh(self, limit=100):
        self.refresh_calls += 1

    def shutdown(self):
        self.shutdown_calls += 1
        if self.auto_close:
            self.closed.emit()


def snapshot(
    state,
    startup=StartupStatus.READY,
    *,
    error=None,
    operation_ids=(),
    reasons=(),
    failed_folders=(),
):
    return ServiceSnapshot(
        startup_status=startup,
        monitor_state=state,
        error=error,
        blocking_operation_ids=operation_ids,
        blocking_reasons=reasons,
        failed_folders=failed_folders,
    )


class QtOverviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_application([])

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            widget.close()
        self.app.processEvents()

    def _page(self, state_snapshot):
        bridge = BridgeStub(state_snapshot)
        page = OverviewPage(bridge)
        page.render_state(state_snapshot)
        return page, bridge

    def test_stopped_state_enables_only_start(self):
        page, _ = self._page(snapshot(MonitorState.STOPPED))
        self.assertEqual(page.state_panel.status_badge.text(), "Stopped")
        self.assertTrue(page.state_panel.start_button.isEnabled())
        self.assertFalse(page.state_panel.stop_button.isEnabled())

    def test_starting_state_disables_actions(self):
        page, _ = self._page(snapshot(MonitorState.STARTING))
        self.assertEqual(page.state_panel.status_badge.text(), "Starting")
        self.assertFalse(page.state_panel.start_button.isEnabled())
        self.assertFalse(page.state_panel.stop_button.isEnabled())

    def test_running_state_enables_only_stop(self):
        page, _ = self._page(snapshot(MonitorState.RUNNING))
        self.assertEqual(page.state_panel.status_badge.text(), "Running")
        self.assertFalse(page.state_panel.start_button.isEnabled())
        self.assertTrue(page.state_panel.stop_button.isEnabled())

    def test_stopping_and_error_states_are_truthful(self):
        page, _ = self._page(snapshot(MonitorState.STOPPING))
        self.assertEqual(page.state_panel.status_badge.text(), "Stopping")
        self.assertFalse(page.state_panel.start_button.isEnabled())
        self.assertFalse(page.state_panel.stop_button.isEnabled())

        failure = snapshot(MonitorState.ERROR, error="Observer failed")
        page.render_state(failure)
        self.assertEqual(page.state_panel.status_badge.text(), "Error")
        self.assertIn("Observer failed", page.state_panel.message_label.text())
        self.assertFalse(page.state_panel.start_button.isEnabled())
        self.assertTrue(page.state_panel.stop_button.isEnabled())

    def test_runtime_error_shows_failed_folders(self):
        failure = snapshot(
            MonitorState.ERROR,
            error="Some folders did not start",
            failed_folders=("C:/Inbox", "D:/Scans"),
        )
        page, _ = self._page(failure)

        self.assertFalse(page.state_panel.details_frame.isHidden())
        self.assertIn("C:/Inbox", page.state_panel.details_label.text())
        self.assertIn("D:/Scans", page.state_panel.details_label.text())

    def test_blocked_state_disables_start_and_shows_evidence(self):
        blocked = snapshot(
            MonitorState.BLOCKED,
            StartupStatus.BLOCKED,
            error="Recovery review required",
            operation_ids=("operation-7", "operation-9"),
            reasons=("Destination identity changed",),
        )
        page, bridge = self._page(blocked)

        self.assertEqual(page.state_panel.status_badge.text(), "Action required")
        self.assertFalse(page.state_panel.start_button.isEnabled())
        self.assertTrue(page.state_panel.details_frame.isHidden() is False)
        self.assertIn("operation-7", page.state_panel.details_label.text())
        self.assertIn("Destination identity changed", page.state_panel.details_label.text())
        QTest.mouseClick(
            page.state_panel.start_button,
            Qt.MouseButton.LeftButton,
        )
        self.assertEqual(bridge.start_calls, 0)

    def test_setup_required_is_non_destructive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            page, bridge = self._page(
                snapshot(MonitorState.STOPPED, StartupStatus.SETUP_REQUIRED)
            )

            self.assertEqual(
                page.state_panel.status_badge.text(),
                "Setup required",
            )
            self.assertFalse(page.state_panel.start_button.isEnabled())
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(bridge.start_calls, 0)

    def test_navigation_changes_pages(self):
        bridge = BridgeStub(snapshot(MonitorState.STOPPED))
        window = MainWindow(bridge)

        window.navigation.button("activity").click()
        self.assertEqual(
            window.page_stack.currentIndex(),
            window._page_indexes["activity"],
        )
        window.navigation.button("settings").click()
        self.assertEqual(
            window.page_stack.currentIndex(),
            window._page_indexes["settings"],
        )

    def test_window_supports_1366x768_and_compact_width(self):
        bridge = BridgeStub(snapshot(MonitorState.STOPPED))
        window = MainWindow(bridge)
        self.assertLessEqual(window.width(), 1180)
        self.assertLessEqual(window.height(), 680)
        self.assertLessEqual(window.minimumWidth(), 760)
        self.assertLessEqual(window.minimumHeight(), 520)
        window.resize(1366, 768)
        window.show()
        self.app.processEvents()

        self.assertEqual(
            window.overview_page.scroll_area.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self.assertEqual(
            window.overview_page.scroll_area.horizontalScrollBar().maximum(),
            0,
        )
        self.assertEqual(window.overview_page.metrics_layout.getItemPosition(0)[:2], (0, 0))
        self.assertEqual(window.overview_page.metrics_layout.getItemPosition(3)[:2], (0, 3))

        window.resize(760, 520)
        self.app.processEvents()
        self.assertEqual(
            window.navigation.width(),
            window.navigation.COMPACT_WIDTH,
        )
        self.assertEqual(window.overview_page.metrics_layout.getItemPosition(3)[:2], (1, 1))
        self.assertEqual(window.overview_page.metrics_layout.columnStretch(2), 0)

    def test_dark_palette_covers_scroll_viewport_and_page_surfaces(self):
        bridge = BridgeStub(snapshot(MonitorState.STOPPED))
        window = MainWindow(bridge)
        window.resize(1366, 768)
        window.show()
        self.app.processEvents()

        expected = COLORS.background.lower()
        surfaces = (
            window.centralWidget(),
            window.page_stack,
            window.overview_page,
            window.overview_page.scroll_area.viewport(),
            window.overview_page.content,
        )
        for surface in surfaces:
            color = surface.palette().color(QPalette.ColorRole.Window).name()
            self.assertEqual(color.lower(), expected, surface.objectName())
        self.assertTrue(
            window.overview_page.scroll_area.viewport().testAttribute(
                Qt.WidgetAttribute.WA_StyledBackground
            )
        )
        self.assertTrue(
            window.overview_page.content.testAttribute(
                Qt.WidgetAttribute.WA_StyledBackground
            )
        )

    def test_navigation_icons_are_local_and_accessible_when_compact(self):
        bridge = BridgeStub(snapshot(MonitorState.STOPPED))
        window = MainWindow(bridge)
        window.resize(760, 520)
        window.show()
        self.app.processEvents()

        for item in window.navigation._buttons.values():
            self.assertFalse(item.icon().isNull())
            self.assertTrue(item.accessibleName())
            self.assertTrue(item.toolTip())

        navigation_source = (
            Path(__file__).parents[1] / "app" / "ui" / "qt" / "navigation.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("StandardPixmap", navigation_source)
        self.assertNotIn("standardIcon", navigation_source)

    def test_failed_shutdown_restores_window_for_retry(self):
        bridge = BridgeStub(snapshot(MonitorState.RUNNING))
        bridge.auto_close = False
        window = MainWindow(bridge)
        window.show()

        window.close()
        self.assertEqual(bridge.shutdown_calls, 1)
        self.assertFalse(window.isEnabled())

        bridge.command_failed.emit("shutdown", "folder remained active")
        self.app.processEvents()

        self.assertTrue(window.isEnabled())
        window.close()
        self.assertEqual(bridge.shutdown_calls, 2)

    def test_primary_actions_are_keyboard_focusable(self):
        page, _ = self._page(snapshot(MonitorState.STOPPED))
        page.show()
        page.state_panel.start_button.setFocus()
        self.app.processEvents()

        self.assertTrue(page.state_panel.start_button.hasFocus())
        self.assertNotEqual(
            page.state_panel.start_button.focusPolicy(),
            Qt.FocusPolicy.NoFocus,
        )
        self.assertTrue(page.state_panel.start_button.accessibleName())

    def test_qt_layer_respects_service_and_style_boundaries(self):
        qt_root = Path(__file__).parents[1] / "app" / "ui" / "qt"
        sources = {
            path: path.read_text(encoding="utf-8")
            for path in qt_root.rglob("*.py")
        }
        joined = "\n".join(sources.values())

        for forbidden in (
            "app.watcher",
            "app.mover",
            "app.operation_journal",
            "app.recovery",
            "app.main",
            "app.stats",
            "app.config_loader",
            "app.hash_manager",
        ):
            self.assertNotIn(forbidden, joined)
        self.assertIsNone(re.search(r"service\s*\.\s*monitor(?!_)", joined))
        self.assertIsNone(re.search(r"service\s*\.\s*config\b", joined))
        for path, source in sources.items():
            if "theme" not in path.parts and path.name != "application.py":
                self.assertNotIn("setStyleSheet", source, str(path))


if __name__ == "__main__":
    unittest.main()
