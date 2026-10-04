import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.application_service import FilePilotService, MonitorState, StartupStatus
from app.product_configuration import ConfigurationSaveStatus, ProductRule
from app.product_settings import AIConfigurationStatus, ProductSettingsCandidate


class MonitorDouble:
    def __init__(self, folders):
        self.folders = tuple(str(Path(path).resolve()) for path in folders)
        self.running_folders = []
        self.is_running = False
        self.activity_callback = None
        self.start_calls = 0
        self.stop_calls = 0

    def set_activity_callback(self, callback):
        self.activity_callback = callback

    def start_all(self):
        self.start_calls += 1
        self.running_folders = list(self.folders)
        self.is_running = True

    def stop_all(self):
        self.stop_calls += 1
        self.running_folders = []
        self.is_running = False


class ProductSettingsServiceTests(unittest.TestCase):
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
                {"path": str(self.incoming), "label": "Incoming", "active": True}
            ],
            "organized_base_folder": str(self.organized),
            "destination_folders": {
                "documents": str(self.organized / "documents"),
                "others": str(self.organized / "others"),
            },
            "rules": {"documents": [".txt"]},
            "archive_by_date": False,
            "processing_wait_seconds": 5,
            "duplicate_event_window_seconds": 3,
            "run_at_startup": True,
            "ai": {
                "enabled": False,
                "provider": "ollama",
                "ollama_model": "mistral",
                "claude_api_key": "synthetic-test-credential",
            },
            "hash_db_file": str(self.root / "hashes.json"),
            "log_file": str(self.root / "filepilot.log"),
            "stats_file": str(self.root / "stats.json"),
            "history_file": str(self.root / "history.csv"),
            "custom": {"preserved": True},
        }
        self.config_path.write_text(json.dumps(self.document), encoding="utf-8")
        self.builder_calls = 0
        self.fail_builder_call = None
        self.monitors = []

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
            monitor = MonitorDouble(folders)
            self.monitors.append(monitor)
            return document, monitor

        self.service = FilePilotService(
            config_path=self.config_path,
            monitor_builder=builder,
        )
        self.assertEqual(self.service.bootstrap().status, StartupStatus.READY)

    def tearDown(self):
        self.temporary.cleanup()

    def _candidate(self, **changes):
        candidate = self.service.get_product_settings().candidate
        return replace(candidate, **changes)

    def test_snapshot_is_isolated_non_mutating_and_uses_authoritative_revision(self):
        before = self.config_path.read_bytes()
        configuration = self.service.get_product_configuration()

        snapshot = self.service.get_product_settings()

        self.assertEqual(snapshot.revision, configuration.revision)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(self.builder_calls, 1)
        self.assertTrue(snapshot.changes_allowed)

    def test_snapshot_and_repr_do_not_expose_secret(self):
        snapshot = self.service.get_product_settings()
        rendered = repr(snapshot)

        self.assertNotIn(self.document["ai"]["claude_api_key"], rendered)
        self.assertFalse(hasattr(snapshot.ai, "claude_api_key"))
        self.assertTrue(snapshot.ai.credential_configured)

    def test_ai_disabled_and_local_configured_statuses_are_truthful(self):
        disabled = self.service.get_product_settings()
        self.assertEqual(disabled.ai.status, AIConfigurationStatus.DISABLED)

        saved = self.service.save_product_settings(
            self._candidate(automatic_ai_classification=True),
            disabled.revision,
        )

        self.assertEqual(saved.status, ConfigurationSaveStatus.SAVED)
        self.assertEqual(saved.snapshot.ai.status, AIConfigurationStatus.LOCAL_CONFIGURED)
        self.assertIn("Availability is not checked", saved.snapshot.ai.status_text)
        self.assertNotIn("Connected", saved.snapshot.ai.status_text)

    def test_settings_read_performs_no_ai_probe_or_inference(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("network used")), patch(
            "app.ai_service.AIService.chat",
            side_effect=AssertionError("inference used"),
        ):
            snapshot = self.service.get_product_settings()
        self.assertEqual(snapshot.ai.status, AIConfigurationStatus.DISABLED)

    def test_valid_and_invalid_candidates(self):
        valid = self.service.validate_product_settings(
            self._candidate(processing_wait_seconds=1.5)
        )
        invalid_provider = self.service.validate_product_settings(
            self._candidate(ai_provider="not-a-provider")
        )
        invalid_model = self.service.validate_product_settings(
            self._candidate(ai_provider="ollama", ollama_model="")
        )

        self.assertTrue(valid.valid)
        self.assertFalse(invalid_provider.valid)
        self.assertEqual(invalid_provider.issues[0].code, "INVALID_AI_PROVIDER")
        self.assertFalse(invalid_model.valid)
        self.assertIn("OLLAMA_MODEL_REQUIRED", {i.code for i in invalid_model.issues})

    def test_claude_enablement_requires_existing_credential(self):
        document = json.loads(self.config_path.read_text(encoding="utf-8"))
        document["ai"]["claude_api_key"] = ""
        self.config_path.write_text(json.dumps(document), encoding="utf-8")

        result = self.service.validate_product_settings(
            self._candidate(
                automatic_ai_classification=True,
                ai_provider="claude",
            )
        )

        self.assertFalse(result.valid)
        self.assertIn("CLAUDE_CREDENTIAL_REQUIRED", {i.code for i in result.issues})

    def test_successful_save_preserves_rules_folders_startup_and_secret(self):
        snapshot = self.service.get_product_settings()
        result = self.service.save_product_settings(
            self._candidate(
                processing_wait_seconds=8,
                duplicate_event_window_seconds=4,
                ollama_model="local-model",
            ),
            snapshot.revision,
        )

        self.assertEqual(result.status, ConfigurationSaveStatus.SAVED)
        self.assertNotEqual(result.snapshot.revision, snapshot.revision)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["rules"], self.document["rules"])
        self.assertEqual(saved["watch_folders"], self.document["watch_folders"])
        self.assertEqual(saved["run_at_startup"], self.document["run_at_startup"])
        self.assertEqual(
            saved["ai"]["claude_api_key"],
            self.document["ai"]["claude_api_key"],
        )

    def test_running_save_is_refused_without_stopping_or_writing(self):
        snapshot = self.service.get_product_settings()
        before = self.config_path.read_bytes()
        self.assertEqual(self.service.start(), MonitorState.RUNNING)
        monitor = self.service.monitor

        result = self.service.save_product_settings(
            self._candidate(processing_wait_seconds=9),
            snapshot.revision,
        )

        self.assertEqual(result.status, ConfigurationSaveStatus.NOT_ALLOWED)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(monitor.stop_calls, 0)
        self.assertEqual(monitor.start_calls, 1)
        self.assertTrue(monitor.is_running)

    def test_successful_save_does_not_start_or_stop_monitoring(self):
        snapshot = self.service.get_product_settings()
        old_monitor = self.service.monitor

        result = self.service.save_product_settings(
            self._candidate(processing_wait_seconds=9),
            snapshot.revision,
        )

        self.assertEqual(result.status, ConfigurationSaveStatus.SAVED)
        self.assertEqual(old_monitor.start_calls, 0)
        self.assertEqual(old_monitor.stop_calls, 0)
        self.assertEqual(self.service.monitor.start_calls, 0)
        self.assertEqual(self.service.monitor_state, MonitorState.STOPPED)

    def test_stale_settings_after_folder_save_is_rejected(self):
        settings_a = self.service.get_product_settings()
        configuration_a = self.service.get_product_configuration()
        folder_b = replace(configuration_a.candidate, archive_by_date=True)
        saved_b = self.service.save_product_configuration(
            folder_b,
            configuration_a.revision,
        )

        stale = self.service.save_product_settings(
            replace(settings_a.candidate, processing_wait_seconds=10),
            settings_a.revision,
        )

        self.assertEqual(saved_b.status, ConfigurationSaveStatus.SAVED)
        self.assertEqual(stale.status, ConfigurationSaveStatus.STALE)
        document = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertTrue(document["archive_by_date"])
        self.assertEqual(document["processing_wait_seconds"], 5)

    def test_rules_save_makes_stale_settings_and_settings_makes_folders_stale(self):
        settings_a = self.service.get_product_settings()
        configuration_a = self.service.get_product_configuration()
        rule_save = self.service.save_product_configuration(
            replace(
                configuration_a.candidate,
                rules=(ProductRule("images", (".png",)),),
            ),
            configuration_a.revision,
        )
        stale_settings = self.service.save_product_settings(
            replace(settings_a.candidate, processing_wait_seconds=11),
            settings_a.revision,
        )

        settings_b = self.service.get_product_settings()
        folders_b = self.service.get_product_configuration()
        settings_c = self.service.save_product_settings(
            replace(settings_b.candidate, duplicate_event_window_seconds=6),
            settings_b.revision,
        )
        stale_folders = self.service.save_product_configuration(
            replace(folders_b.candidate, archive_by_date=True),
            folders_b.revision,
        )

        self.assertEqual(rule_save.status, ConfigurationSaveStatus.SAVED)
        self.assertEqual(stale_settings.status, ConfigurationSaveStatus.STALE)
        self.assertEqual(settings_c.status, ConfigurationSaveStatus.SAVED)
        self.assertEqual(stale_folders.status, ConfigurationSaveStatus.STALE)
        document = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(document["rules"], {"images": [".png"]})
        self.assertFalse(document["archive_by_date"])

    def test_write_failure_preserves_previous_document(self):
        snapshot = self.service.get_product_settings()
        before = self.config_path.read_bytes()

        with patch.object(
            self.service._configuration_store,
            "write_document_atomic",
            side_effect=OSError("disk unavailable"),
        ):
            result = self.service.save_product_settings(
                self._candidate(processing_wait_seconds=12),
                snapshot.revision,
            )

        self.assertEqual(result.status, ConfigurationSaveStatus.WRITE_FAILED)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(self.builder_calls, 1)

    def test_runtime_reload_failure_rolls_back_document_and_runtime(self):
        snapshot = self.service.get_product_settings()
        before = self.config_path.read_bytes()
        previous_monitor = self.service.monitor
        self.fail_builder_call = 2

        with self.assertLogs(level="ERROR"):
            result = self.service.save_product_settings(
                self._candidate(processing_wait_seconds=13),
                snapshot.revision,
            )

        self.assertEqual(result.status, ConfigurationSaveStatus.RELOAD_FAILED)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertIs(self.service.monitor, previous_monitor)
        self.assertEqual(self.service.monitor_state, MonitorState.STOPPED)

    def test_runtime_reload_and_rollback_failure_is_explicit(self):
        snapshot = self.service.get_product_settings()
        self.fail_builder_call = 2
        original_write = self.service._configuration_store.write_bytes_atomic
        write_calls = 0

        def fail_rollback(data, *, expected_revision=None):
            nonlocal write_calls
            write_calls += 1
            if write_calls == 2:
                raise OSError("rollback storage unavailable")
            return original_write(data, expected_revision=expected_revision)

        with patch.object(
            self.service._configuration_store,
            "write_bytes_atomic",
            side_effect=fail_rollback,
        ), self.assertLogs(level="ERROR"):
            result = self.service.save_product_settings(
                self._candidate(processing_wait_seconds=14),
                snapshot.revision,
            )

        self.assertEqual(result.status, ConfigurationSaveStatus.ROLLBACK_FAILED)
        self.assertEqual(self.service.monitor_state, MonitorState.ERROR)

    def test_service_never_targets_repository_config(self):
        repository_config = Path(__file__).resolve().parents[1] / "config" / "config.json"
        before = repository_config.read_bytes()

        snapshot = self.service.get_product_settings()
        self.service.validate_product_settings(snapshot.candidate)

        self.assertEqual(self.service._config_path, self.config_path)
        self.assertEqual(repository_config.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
