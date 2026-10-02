import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.application_service import FilePilotService, MonitorState, StartupStatus
from app.product_configuration import (
    ConfigurationSaveStatus,
    ProductRule,
)


class MonitorDouble:
    def __init__(self, folders, *, bind_error=None):
        self.folders = tuple(str(Path(path).resolve()) for path in folders)
        self.running_folders = []
        self.is_running = False
        self.activity_callback = None
        self.start_calls = 0
        self.stop_calls = 0
        self.bind_error = bind_error

    def set_activity_callback(self, callback):
        if self.bind_error is not None:
            raise self.bind_error
        self.activity_callback = callback

    def start_all(self):
        self.start_calls += 1
        self.running_folders = list(self.folders)
        self.is_running = True

    def stop_all(self):
        self.stop_calls += 1
        self.running_folders = []
        self.is_running = False


class ConfigurationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.config_path = self.root / "config.json"
        self.incoming = self.root / "incoming"
        self.organized = self.root / "organized"
        self.incoming.mkdir()
        self.organized.mkdir()
        self.document = {
            "first_run_completed": True,
            "source_folder": str(self.incoming),
            "watch_folders": [
                {
                    "path": str(self.incoming),
                    "label": "Incoming",
                    "active": True,
                }
            ],
            "organized_base_folder": str(self.organized),
            "destination_folders": {
                "documents": str(self.organized / "documents"),
                "others": str(self.organized / "others"),
            },
            "rules": {"documents": [".txt"]},
            "archive_by_date": False,
            "hash_db_file": str(self.root / "hashes.json"),
            "log_file": str(self.root / "filepilot.log"),
            "stats_file": str(self.root / "stats.json"),
            "history_file": str(self.root / "history.csv"),
            "custom": {"preserved": True},
        }
        self.config_path.write_text(json.dumps(self.document), encoding="utf-8")
        self.builder_calls = 0
        self.monitors = []
        self.fail_builder_call = None
        self.fail_binding_call = None

        def builder():
            self.builder_calls += 1
            if self.builder_calls == self.fail_builder_call:
                raise RuntimeError("candidate runtime failed")
            document = json.loads(self.config_path.read_text(encoding="utf-8"))
            folders = [
                item["path"]
                for item in document.get("watch_folders", [])
                if item.get("active", True)
            ]
            bind_error = (
                RuntimeError("candidate callback binding failed")
                if self.builder_calls == self.fail_binding_call
                else None
            )
            monitor = MonitorDouble(folders, bind_error=bind_error)
            self.monitors.append(monitor)
            return document, monitor

        self.service = FilePilotService(
            config_path=self.config_path,
            monitor_builder=builder,
        )
        result = self.service.bootstrap()
        self.assertEqual(result.status, StartupStatus.READY)

    def tearDown(self):
        self.temporary.cleanup()

    def _edited_candidate(self):
        snapshot = self.service.get_product_configuration()
        return snapshot, replace(
            snapshot.candidate,
            rules=(ProductRule("portable", (" PDF ",)),),
            archive_by_date=True,
        )

    def test_configuration_snapshot_is_owned_by_service(self):
        snapshot = self.service.get_product_configuration()

        self.assertTrue(snapshot.folders.changes_allowed)
        self.assertEqual(snapshot.rules[0].category, "documents")
        self.assertIsNotNone(snapshot.revision)

    def test_validation_and_preview_do_not_mutate_disk(self):
        snapshot, candidate = self._edited_candidate()
        before = self.config_path.read_bytes()

        validation = self.service.validate_product_configuration(candidate)
        preview = self.service.preview_candidate_classification(
            candidate.rules,
            "guide.PDF",
        )

        self.assertTrue(validation.valid)
        self.assertEqual(preview.category, "portable")
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(snapshot.revision, self.service.get_product_configuration().revision)
        self.assertEqual(self.builder_calls, 1)
        self.assertEqual(self.service.monitor.start_calls, 0)
        self.assertEqual(self.service.monitor.stop_calls, 0)

    def test_successful_save_rebuilds_runtime_and_leaves_monitoring_stopped(self):
        snapshot, candidate = self._edited_candidate()
        old_monitor = self.service.monitor

        result = self.service.save_product_configuration(
            candidate,
            snapshot.revision,
        )

        self.assertEqual(result.status, ConfigurationSaveStatus.SAVED)
        self.assertIsNot(self.service.monitor, old_monitor)
        self.assertEqual(self.builder_calls, 2)
        self.assertEqual(self.service.monitor_state, MonitorState.STOPPED)
        self.assertFalse(self.service.monitor.is_running)
        self.assertEqual(self.service.monitor.start_calls, 0)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["rules"], {"portable": [".pdf"]})
        self.assertTrue(saved["archive_by_date"])
        self.assertEqual(saved["custom"], {"preserved": True})

    def test_running_monitor_refuses_save_without_writing(self):
        snapshot, candidate = self._edited_candidate()
        before = self.config_path.read_bytes()
        self.assertEqual(self.service.start(), MonitorState.RUNNING)

        result = self.service.save_product_configuration(
            candidate,
            snapshot.revision,
        )

        self.assertEqual(result.status, ConfigurationSaveStatus.NOT_ALLOWED)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(self.builder_calls, 1)
        self.assertTrue(self.service.monitor.is_running)
        self.assertEqual(self.service.monitor.stop_calls, 0)

    def test_stale_revision_refuses_save_without_writing(self):
        _, candidate = self._edited_candidate()
        before = self.config_path.read_bytes()

        result = self.service.save_product_configuration(candidate, "stale-revision")

        self.assertEqual(result.status, ConfigurationSaveStatus.STALE)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(self.builder_calls, 1)

    def test_invalid_candidate_refuses_save_without_writing(self):
        snapshot, candidate = self._edited_candidate()
        invalid = replace(
            candidate,
            rules=(
                ProductRule("documents", ("TXT",)),
                ProductRule("notes", ("*.txt",)),
            ),
        )
        before = self.config_path.read_bytes()

        result = self.service.save_product_configuration(
            invalid,
            snapshot.revision,
        )

        self.assertEqual(result.status, ConfigurationSaveStatus.INVALID)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(self.builder_calls, 1)

    def test_malformed_candidate_refuses_save_without_writing(self):
        snapshot, candidate = self._edited_candidate()
        malformed = replace(candidate, watch_folders=None)
        before = self.config_path.read_bytes()

        result = self.service.save_product_configuration(
            malformed,
            snapshot.revision,
        )

        self.assertEqual(result.status, ConfigurationSaveStatus.INVALID)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(self.builder_calls, 1)

    def test_atomic_write_failure_preserves_document_and_runtime(self):
        snapshot, candidate = self._edited_candidate()
        before = self.config_path.read_bytes()
        old_monitor = self.service.monitor

        with patch.object(
            self.service._configuration_store,
            "write_document_atomic",
            side_effect=OSError("disk unavailable"),
        ):
            result = self.service.save_product_configuration(
                candidate,
                snapshot.revision,
            )

        self.assertEqual(result.status, ConfigurationSaveStatus.WRITE_FAILED)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertIs(self.service.monitor, old_monitor)
        self.assertEqual(self.builder_calls, 1)

    def test_reload_failure_restores_previous_document_and_runtime(self):
        snapshot, candidate = self._edited_candidate()
        before = self.config_path.read_bytes()
        old_monitor = self.service.monitor
        old_config = self.service.config
        self.fail_builder_call = 2

        with self.assertLogs(level="ERROR"):
            result = self.service.save_product_configuration(
                candidate,
                snapshot.revision,
            )

        self.assertEqual(result.status, ConfigurationSaveStatus.RELOAD_FAILED)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertIs(self.service.monitor, old_monitor)
        self.assertIs(self.service.config, old_config)
        self.assertEqual(self.service.startup_status, StartupStatus.READY)
        self.assertEqual(self.service.monitor_state, MonitorState.STOPPED)

    def test_post_builder_binding_failure_also_rolls_back(self):
        snapshot, candidate = self._edited_candidate()
        before = self.config_path.read_bytes()
        old_monitor = self.service.monitor
        self.fail_binding_call = 2

        with self.assertLogs(level="ERROR"):
            result = self.service.save_product_configuration(
                candidate,
                snapshot.revision,
            )

        self.assertEqual(result.status, ConfigurationSaveStatus.RELOAD_FAILED)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertIs(self.service.monitor, old_monitor)
        self.assertEqual(self.service.startup_status, StartupStatus.READY)
        self.assertFalse(self.monitors[-1].is_running)

    def test_two_sequential_saves_require_and_return_the_new_revision(self):
        first_snapshot, first_candidate = self._edited_candidate()
        first = self.service.save_product_configuration(
            first_candidate,
            first_snapshot.revision,
        )
        second_candidate = replace(
            first.snapshot.candidate,
            rules=(ProductRule("images", ("PNG",)),),
        )

        stale = self.service.save_product_configuration(
            second_candidate,
            first_snapshot.revision,
        )
        second = self.service.save_product_configuration(
            second_candidate,
            first.snapshot.revision,
        )

        self.assertEqual(stale.status, ConfigurationSaveStatus.STALE)
        self.assertEqual(second.status, ConfigurationSaveStatus.SAVED)
        self.assertNotEqual(first.snapshot.revision, second.snapshot.revision)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["rules"], {"images": [".png"]})

    def test_rules_save_does_not_revert_a_newer_folder_save(self):
        original = self.service.get_product_configuration()
        second_incoming = self.root / "second-incoming"
        second_incoming.mkdir()
        folder_candidate = replace(
            original.candidate,
            watch_folders=(
                replace(
                    original.candidate.watch_folders[0],
                    path=second_incoming,
                ),
            ),
        )
        folder_save = self.service.save_product_configuration(
            folder_candidate,
            original.revision,
        )
        stale_rules = replace(
            original.candidate,
            rules=(ProductRule("images", (".png",)),),
        )

        stale = self.service.save_product_configuration(stale_rules, original.revision)

        self.assertEqual(folder_save.status, ConfigurationSaveStatus.SAVED)
        self.assertEqual(stale.status, ConfigurationSaveStatus.STALE)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["watch_folders"][0]["path"], str(second_incoming))
        self.assertEqual(saved["rules"], self.document["rules"])

    def test_folders_save_does_not_revert_a_newer_rules_save(self):
        original = self.service.get_product_configuration()
        rule_candidate = replace(
            original.candidate,
            rules=(ProductRule("images", (".png",)),),
        )
        rule_save = self.service.save_product_configuration(
            rule_candidate,
            original.revision,
        )
        stale_folders = replace(original.candidate, archive_by_date=True)

        stale = self.service.save_product_configuration(
            stale_folders,
            original.revision,
        )

        self.assertEqual(rule_save.status, ConfigurationSaveStatus.SAVED)
        self.assertEqual(stale.status, ConfigurationSaveStatus.STALE)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["rules"], {"images": [".png"]})
        self.assertFalse(saved["archive_by_date"])


if __name__ == "__main__":
    unittest.main()
