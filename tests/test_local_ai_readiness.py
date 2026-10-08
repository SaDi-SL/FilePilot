import unittest
from unittest.mock import patch

from app.local_ai_readiness import check_local_ai, model_is_installed


class ReadinessTests(unittest.TestCase):
    def test_tags_are_exact_except_latest_alias(self):
        self.assertTrue(model_is_installed("bge-m3", ("bge-m3:latest",)))
        self.assertFalse(model_is_installed("gemma4:e4b-it-qat", ("gemma4:12b",)))
        self.assertFalse(model_is_installed("", ("x:latest",)))

    @patch("app.local_ai_readiness._request_json")
    def test_inventory_only_without_inference(self, transport):
        transport.return_value = {"models": [{"name": "gemma4:e4b-it-qat"}, {"model": "bge-m3:latest"}]}
        result = check_local_ai("gemma4:e4b-it-qat")
        self.assertTrue(result.installed)
        self.assertEqual(transport.call_count, 1)
        req = transport.call_args.args[0]
        self.assertEqual(req.full_url, "http://localhost:11434/api/tags")
        self.assertIsNone(req.data)
        self.assertEqual(transport.call_args.kwargs["timeout"], 3.0)

    @patch("app.local_ai_readiness._request_json")
    def test_missing_models_and_unreachable_are_distinct(self, transport):
        transport.return_value = {"models": []}
        result = check_local_ai("gemma4:e4b-it-qat")
        self.assertTrue(result.reachable)
        self.assertFalse(result.installed)
        transport.side_effect = TimeoutError()
        result = check_local_ai("gemma4:e4b-it-qat")
        self.assertFalse(result.reachable)
        self.assertIn("Open Ollama", result.error)

    @patch("app.local_ai_readiness._request_json", return_value={"models": [{}]})
    def test_malformed_inventory_is_not_success(self, transport):
        self.assertFalse(check_local_ai("gemma4:e4b-it-qat").reachable)
