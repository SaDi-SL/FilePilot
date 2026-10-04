import os
import unittest
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel, QLineEdit
except ImportError as error:
    raise unittest.SkipTest(
        "PySide6 is required for Qt tests; install requirements-qt.txt"
    ) from error

from app.application_service import MonitorState, StartupStatus
from app.product_configuration import ConfigurationDataState, ConfigurationSaveStatus
from app.product_identity import PRODUCT_IDENTITY
from app.product_settings import (
    AIConfigurationStatus,
    AISettings,
    GeneralSettings,
    PrivacySettings,
    ProductSettingsSnapshot,
    SettingsSaveResult,
    SettingsValidationIssue,
    SettingsValidationResult,
    StartupSettings,
)
from app.ui.qt.pages.settings import SettingsPage
from app.ui.qt.service_bridge import ServiceSnapshot


class SettingsBridgeStub(QObject):
    state_changed = Signal(object)
    settings_snapshot_changed = Signal(object)
    settings_validation_changed = Signal(str, object)
    settings_save_started = Signal(str)
    settings_save_completed = Signal(str, object)
    settings_request_failed = Signal(str, str, str)

    def __init__(self, snapshot):
        super().__init__()
        self.snapshot = ServiceSnapshot(StartupStatus.READY, MonitorState.STOPPED)
        self.settings_snapshot = snapshot
        self.product_identity = PRODUCT_IDENTITY
        self.validation_requests = []
        self.save_requests = []
        self.refresh_calls = 0

    def request_settings_validation(self, context, candidate):
        self.validation_requests.append((context, candidate))

    def request_settings_save(self, context, candidate, revision):
        self.save_requests.append((context, candidate, revision))

    def request_settings_refresh(self):
        self.refresh_calls += 1


def available_snapshot(revision="a" * 64):
    return ProductSettingsSnapshot(
        state=ConfigurationDataState.AVAILABLE,
        revision=revision,
        general=GeneralSettings(5, 3),
        startup=StartupSettings(
            "A normal launch does not start monitoring. --startup starts monitoring."
        ),
        ai=AISettings(
            False,
            "ollama",
            "mistral",
            False,
            AIConfigurationStatus.DISABLED,
            "Automatic AI classification is disabled.",
        ),
        privacy=PrivacySettings(
            "FilePilot operates on configured local folders.",
            "Claude is a cloud provider. No provider is contacted to open Settings.",
        ),
        changes_allowed=True,
    )


class QtSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            widget.close()
        self.app.processEvents()

    def test_page_loads_authoritative_values_and_identity(self):
        bridge = SettingsBridgeStub(available_snapshot())
        page = SettingsPage(bridge)

        self.assertEqual(page.processing_wait.value(), 5)
        self.assertEqual(page.duplicate_window.value(), 3)
        self.assertEqual(page.ai_provider.currentData(), "ollama")
        self.assertEqual(
            page.version_label.text(),
            f"Version {PRODUCT_IDENTITY.display_version}",
        )
        self.assertIn("Development build", page.build_label.text())

    def test_boolean_provider_and_model_edits_create_unsaved_candidate(self):
        bridge = SettingsBridgeStub(available_snapshot())
        page = SettingsPage(bridge)

        page.ai_enabled.setChecked(True)
        page.ai_provider.setCurrentIndex(page.ai_provider.findData("claude"))

        self.assertTrue(page._dirty)
        self.assertEqual(page.unsaved_label.text(), "Unsaved changes")
        self.assertTrue(page._candidate().automatic_ai_classification)
        self.assertEqual(page._candidate().ai_provider, "claude")
        self.assertFalse(page.ollama_model.isEnabled())

    def test_validation_controls_save_and_invalid_feedback(self):
        bridge = SettingsBridgeStub(available_snapshot())
        page = SettingsPage(bridge)
        page.processing_wait.setValue(7)
        context = page._validation_context()
        issue = SettingsValidationIssue(
            "ai.provider",
            "CLAUDE_CREDENTIAL_REQUIRED",
            "Claude cannot be enabled because no credential is configured.",
        )

        bridge.settings_validation_changed.emit(
            context,
            SettingsValidationResult(False, page._candidate(), (issue,)),
        )

        self.assertFalse(page.save_button.isEnabled())
        self.assertIn("no credential", page.feedback_label.text())
        bridge.settings_validation_changed.emit(
            context,
            SettingsValidationResult(True, page._candidate()),
        )
        self.assertTrue(page.save_button.isEnabled())

    def test_revert_requests_authoritative_refresh(self):
        bridge = SettingsBridgeStub(available_snapshot())
        page = SettingsPage(bridge)
        page.processing_wait.setValue(9)

        page._reload()
        bridge.settings_snapshot_changed.emit(available_snapshot())

        self.assertEqual(bridge.refresh_calls, 1)
        self.assertEqual(page.processing_wait.value(), 5)
        self.assertFalse(page._dirty)

    def test_save_uses_revision_and_success_refreshes_baseline(self):
        snapshot = available_snapshot()
        bridge = SettingsBridgeStub(snapshot)
        page = SettingsPage(bridge)
        page.processing_wait.setValue(8)
        bridge.settings_validation_changed.emit(
            page._validation_context(),
            SettingsValidationResult(True, page._candidate()),
        )

        page._save()

        self.assertEqual(len(bridge.save_requests), 1)
        self.assertEqual(bridge.save_requests[0][2], snapshot.revision)
        saved_snapshot = replace(
            snapshot,
            revision="b" * 64,
            general=GeneralSettings(8, 3),
        )
        bridge.settings_save_completed.emit(
            "settings",
            SettingsSaveResult(
                ConfigurationSaveStatus.SAVED,
                "Settings saved.",
                saved_snapshot,
            ),
        )
        self.assertFalse(page._dirty)
        self.assertEqual(page._baseline.revision, "b" * 64)

    def test_double_save_is_blocked_by_page_state(self):
        bridge = SettingsBridgeStub(available_snapshot())
        page = SettingsPage(bridge)
        page.processing_wait.setValue(8)
        bridge.settings_validation_changed.emit(
            page._validation_context(),
            SettingsValidationResult(True, page._candidate()),
        )
        page._save()
        bridge.settings_save_started.emit("settings")

        page._save()

        self.assertEqual(len(bridge.save_requests), 1)
        self.assertFalse(page.save_button.isEnabled())
        self.assertFalse(page.processing_wait.isEnabled())

    def test_unrelated_edit_preserves_exact_timing_values(self):
        snapshot = replace(
            available_snapshot(),
            general=GeneralSettings(1.23456789, 0.333333333),
        )
        page = SettingsPage(SettingsBridgeStub(snapshot))

        page.ai_enabled.setChecked(True)
        candidate = page._candidate()

        self.assertEqual(candidate.processing_wait_seconds, 1.23456789)
        self.assertEqual(candidate.duplicate_event_window_seconds, 0.333333333)

    def test_failed_refresh_disables_clean_stale_editor(self):
        bridge = SettingsBridgeStub(available_snapshot())
        page = SettingsPage(bridge)
        unavailable = ProductSettingsSnapshot(
            ConfigurationDataState.ERROR,
            error="Settings storage is unavailable.",
        )

        bridge.settings_snapshot_changed.emit(unavailable)

        self.assertFalse(page.processing_wait.isEnabled())
        self.assertFalse(page.save_button.isEnabled())
        self.assertIn("unavailable", page.feedback_label.text())

    def test_valid_dirty_stopped_shows_ready_and_enables_save(self):
        bridge = SettingsBridgeStub(available_snapshot())
        page = SettingsPage(bridge)
        page.processing_wait.setValue(8)

        bridge.settings_validation_changed.emit(
            page._validation_context(),
            SettingsValidationResult(True, page._candidate()),
        )

        self.assertEqual(
            page.feedback_label.text(),
            "Ready to save these Settings changes.",
        )
        self.assertTrue(page.save_button.isEnabled())

    def test_valid_dirty_running_shows_stop_guidance_and_disables_save(self):
        bridge = SettingsBridgeStub(available_snapshot())
        page = SettingsPage(bridge)
        page.processing_wait.setValue(8)
        bridge.state_changed.emit(
            ServiceSnapshot(StartupStatus.READY, MonitorState.RUNNING)
        )

        bridge.settings_validation_changed.emit(
            page._validation_context(),
            SettingsValidationResult(True, page._candidate()),
        )

        self.assertEqual(
            page.feedback_label.text(),
            "Stop monitoring to save these Settings changes.",
        )
        self.assertFalse(page.save_button.isEnabled())

    def test_invalid_dirty_running_keeps_validation_error_primary(self):
        bridge = SettingsBridgeStub(available_snapshot())
        page = SettingsPage(bridge)
        page.processing_wait.setValue(8)
        bridge.state_changed.emit(
            ServiceSnapshot(StartupStatus.READY, MonitorState.RUNNING)
        )
        issue = SettingsValidationIssue(
            "ai.provider",
            "INVALID_AI_PROVIDER",
            "Choose a supported AI provider.",
        )

        bridge.settings_validation_changed.emit(
            page._validation_context(),
            SettingsValidationResult(False, page._candidate(), (issue,)),
        )

        self.assertEqual(page.feedback_label.text(), issue.message)
        self.assertFalse(page.save_button.isEnabled())

    def test_valid_dirty_running_to_stopped_updates_guidance_and_save_state(self):
        bridge = SettingsBridgeStub(available_snapshot())
        page = SettingsPage(bridge)
        page.processing_wait.setValue(8)
        bridge.state_changed.emit(
            ServiceSnapshot(StartupStatus.READY, MonitorState.RUNNING)
        )
        bridge.settings_validation_changed.emit(
            page._validation_context(),
            SettingsValidationResult(True, page._candidate()),
        )
        self.assertFalse(page.save_button.isEnabled())

        bridge.state_changed.emit(
            ServiceSnapshot(StartupStatus.READY, MonitorState.STOPPED)
        )

        self.assertEqual(
            page.feedback_label.text(),
            "Ready to save these Settings changes.",
        )
        self.assertTrue(page.save_button.isEnabled())

    def test_stale_snapshot_and_save_keep_unsaved_values(self):
        bridge = SettingsBridgeStub(available_snapshot())
        page = SettingsPage(bridge)
        page.processing_wait.setValue(10)
        bridge.settings_snapshot_changed.emit(available_snapshot("c" * 64))

        bridge.settings_save_completed.emit(
            "settings",
            SettingsSaveResult(
                ConfigurationSaveStatus.STALE,
                "Configuration changed elsewhere. Refresh before saving Settings.",
                available_snapshot("c" * 64),
            ),
        )

        self.assertEqual(page.processing_wait.value(), 10)
        self.assertTrue(page._dirty)
        self.assertIn("changed elsewhere", page.feedback_label.text())

    def test_ai_status_is_configured_not_connected_and_secret_is_not_rendered(self):
        snapshot = replace(
            available_snapshot(),
            ai=AISettings(
                True,
                "claude",
                "mistral",
                True,
                AIConfigurationStatus.CLOUD_CONFIGURED,
                "Claude credentials are configured but not verified on this page.",
            ),
        )
        bridge = SettingsBridgeStub(snapshot)
        page = SettingsPage(bridge)

        self.assertIn("not verified", page.ai_status.text())
        self.assertNotIn("Connected", page.ai_status.text())
        self.assertNotIn("synthetic", page.credential_status.text())
        self.assertIsNone(page.findChild(QLineEdit, "ClaudeCredential"))

    def test_about_has_no_fake_update_state(self):
        page = SettingsPage(SettingsBridgeStub(available_snapshot()))
        visible_text = " ".join(
            label.text() for label in page.findChildren(QLabel)
        ).lower()
        self.assertNotIn("update available", visible_text)
        self.assertNotIn("latest version", visible_text)
        self.assertNotIn("connected", visible_text)

    def test_compact_workspace_has_no_horizontal_scroll_and_visible_focus(self):
        page = SettingsPage(SettingsBridgeStub(available_snapshot()))
        page.resize(650, 500)
        page.show()
        self.app.processEvents()

        self.assertEqual(
            page.scroll_area.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        page.processing_wait.setFocus()
        QTest.keyClick(page.processing_wait, Qt.Key.Key_Tab)
        self.app.processEvents()
        self.assertIs(self.app.focusWidget(), page.duplicate_window)
        self.assertNotEqual(page.save_button.focusPolicy(), Qt.FocusPolicy.NoFocus)

    def test_long_model_value_does_not_force_horizontal_scrolling(self):
        snapshot = replace(
            available_snapshot(),
            ai=replace(available_snapshot().ai, ollama_model="model-" * 30),
        )
        page = SettingsPage(SettingsBridgeStub(snapshot))
        page.resize(650, 500)
        page.show()
        self.app.processEvents()

        self.assertEqual(page.ollama_model.text(), "model-" * 30)
        self.assertEqual(
            page.scroll_area.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )


if __name__ == "__main__":
    unittest.main()
