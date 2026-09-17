import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.watcher import NewFileHandler


class AIRuntimeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _config(self, ai=None):
        config = {
            "destination_folders": {
                "documents": str(self.root / "documents"),
                "others": str(self.root / "others"),
            },
            "rules": {"documents": [".pdf"]},
            "processing_wait_seconds": 0,
            "stats_file": str(self.root / "stats.json"),
            "history_file": str(self.root / "history.csv"),
            "hash_db_file": str(self.root / "hashes.json"),
        }
        if ai is not None:
            config["ai"] = ai
        return config

    def _ai(self, category):
        ai = MagicMock()
        ai.is_enabled = True
        ai.is_available.return_value = True
        ai.classify.return_value = SimpleNamespace(
            ok=True,
            category=category,
            reason="test reason",
            provider="test",
        )
        return ai

    def _process(self, handler, filename, smart_category=None, ai=None):
        source = self.root / filename
        source.write_text("test", encoding="utf-8")
        with (
            patch("app.watcher.smart_classify", return_value=smart_category),
            patch("app.watcher.move_file_with_retries") as move_file,
            patch("app.watcher.time.sleep"),
            patch("app.ai_classifier.get_ai_classifier", return_value=ai) as get_ai,
        ):
            handler._process_file_thread(str(source), "test")
        return move_file, get_ai

    def test_ai_is_disabled_when_ai_section_is_missing(self):
        handler = NewFileHandler(self._config(), {})

        move_file, get_ai = self._process(handler, "unknown.bin", ai=self._ai("documents"))

        get_ai.assert_not_called()
        self.assertEqual(move_file.call_args.kwargs["category_override"], "others")

    def test_ai_is_disabled_when_enabled_is_false(self):
        handler = NewFileHandler(self._config({"enabled": False}), {})

        move_file, get_ai = self._process(handler, "unknown.bin", ai=self._ai("documents"))

        get_ai.assert_not_called()
        self.assertEqual(move_file.call_args.kwargs["category_override"], "others")

    def test_extension_rule_takes_precedence_over_ai(self):
        handler = NewFileHandler(
            self._config({"enabled": True}),
            {".pdf": "documents"},
        )

        move_file, get_ai = self._process(handler, "report.pdf", ai=self._ai("documents"))

        get_ai.assert_not_called()
        self.assertEqual(move_file.call_args.kwargs["category_override"], "documents")
        self.assertEqual(move_file.call_args.kwargs["classification_method"], "extension")

    def test_ai_runs_when_enabled_and_no_earlier_classifier_matches(self):
        ai = self._ai("documents")
        handler = NewFileHandler(self._config({"enabled": True}), {})

        move_file, get_ai = self._process(handler, "unknown.bin", ai=ai)

        get_ai.assert_called_once_with(handler.config)
        ai.classify.assert_called_once_with("unknown.bin", ["documents"])
        self.assertEqual(move_file.call_args.kwargs["category_override"], "documents")
        self.assertEqual(move_file.call_args.kwargs["classification_method"], "ai")

    def test_ai_category_outside_allowed_categories_is_rejected(self):
        handler = NewFileHandler(self._config({"enabled": True}), {})

        move_file, _ = self._process(
            handler,
            "unknown.bin",
            ai=self._ai("untrusted-category"),
        )

        self.assertEqual(move_file.call_args.kwargs["category_override"], "others")
        self.assertNotIn("untrusted-category", handler.destination_folders)
        self.assertFalse((self.root / "untrusted-category").exists())

    def test_plugin_smart_and_ai_classification_do_not_mutate_extension_lookup(self):
        extension_lookup = {}

        plugin_manager = MagicMock()
        plugin_manager.classify_with_plugins.return_value = "plugin-category"
        plugin_handler = NewFileHandler(self._config(), extension_lookup, plugin_manager)
        self._process(plugin_handler, "plugin.bin")

        smart_handler = NewFileHandler(self._config(), extension_lookup)
        self._process(smart_handler, "smart.bin", smart_category="smart-category")

        ai_handler = NewFileHandler(self._config({"enabled": True}), extension_lookup)
        self._process(ai_handler, "ai.bin", ai=self._ai("documents"))

        self.assertEqual(extension_lookup, {})

    def test_same_extension_files_cannot_influence_each_other(self):
        extension_lookup = {}
        plugin_manager = MagicMock()
        plugin_manager.classify_with_plugins.side_effect = ["plugin-category", None]
        handler = NewFileHandler(self._config(), extension_lookup, plugin_manager)

        first_move, _ = self._process(handler, "first.bin")
        second_move, _ = self._process(handler, "second.bin")

        self.assertEqual(first_move.call_args.kwargs["category_override"], "plugin-category")
        self.assertEqual(second_move.call_args.kwargs["category_override"], "others")
        self.assertEqual(extension_lookup, {})


if __name__ == "__main__":
    unittest.main()
