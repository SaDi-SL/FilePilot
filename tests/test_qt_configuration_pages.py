import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ImportError as error:
    raise unittest.SkipTest(
        "PySide6 is required for Qt tests; install requirements-qt.txt"
    ) from error

from app.application_service import MonitorState, StartupStatus
from app.product_configuration import (
    CandidateClassificationPreview,
    ConfigurationDataState,
    ConfigurationSaveResult,
    ConfigurationSaveStatus,
    ConfigurationValidationResult,
    ConfigurationValidationIssue,
    ProductConfigurationCandidate,
    ProductConfigurationSnapshot,
    ProductFolderSettings,
    ProductRule,
    ProductWatchFolder,
)
from app.ui.qt.pages.folders import FoldersPage
from app.ui.qt.pages.rules import RulesPage
from app.ui.qt.service_bridge import ServiceSnapshot


class ConfigurationBridgeStub(QObject):
    state_changed = Signal(object)
    configuration_snapshot_changed = Signal(object)
    configuration_validation_changed = Signal(str, object)
    candidate_classification_changed = Signal(str, object)
    configuration_save_started = Signal(str)
    configuration_save_completed = Signal(str, object)
    configuration_request_failed = Signal(str, str, str)

    def __init__(self, configuration_snapshot):
        super().__init__()
        self.snapshot = ServiceSnapshot(StartupStatus.READY, MonitorState.STOPPED)
        self.configuration_snapshot = configuration_snapshot
        self.validation_requests = []
        self.preview_requests = []
        self.save_requests = []
        self.refresh_calls = 0

    def request_configuration_validation(self, context, candidate):
        self.validation_requests.append((context, candidate))

    def request_candidate_classification(self, context, rules, filename):
        self.preview_requests.append((context, rules, filename))

    def request_configuration_save(self, context, candidate, revision):
        self.save_requests.append((context, candidate, revision))

    def request_configuration_refresh(self):
        self.refresh_calls += 1


class QtConfigurationPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name).resolve()
        self.incoming = root / "incoming"
        self.second = root / "second"
        self.organized = root / "organized"
        self.incoming.mkdir()
        self.second.mkdir()
        self.organized.mkdir()
        folders = ProductFolderSettings(
            watch_folders=(
                ProductWatchFolder(self.incoming, "Incoming", True, True),
                ProductWatchFolder(self.second, "Second", False, True),
            ),
            organized_folder=self.organized,
            organized_exists=True,
            archive_by_date=False,
            topology_valid=True,
            changes_allowed=True,
        )
        self.snapshot = ProductConfigurationSnapshot(
            ConfigurationDataState.AVAILABLE,
            "a" * 64,
            folders,
            (
                ProductRule("documents", (".txt", ".pdf")),
                ProductRule("images", (".png",)),
            ),
        )

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            widget.close()
        self.app.processEvents()
        self.temporary.cleanup()

    def test_rules_page_loads_authoritative_snapshot(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = RulesPage(bridge)

        self.assertEqual(page.rule_tree.topLevelItemCount(), 2)
        self.assertEqual(page.rule_tree.topLevelItem(0).text(0), "documents")
        self.assertIn(".pdf", page.rule_tree.topLevelItem(0).text(1))
        self.assertFalse(page.save_button.isEnabled())

    def test_rules_build_complete_unsaved_candidate_for_service_validation(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = RulesPage(bridge)
        page.rule_tree.topLevelItem(0).setText(1, " TXT, *.docx ")

        page._request_validation()

        context, candidate = bridge.validation_requests[-1]
        self.assertTrue(context.startswith("rules:"))
        self.assertEqual(candidate.rules[0].extensions, ("TXT", "*.docx"))
        self.assertEqual(candidate.organized_folder, self.organized)
        self.assertEqual(candidate.watch_folders, self.snapshot.folders.watch_folders)

    def test_rules_preview_uses_unsaved_rows_without_saving(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = RulesPage(bridge)
        page.rule_tree.topLevelItem(1).setText(1, "JPG")
        page.preview_input.setText("portrait.JPG")

        page._request_preview()

        context, rules, filename = bridge.preview_requests[-1]
        self.assertEqual((context, filename), ("rules", "portrait.JPG"))
        self.assertEqual(rules[1].extensions, ("JPG",))
        self.assertEqual(bridge.save_requests, [])

        result = CandidateClassificationPreview(
            True,
            filename,
            ".jpg",
            "images",
            False,
            message=".jpg is assigned to images.",
        )
        bridge.candidate_classification_changed.emit("rules", result)
        self.assertIn("images", page.preview_result.text())

    def test_rules_add_and_remove_extension_uses_selected_detail_editor(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = RulesPage(bridge)
        page.rule_tree.setCurrentItem(page.rule_tree.topLevelItem(0))
        page.extension_input.setText("DOCX")

        page._add_extension()

        self.assertIn("DOCX", page._rules()[0].extensions)
        self.assertEqual(page.unsaved_label.text(), "Unsaved changes")
        page.extension_list.setCurrentRow(page.extension_list.count() - 1)
        page._remove_extension()
        self.assertNotIn("DOCX", page._rules()[0].extensions)

    def test_rule_conflict_feedback_disables_save_and_names_both_categories(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = RulesPage(bridge)
        page.rule_tree.topLevelItem(0).setText(1, ".txt")
        context = page._validation_context()
        issue = ConfigurationValidationIssue(
            "rules.1.extensions.0",
            "EXTENSION_CATEGORY_CONFLICT",
            "Extension .txt is assigned to both documents and images.",
            ("rules.0.extensions.0", "rules.1.extensions.0"),
        )

        bridge.configuration_validation_changed.emit(
            context,
            ConfigurationValidationResult(False, page._candidate(), (issue,)),
        )

        self.assertFalse(page.save_button.isEnabled())
        self.assertIn("documents", page.feedback_label.text())
        self.assertIn("images", page.feedback_label.text())

    def test_rules_revert_discards_unsaved_editor_state(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = RulesPage(bridge)
        page.rule_tree.topLevelItem(0).setText(1, ".docx")

        page._reload()
        bridge.configuration_snapshot_changed.emit(self.snapshot)

        self.assertFalse(page._dirty)
        self.assertFalse(page.save_button.isEnabled())
        self.assertIn(".pdf", page.rule_tree.topLevelItem(0).text(1))
        self.assertEqual(page.unsaved_label.text(), "All changes saved")

    def test_rules_preserve_dirty_edits_when_new_revision_arrives(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = RulesPage(bridge)
        page.rule_tree.topLevelItem(0).setText(1, ".docx")
        newer = replace(self.snapshot, revision="b" * 64)

        bridge.configuration_snapshot_changed.emit(newer)

        self.assertEqual(page.rule_tree.topLevelItem(0).text(1), ".docx")
        self.assertIn("changed elsewhere", page.feedback_label.text())
        self.assertEqual(page._baseline.revision, self.snapshot.revision)

    def test_rules_preserve_dirty_edits_on_same_revision_refresh(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = RulesPage(bridge)
        page.rule_tree.topLevelItem(0).setText(1, ".docx")

        bridge.configuration_snapshot_changed.emit(self.snapshot)

        self.assertEqual(page.rule_tree.topLevelItem(0).text(1), ".docx")
        self.assertTrue(page._dirty)

    def test_validation_result_cannot_validate_a_newer_debounced_edit(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = RulesPage(bridge)
        page.rule_tree.topLevelItem(0).setText(1, ".docx")
        page._request_validation()
        old_context, old_candidate = bridge.validation_requests[-1]
        page.rule_tree.topLevelItem(0).setText(1, "bad extension")

        bridge.configuration_validation_changed.emit(
            old_context,
            ConfigurationValidationResult(True, old_candidate),
        )

        self.assertFalse(page._validation_valid)
        self.assertFalse(page.save_button.isEnabled())

    def test_running_lifecycle_disables_rule_save_even_after_valid_result(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = RulesPage(bridge)
        page.rule_tree.topLevelItem(0).setText(1, ".docx")
        valid = ConfigurationValidationResult(True, page._candidate())
        bridge.configuration_validation_changed.emit(page._validation_context(), valid)
        self.assertTrue(page.save_button.isEnabled())

        running = ServiceSnapshot(StartupStatus.READY, MonitorState.RUNNING)
        bridge.state_changed.emit(running)

        self.assertFalse(page.save_button.isEnabled())
        self.assertIn("Stop monitoring", page.feedback_label.text())

    def test_folders_page_shows_active_and_inactive_rows(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = FoldersPage(bridge)

        self.assertEqual(page.watch_tree.topLevelItemCount(), 2)
        self.assertEqual(page.watch_tree.topLevelItem(0).text(0), "Active | Ready")
        self.assertEqual(page.watch_tree.topLevelItem(1).text(0), "Inactive | Ready")
        self.assertEqual(page.destination_input.text(), str(self.organized))

    def test_folder_toggle_builds_complete_candidate_without_io(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = FoldersPage(bridge)
        second = page.watch_tree.topLevelItem(1)
        second.setCheckState(0, Qt.CheckState.Checked)
        page.archive_checkbox.setChecked(True)

        page._request_validation()

        context, candidate = bridge.validation_requests[-1]
        self.assertTrue(context.startswith("folders:"))
        self.assertTrue(candidate.watch_folders[1].active)
        self.assertTrue(candidate.archive_by_date)
        self.assertEqual(candidate.rules, self.snapshot.rules)

    def test_folder_browse_updates_candidate_only(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = FoldersPage(bridge)
        before_count = page.watch_tree.topLevelItemCount()

        with patch(
            "app.ui.qt.pages.folders.QFileDialog.getExistingDirectory",
            return_value=str(self.second),
        ):
            page._browse_watch_folder()

        self.assertEqual(page.watch_tree.topLevelItemCount(), before_count + 1)
        self.assertEqual(bridge.save_requests, [])
        self.assertEqual(page.unsaved_label.text(), "Unsaved changes")

    def test_folder_topology_error_disables_save(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = FoldersPage(bridge)
        page.destination_input.setText(str(self.incoming))
        issue = ConfigurationValidationIssue(
            "folders.topology",
            "UNSAFE_PATH_TOPOLOGY",
            "Watch root and organized root must be different.",
        )

        bridge.configuration_validation_changed.emit(
            page._validation_context(),
            ConfigurationValidationResult(False, page._candidate(), (issue,)),
        )

        self.assertFalse(page.save_button.isEnabled())
        self.assertIn("must be different", page.feedback_label.text())

    def test_folders_revert_restores_authoritative_paths(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = FoldersPage(bridge)
        page.destination_input.setText(str(self.second))

        page._reload()
        bridge.configuration_snapshot_changed.emit(self.snapshot)

        self.assertEqual(page.destination_input.text(), str(self.organized))
        self.assertFalse(page._dirty)
        self.assertFalse(page.save_button.isEnabled())
        self.assertEqual(page.unsaved_label.text(), "All changes saved")

    def test_generation_qualified_validation_failure_is_visible(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = RulesPage(bridge)
        page.rule_tree.topLevelItem(0).setText(1, ".docx")
        context = page._validation_context()

        bridge.configuration_request_failed.emit(
            "configuration_validation",
            context,
            "Validation could not be completed.",
        )

        self.assertFalse(page._validation_valid)
        self.assertFalse(page.save_button.isEnabled())
        self.assertIn("could not be completed", page.feedback_label.text())

    def test_long_folder_path_remains_available_in_editor(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = FoldersPage(bridge)
        long_path = str(self.organized / ("long-folder-name-" * 12))

        page.destination_input.setText(long_path)

        self.assertEqual(page.destination_input.text(), long_path)
        self.assertTrue(page.destination_input.hasSelectedText() is False)

    def test_successful_save_refreshes_page_baseline(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = RulesPage(bridge)
        page.rule_tree.topLevelItem(0).setText(1, ".docx")
        candidate = page._candidate()
        saved_snapshot = replace(
            self.snapshot,
            revision="c" * 64,
            rules=(ProductRule("documents", (".docx",)),),
        )
        result = ConfigurationSaveResult(
            ConfigurationSaveStatus.SAVED,
            "Configuration saved.",
            saved_snapshot,
        )

        bridge.configuration_save_completed.emit("rules", result)

        self.assertFalse(page._dirty)
        self.assertEqual(page._baseline.revision, "c" * 64)
        self.assertFalse(page.save_button.isEnabled())

    def test_stale_save_keeps_edits_and_requests_user_reconciliation(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = RulesPage(bridge)
        page.rule_tree.topLevelItem(0).setText(1, ".docx")
        stale = ConfigurationSaveResult(
            ConfigurationSaveStatus.STALE,
            "Configuration changed elsewhere. Refresh before saving.",
            replace(self.snapshot, revision="d" * 64),
        )

        bridge.configuration_save_completed.emit("rules", stale)

        self.assertTrue(page._dirty)
        self.assertEqual(page.rule_tree.topLevelItem(0).text(1), ".docx")
        self.assertIn("changed elsewhere", page.feedback_label.text())

    def test_running_lifecycle_disables_folder_save(self):
        bridge = ConfigurationBridgeStub(self.snapshot)
        page = FoldersPage(bridge)
        page.archive_checkbox.setChecked(True)
        bridge.configuration_validation_changed.emit(
            page._validation_context(),
            ConfigurationValidationResult(True, page._candidate()),
        )
        self.assertTrue(page.save_button.isEnabled())

        bridge.state_changed.emit(
            ServiceSnapshot(StartupStatus.READY, MonitorState.RUNNING)
        )

        self.assertFalse(page.save_button.isEnabled())
        self.assertIn("Stop monitoring", page.feedback_label.text())

    def test_configuration_pages_fit_compact_workspace_and_keep_focusable_actions(self):
        for page_type in (RulesPage, FoldersPage):
            with self.subTest(page=page_type.__name__):
                bridge = ConfigurationBridgeStub(self.snapshot)
                page = page_type(bridge)
                page.resize(650, 500)
                page.show()
                self.app.processEvents()

                self.assertEqual(
                    page.scroll_area.horizontalScrollBarPolicy(),
                    Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
                )
                page.save_button.setFocus()
                self.app.processEvents()
                self.assertNotEqual(
                    page.save_button.focusPolicy(),
                    Qt.FocusPolicy.NoFocus,
                )
                page.close()


if __name__ == "__main__":
    unittest.main()
