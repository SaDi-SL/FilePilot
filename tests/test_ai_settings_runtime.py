import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.ai_classifier import get_ai_classifier, reset_ai_classifier
from app.gui_actions import ActionsMixin


class Var:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class MonitorDouble:
    def __init__(self, is_running=False):
        self.is_running = is_running
        self.stop_all_calls = 0
        self.start_all_calls = 0
        self.callback = None

    def stop_all(self):
        self.stop_all_calls += 1

    def start_all(self):
        self.start_all_calls += 1

    def set_file_processed_callback(self, callback):
        self.callback = callback


class AISettingsRuntimeTests(unittest.TestCase):
    def tearDown(self):
        reset_ai_classifier()

    def test_default_config_has_ai_disabled(self):
        config_path = Path(__file__).resolve().parents[1] / "config" / "config.json"

        with open(config_path, "r", encoding="utf-8") as file:
            ai_config = json.load(file)["ai"]

        self.assertEqual(
            ai_config,
            {
                "enabled": False,
                "provider": "ollama",
                "claude_api_key": "",
                "ollama_model": "mistral",
            },
        )

    def test_normal_settings_save_persists_ai_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text('{"preserved": true}', encoding="utf-8")
            gui = self._make_save_gui()

            with (
                patch("app.gui_actions.get_config_path", return_value=config_path),
                patch("app.gui_actions.get_language", return_value="en"),
            ):
                ActionsMixin.save_settings(gui)

            with open(config_path, "r", encoding="utf-8") as file:
                saved = json.load(file)

        self.assertTrue(saved["preserved"])
        self.assertEqual(
            saved["ai"],
            {
                "enabled": True,
                "provider": "claude",
                "claude_api_key": "sk-ant-test",
                "ollama_model": "llama3.2",
            },
        )
        gui.reload_settings.assert_called_once_with()

    def test_reload_restores_ai_values_and_restarts_active_monitoring(self):
        old_monitor = MonitorDouble(is_running=True)
        new_monitor = MonitorDouble()
        config = self._runtime_config(
            {
                "enabled": True,
                "provider": "claude",
                "claude_api_key": "sk-ant-reloaded",
                "ollama_model": "llama3.1",
            }
        )
        gui = self._make_reload_gui(old_monitor)

        with (
            patch("app.gui_actions.build_monitor", return_value=(config, new_monitor)),
            patch("app.ai_classifier.reset_ai_classifier") as reset_classifier,
            patch("app.startup_manager.is_startup_enabled", return_value=False),
        ):
            ActionsMixin.reload_settings(gui)

        self.assertEqual(old_monitor.stop_all_calls, 1)
        self.assertEqual(new_monitor.start_all_calls, 1)
        self.assertIs(new_monitor.callback, gui.live_callback)
        self.assertTrue(gui.ai_enabled_var.get())
        self.assertEqual(gui.ai_provider_var.get(), "claude")
        self.assertEqual(gui.claude_api_key_var.get(), "sk-ant-reloaded")
        self.assertEqual(gui.ollama_model_var.get(), "llama3.1")
        gui.check_ai_status.assert_called_once_with()
        reset_classifier.assert_called_once_with()

    def test_reload_keeps_inactive_monitoring_inactive_and_missing_ai_safe(self):
        old_monitor = MonitorDouble(is_running=False)
        new_monitor = MonitorDouble()
        gui = self._make_reload_gui(old_monitor)

        with (
            patch(
                "app.gui_actions.build_monitor",
                return_value=(self._runtime_config(), new_monitor),
            ),
            patch("app.ai_classifier.reset_ai_classifier"),
            patch("app.startup_manager.is_startup_enabled", return_value=False),
        ):
            ActionsMixin.reload_settings(gui)

        self.assertEqual(old_monitor.stop_all_calls, 0)
        self.assertEqual(new_monitor.start_all_calls, 0)
        self.assertFalse(gui.ai_enabled_var.get())
        self.assertEqual(gui.ai_provider_var.get(), "ollama")
        self.assertEqual(gui.claude_api_key_var.get(), "")
        self.assertEqual(gui.ollama_model_var.get(), "mistral")

    def test_classifier_reset_rebuilds_from_new_ai_configuration(self):
        first = get_ai_classifier(
            {
                "ai": {
                    "provider": "claude",
                    "claude_api_key": "sk-ant-first",
                    "ollama_model": "first-model",
                }
            }
        )

        reset_ai_classifier()
        second = get_ai_classifier(
            {
                "ai": {
                    "provider": "ollama",
                    "claude_api_key": "",
                    "ollama_model": "second-model",
                }
            }
        )

        self.assertIsNot(first, second)
        self.assertEqual(second.provider_name, "ollama")
        self.assertEqual(second._ollama.model, "second-model")
        self.assertIsNone(second._claude)

    def test_selected_provider_availability_is_distinct_from_fallback(self):
        classifier = get_ai_classifier(
            {"ai": {"provider": "claude", "claude_api_key": ""}}
        )
        classifier._ollama.is_available = MagicMock(return_value=True)

        self.assertFalse(classifier.is_provider_available())
        self.assertTrue(classifier.is_available())

    def _make_save_gui(self):
        gui = object.__new__(ActionsMixin)
        gui.source_folder_var = Var("incoming")
        gui.organized_base_var = Var("organized")
        gui.processing_wait_var = Var("5")
        gui.duplicate_window_var = Var("3")
        gui.archive_by_date_var = Var(True)
        gui.run_at_startup_var = Var(False)
        gui.ai_enabled_var = Var(True)
        gui.ai_provider_var = Var("claude")
        gui.claude_api_key_var = Var("sk-ant-test")
        gui.ollama_model_var = Var("llama3.2")
        gui.status_bar_var = Var()
        gui.toast_manager = MagicMock()
        gui.add_notification = MagicMock()
        gui.reload_settings = MagicMock()
        return gui

    def _make_reload_gui(self, monitor):
        gui = object.__new__(ActionsMixin)
        gui.monitor = monitor
        gui.source_folder_var = Var()
        gui.organized_base_var = Var()
        gui.processing_wait_var = Var()
        gui.duplicate_window_var = Var()
        gui.archive_by_date_var = Var()
        gui.run_at_startup_var = Var()
        gui.ai_enabled_var = Var()
        gui.ai_provider_var = Var()
        gui.claude_api_key_var = Var()
        gui.ollama_model_var = Var()
        gui.status_var = Var()
        gui.status_bar_var = Var()
        gui.colors = {
            "success_bg": "green",
            "success": "green",
            "success_border": "green",
            "danger": "red",
            "danger_fg": "white",
            "danger_border": "red",
        }
        gui.header_status = MagicMock()
        gui._status_badge = MagicMock()
        gui._status_dot = MagicMock()
        gui.start_button = MagicMock()
        gui.stop_button = MagicMock()
        gui.toast_manager = MagicMock()
        gui.add_notification = MagicMock()
        gui._stop_dot_pulse = MagicMock()
        gui._stop_auto_refresh = MagicMock()
        gui._start_dot_pulse = MagicMock()
        gui._start_auto_refresh = MagicMock()
        gui.refresh_stats = MagicMock()
        gui.refresh_history = MagicMock()
        gui.render_rule_entries = MagicMock()
        gui.render_smart_rule_entries = MagicMock()
        gui.update_rules_count = MagicMock()
        gui.refresh_plugins_view = MagicMock()
        gui.check_ai_status = MagicMock()
        gui.live_callback = object()
        gui._make_live_callback = MagicMock(return_value=gui.live_callback)
        return gui

    @staticmethod
    def _runtime_config(ai=None):
        config = {
            "source_folder": "incoming",
            "organized_base_folder": "organized",
            "processing_wait_seconds": 5,
            "duplicate_event_window_seconds": 3,
            "archive_by_date": False,
        }
        if ai is not None:
            config["ai"] = ai
        return config


if __name__ == "__main__":
    unittest.main()
