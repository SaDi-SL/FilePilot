import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

try:
    from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool, Signal
    from PySide6.QtTest import QTest
except ImportError as error:
    raise unittest.SkipTest("PySide6 is required for Qt setup tests") from error
from app.product_configuration import (ConfigurationDataState, ProductConfigurationSnapshot,
                                      ProductFolderSettings, ProductWatchFolder)
from app.local_ai_readiness import LocalAIReadiness
from app.ui.qt.application import create_application
from app.ui.qt.setup_dialog import SetupDialog
from tests.test_qt_configuration_pages import ConfigurationBridgeStub
from tests.test_qt_settings import available_snapshot


class SetupBridge(ConfigurationBridgeStub):
    settings_snapshot_changed = Signal(object)
    settings_validation_changed = Signal(str, object)
    settings_save_started = Signal(str)
    settings_save_completed = Signal(str, object)
    settings_request_failed = Signal(str, str, str)

    def __init__(self, config):
        super().__init__(config)
        self.settings_snapshot = available_snapshot()
        self.settings_save_requests = []

    def request_settings_save(self, *args):
        self.settings_save_requests.append(args)


class SetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_application([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        incoming, organized = root / "incoming", root / "organized"
        incoming.mkdir(); organized.mkdir()
        self.config = ProductConfigurationSnapshot(ConfigurationDataState.AVAILABLE, "a" * 64,
            ProductFolderSettings((ProductWatchFolder(incoming, "Incoming", True, True),),
                                  organized, True, False, True, True))
        self.bridge = SetupBridge(self.config)
        self.dialog = SetupDialog(self.bridge)

    def tearDown(self):
        QThreadPool.globalInstance().waitForDone(3000)
        self.dialog.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def test_invalid_or_unsaved_folders_cannot_complete(self):
        self.dialog.folders.destination_input.setText("missing")
        self.dialog._next()
        self.assertEqual(self.dialog.stack.currentIndex(), 0)
        self.assertIn("Save or revert", self.dialog.notice.text())
        self.dialog.folders._dirty = False
        self.bridge.configuration_snapshot = replace(self.config, issues=(object(),))
        self.dialog._next()
        self.assertEqual(self.dialog.stack.currentIndex(), 0)
        self.assertIn("valid watch folders", self.dialog.notice.text())

    def test_local_ai_optional_and_monitoring_not_started(self):
        self.dialog._next()
        self.assertEqual(self.dialog.stack.currentIndex(), 1)
        self.dialog._next()
        self.assertEqual(self.dialog.stack.currentIndex(), 2)
        self.assertIn("not verified; optional", self.dialog.summary.toPlainText())
        self.assertIn("Monitoring: stopped", self.dialog.summary.toPlainText())
        completed = []
        self.dialog.index_requested.connect(lambda: completed.append(True))
        self.dialog._go_to_index()
        self.assertEqual(completed, [True])
        self.assertEqual(self.bridge.save_requests, [])
        self.assertEqual(self.bridge.settings_save_requests, [])

    def test_async_inventory_does_not_probe_on_open_and_stale_result_ignored(self):
        threads = []
        result = LocalAIReadiness(True, ("mistral:latest", "bge-m3:latest"), "mistral", True, "bge-m3", True)
        def probe(model):
            threads.append(threading.get_ident())
            return result
        with patch("app.ui.qt.setup_dialog.check_local_ai", side_effect=probe) as request:
            self.assertEqual(request.call_count, 0)
            self.dialog._check_ai()
            for _ in range(100):
                if self.dialog._result:
                    break
                QTest.qWait(10)
            self.assertTrue(self.dialog._result.installed)
            self.assertNotEqual(threads[0], threading.get_ident())
            generation = self.dialog._probe_generation
            new = replace(self.bridge.settings_snapshot, ai=replace(self.bridge.settings_snapshot.ai, ollama_model="gemma4:e4b-it-qat"), revision="b" * 64)
            self.bridge.settings_snapshot = new
            self.bridge.settings_snapshot_changed.emit(new)
            self.assertIsNone(self.dialog._result)
            self.dialog._probe_finished((generation, result))
            self.assertIsNone(self.dialog._result)

    def test_model_choice_requires_save_before_probe_and_next(self):
        result = LocalAIReadiness(True, ("gemma4:e4b-it-qat", "bge-m3:latest"), "mistral", False, "bge-m3", True)
        self.dialog._probe_finished((self.dialog._probe_generation, result))
        self.dialog.models.setCurrentText("gemma4:e4b-it-qat")
        self.dialog._use_model()
        self.assertTrue(self.dialog.settings._dirty)
        with patch("app.ui.qt.setup_dialog.check_local_ai") as probe:
            self.dialog._check_ai()
            self.assertFalse(probe.called)
        self.dialog._next()
        self.assertEqual(self.dialog.stack.currentIndex(), 0)

    def test_closing_during_save_is_blocked_and_unsaved_discard_can_cancel(self):
        self.dialog.show()
        self.dialog.folders._saving = True
        self.dialog.reject()
        self.assertTrue(self.dialog.isVisible())
        self.dialog.folders._saving = False
        self.dialog.settings._dirty = True
        from PySide6.QtWidgets import QMessageBox
        with patch("app.ui.qt.setup_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
            self.dialog.reject()
            self.assertTrue(self.dialog.isVisible())

    def test_compact_steps_keep_navigation_inside_dialog(self):
        self.dialog.resize(680, 540)
        self.dialog.show()
        self.app.processEvents()
        for step in range(3):
            self.dialog.stack.setCurrentIndex(step)
            self.dialog._refresh()
            self.app.processEvents()
            self.assertTrue(self.dialog.rect().contains(self.dialog.next_button.mapTo(self.dialog, self.dialog.next_button.rect().bottomRight())))

    def test_qt_launcher_seeds_defaults_only_for_default_service(self):
        from app.ui.qt.application import run_qt
        with patch("app.config_loader.ensure_external_config_exists") as seed, \
             patch("app.ui.qt.application.create_application") as application, \
             patch("app.ui.qt.application.QtServiceBridge") as bridge, \
             patch("app.ui.qt.application.MainWindow"):
            application.return_value.exec.return_value = 0
            self.assertEqual(run_qt(), 0)
            seed.assert_called_once_with()
            bridge.return_value.bootstrap.assert_called_once_with()
            seed.reset_mock()
            run_qt(service=object())
            seed.assert_not_called()

    def test_window_opens_setup_once_when_required_and_index_link_navigates(self):
        from app.application_service import ProductSnapshot, StartupStatus
        from app.ui.qt.main_window import MainWindow
        from app.ui.qt.service_bridge import ServiceSnapshot
        from app.application_service import MonitorState
        class WindowBridge(SetupBridge):
            product_snapshot_changed = Signal(object)
            command_failed = Signal(str, str)
            closed = Signal()
            def __init__(self, config):
                super().__init__(config)
                self.product_snapshot = ProductSnapshot.loading()
            def request_start(self):
                pass
            def request_stop(self):
                pass
            def request_product_refresh(self, limit=100):
                pass
            def shutdown(self):
                self.closed.emit()
        bridge = WindowBridge(self.config)
        window = MainWindow(bridge)
        window.show()
        bridge.snapshot = ServiceSnapshot(StartupStatus.SETUP_REQUIRED, MonitorState.STOPPED)
        bridge.state_changed.emit(bridge.snapshot)
        self.app.processEvents()
        self.assertIsNotNone(window._setup_dialog)
        original = window._setup_dialog
        bridge.state_changed.emit(bridge.snapshot)
        self.app.processEvents()
        self.assertIs(window._setup_dialog, original)
        original._next()
        original._next()
        original._go_to_index()
        self.app.processEvents()
        self.assertIs(window.page_stack.currentWidget(), window.my_files_page)
        self.assertEqual(window.my_files_page.workspace_tabs.currentIndex(), 1)
        self.assertIsNone(window._setup_dialog)
        bridge.state_changed.emit(bridge.snapshot)
        self.app.processEvents()
        self.assertIsNone(window._setup_dialog)
        window.close()
        window.deleteLater()

    def test_screen_fit_keeps_step_actions_visible_on_short_scaled_displays(self):
        from PySide6.QtCore import QRect
        self.dialog.show()
        self.app.processEvents()
        for available in (QRect(0, 0, 1024, 640), QRect(100, 40, 800, 480)):
            self.dialog.fit_to_available_screen(available)
            self.app.processEvents()
            for step in range(3):
                self.dialog.stack.setCurrentIndex(step)
                self.dialog._refresh()
                self.app.processEvents()
                self.assertLessEqual(self.dialog.height(), available.height() - 72)
                self.assertLessEqual(self.dialog.width(), available.width() - 48)
                for button in (self.dialog.next_button, self.dialog.later_button):
                    bottom = button.mapTo(self.dialog, button.rect().bottomRight())
                    self.assertTrue(self.dialog.rect().contains(bottom))
                    self.assertTrue(button.isVisible())
                self.dialog.content_scroll.verticalScrollBar().setValue(
                    self.dialog.content_scroll.verticalScrollBar().maximum())
                self.app.processEvents()
                self.assertTrue(self.dialog.next_button.isVisible())
        self.assertTrue(self.dialog.isSizeGripEnabled())
