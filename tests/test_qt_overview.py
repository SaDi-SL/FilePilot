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

    def test_first_use_document_path_does_not_require_organization_or_start_monitoring(self):
        from unittest.mock import patch
        from tests.test_qt_library import ControllerStub
        from app.library_catalog import LibrarySnapshot
        bridge = BridgeStub(snapshot(MonitorState.STOPPED, StartupStatus.SETUP_REQUIRED))
        bridge.library = ControllerStub()
        window = MainWindow(bridge)
        window.show()
        self.app.processEvents()
        self.assertIsNone(window._setup_dialog)
        self.assertIs(window.page_stack.currentWidget(), window.overview_page)
        self.assertTrue(all(key == "status" for key, _ in bridge.library.requests))
        with patch("app.ui.qt.widgets.library_workspace.QFileDialog.getExistingDirectory", return_value="") as picker:
            window.overview_page.add_documents_button.click()
            QTest.qWait(20)
            picker.assert_called_once()
        self.assertIs(window.my_files_page.workspace_tabs.currentWidget(), window.my_files_page.library_workspace)
        self.assertFalse(any(key == "add" for key, _ in bridge.library.requests))
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "notes.txt"
            source.write_text("Original content", encoding="utf-8")
            window.show_page("overview")
            with patch("app.ui.qt.widgets.library_workspace.QFileDialog.getExistingDirectory", return_value=folder):
                window.overview_page.add_documents_button.click()
                QTest.qWait(20)
            self.assertEqual(bridge.library.requests[-1], ("add", {"path": folder}))
            bridge.library.result.emit("add", LibrarySnapshot((Path(folder),), 0, 0, 0, (), {}))
            self.assertEqual(bridge.library.requests[-1][0], "index")
            self.assertEqual(source.read_text(), "Original content")
        self.assertEqual(bridge.start_calls, 0)
        self.assertIsNone(window._setup_dialog)
        window.close()

    def test_home_next_step_tracks_library_and_does_not_hide_real_errors(self):
        from app.library_catalog import LibrarySnapshot, LibraryFile
        from app.application_service import ProductMetrics, ProductDataState
        from tests.test_qt_activity import available_snapshot
        page, bridge = self._page(snapshot(MonitorState.STOPPED, StartupStatus.SETUP_REQUIRED))
        page.render_product_snapshot(available_snapshot(metrics=ProductMetrics(0, 0, 0, 0)))
        self.assertTrue(page.metrics_widget.isHidden())
        self.assertTrue(page.recent_activity.isHidden())
        page._library_result("status", LibrarySnapshot((Path("/docs"),), 0, 0, 0, (), {}))
        self.assertIn("folder is saved", page.document_next.text())
        page._library_busy_changed(True)
        self.assertFalse(page.add_documents_button.isEnabled())
        self.assertIn("Preparing", page.document_next.text())
        page._library_busy_changed(False)
        page._library_result("status", LibrarySnapshot((), 1, 0, 0, (LibraryFile(Path("/docs/scan.pdf"), "ocr_required", "", False),), {}))
        self.assertIn("Review issues", page.document_next.text())
        page._library_result("status", LibrarySnapshot((), 1, 1, 0, (), {"cancelled": True}))
        self.assertIn("Continue preparation", page.document_next.text())
        page._library_error("index", "failure")
        self.assertIn("needs attention", page.document_next.text())
        page.render_product_snapshot(available_snapshot(metrics=ProductMetrics(1, 0, 0, 0)))
        self.assertFalse(page.metrics_widget.isHidden())
        error = ProductSnapshot(ProductDataState.ERROR, ProductMetrics.unavailable(), (), "Journal unavailable")
        page.render_product_snapshot(error)
        self.assertFalse(page.metrics_widget.isHidden())
        self.assertFalse(page.recent_activity.isHidden())

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
            self.assertIn("Open Folders", page.state_panel.message_label.text())
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
