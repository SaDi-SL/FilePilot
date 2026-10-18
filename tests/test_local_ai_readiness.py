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


class PrepareLocalAITests(unittest.TestCase):
    def test_reachable_service_is_never_restarted(self):
        from app.local_ai_readiness import LocalAIReadiness, prepare_local_ai
        ready = LocalAIReadiness(True, ("model",), "model", True, "bge-m3", False)
        with patch("app.local_ai_readiness.check_local_ai", return_value=ready), patch("subprocess.Popen") as launch:
            self.assertEqual(prepare_local_ai("model"), ready)
            launch.assert_not_called()

    def test_windows_start_is_background_loopback_and_rechecks_inventory(self):
        from app.local_ai_readiness import LocalAIReadiness, prepare_local_ai
        offline = LocalAIReadiness(False, (), "model", False, "bge-m3", False)
        ready = LocalAIReadiness(True, ("model",), "model", True, "bge-m3", False)
        with patch("sys.platform", "win32"), patch("shutil.which", return_value="C:/Ollama/ollama.exe"), patch("subprocess.CREATE_NO_WINDOW", 0x08000000, create=True), patch("subprocess.Popen") as launch, patch("time.sleep"), patch("app.local_ai_readiness.check_local_ai", side_effect=[offline, ready]):
            result = prepare_local_ai("model")
        self.assertTrue(result.answer_installed)
        self.assertEqual(launch.call_args.args[0], ["C:/Ollama/ollama.exe", "serve"])
        self.assertEqual(launch.call_args.kwargs["env"]["OLLAMA_HOST"], "127.0.0.1:11434")
        self.assertFalse(launch.call_args.kwargs.get("shell", False))

    def test_missing_install_reports_action_without_launching(self):
        from app.local_ai_readiness import LocalAIReadiness, prepare_local_ai
        offline = LocalAIReadiness(False, (), "model", False, "bge-m3", False)
        with patch("sys.platform", "win32"), patch("shutil.which", return_value=None), patch.dict("os.environ", {"LOCALAPPDATA": ""}), patch("subprocess.Popen") as launch, patch("app.local_ai_readiness.check_local_ai", return_value=offline):
            result = prepare_local_ai("model")
        self.assertIn("Install Ollama", result.error)
        launch.assert_not_called()
