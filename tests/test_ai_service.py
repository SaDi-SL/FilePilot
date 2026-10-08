import json
import unittest
from urllib import error as url_error
from unittest.mock import MagicMock, patch

from app.ai_service import (
    AIProviderAuthenticationError,
    AIProviderRequestError,
    AIProviderResponseError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    AIResponse,
    AIService,
    CLAUDE_API_URL,
    CLAUDE_DEFAULT_MAX_TOKENS,
    ClaudeProvider,
    OllamaProvider,
    create_ai_service,
)


class FakeProvider:
    def __init__(self, name, is_cloud, ready=True, text="response", failure=None):
        self.name = name
        self.is_cloud = is_cloud
        self.ready = ready
        self.text = text
        self.failure = failure
        self.ready_calls = []
        self.chat_calls = []

    def is_ready(self, *, timeout=3.0):
        self.ready_calls.append(timeout)
        return self.ready

    def chat(self, prompt, *, timeout, max_output_tokens=None):
        self.chat_calls.append((prompt, timeout, max_output_tokens))
        if self.failure is not None:
            raise self.failure
        return self.text


def urlopen_response(payload):
    response = MagicMock()
    if isinstance(payload, bytes):
        response.read.return_value = payload
    else:
        response.read.return_value = json.dumps(payload).encode("utf-8")
    context = MagicMock()
    context.__enter__.return_value = response
    return context


class AIServiceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.ollama = FakeProvider("ollama", False)
        self.claude = FakeProvider("claude", True)

    def service(self, selected="ollama"):
        return AIService(selected, self.ollama, self.claude)

    def test_selected_ollama_is_used(self):
        service = self.service("ollama")

        response = service.chat("prompt")

        self.assertEqual(response.provider, "ollama")
        self.assertFalse(response.is_cloud)
        self.assertEqual(service.get_active_provider_name(), "ollama")
        self.assertTrue(service.is_selected_provider_ready())
        self.assertEqual(self.claude.chat_calls, [])

    def test_selected_claude_is_used(self):
        response = self.service("claude").chat("prompt")

        self.assertEqual(response.provider, "claude")
        self.assertTrue(response.is_cloud)
        self.assertEqual(self.claude.chat_calls, [("prompt", 30, None)])
        self.assertEqual(self.ollama.chat_calls, [])

    def test_unavailable_claude_falls_back_to_ollama(self):
        self.claude.ready = False

        response = self.service("claude").chat("prompt")

        self.assertEqual(response, AIResponse("response", "ollama", False))
        self.assertEqual(self.ollama.chat_calls, [("prompt", 30, None)])

    def test_strict_claude_does_not_fall_back(self):
        self.claude.ready = False

        with self.assertRaises(AIProviderUnavailableError):
            self.service("claude").chat("prompt", allow_fallback=False)

        self.assertEqual(self.ollama.ready_calls, [])
        self.assertEqual(self.ollama.chat_calls, [])

    def test_unavailable_ollama_never_falls_back_to_claude(self):
        self.ollama.ready = False

        with self.assertRaises(AIProviderUnavailableError):
            self.service("ollama").chat("private prompt")

        self.assertEqual(self.claude.ready_calls, [])
        self.assertEqual(self.claude.chat_calls, [])

    def test_no_available_provider_raises_unavailable(self):
        self.ollama.ready = False
        self.claude.ready = False

        with self.assertRaises(AIProviderUnavailableError):
            self.service("claude").chat("prompt")

    def test_selected_readiness_is_distinct_from_fallback_availability(self):
        self.claude.ready = False
        service = self.service("claude")

        self.assertFalse(service.is_selected_provider_ready(timeout=1.25))
        self.assertTrue(service.is_available(timeout=2.5))
        self.assertEqual(self.claude.ready_calls, [1.25, 2.5])
        self.assertEqual(self.ollama.ready_calls, [2.5])

    def test_request_controls_are_forwarded_per_call_without_service_state(self):
        service = self.service("ollama")

        first = service.chat("first", timeout=5, max_output_tokens=10)
        second = service.chat("second", timeout=90, max_output_tokens=800)

        self.assertEqual(first.provider, "ollama")
        self.assertEqual(second.provider, "ollama")
        self.assertEqual(
            self.ollama.chat_calls,
            [("first", 5, 10), ("second", 90, 800)],
        )
        self.assertNotIn("timeout", service.__dict__)
        self.assertNotIn("max_output_tokens", service.__dict__)

    def test_request_failure_does_not_trigger_fallback(self):
        failure = AIProviderRequestError("claude request failed")
        self.claude.failure = failure

        with self.assertRaises(AIProviderRequestError) as raised:
            self.service("claude").chat("prompt")

        self.assertIs(raised.exception, failure)
        self.assertEqual(self.ollama.ready_calls, [])
        self.assertEqual(self.ollama.chat_calls, [])

    def test_factory_preserves_missing_and_unknown_provider_compatibility(self):
        missing = create_ai_service({})
        unknown = create_ai_service({"provider": "unexpected"})

        self.assertEqual(missing.selected_provider, "ollama")
        self.assertEqual(unknown.selected_provider, "unexpected")
        with patch.object(OllamaProvider, "is_ready", return_value=True):
            self.assertEqual(missing.get_active_provider_name(), "ollama")
            self.assertEqual(unknown.get_active_provider_name(), "ollama")
            self.assertFalse(unknown.is_selected_provider_ready())

    def test_factory_ignores_automatic_enabled_flag_for_manual_operations(self):
        service = create_ai_service({"enabled": False, "ollama_model": "local"})

        with (
            patch.object(OllamaProvider, "is_ready", return_value=True),
            patch.object(OllamaProvider, "chat", return_value="manual response"),
        ):
            response = service.chat("manual prompt", timeout=12)

        self.assertEqual(response, AIResponse("manual response", "ollama", False))


class OllamaProviderTests(unittest.TestCase):
    def test_readiness_uses_tags_endpoint_and_supplied_timeout(self):
        provider = OllamaProvider(model="test-model", base_url="http://local/")

        with patch("app.ai_service.request.urlopen", return_value=urlopen_response({})) as opener:
            self.assertTrue(provider.is_ready(timeout=1.5))

        req = opener.call_args.args[0]
        self.assertEqual(req.full_url, "http://local/api/tags")
        self.assertEqual(opener.call_args.kwargs["timeout"], 1.5)

    def test_readiness_failure_returns_false(self):
        provider = OllamaProvider()

        with patch(
            "app.ai_service.request.urlopen",
            side_effect=url_error.URLError("offline"),
        ):
            self.assertFalse(provider.is_ready())

    def test_chat_parses_response_and_maps_request_controls(self):
        provider = OllamaProvider(model="llama-test")

        with patch(
            "app.ai_service.request.urlopen",
            return_value=urlopen_response({"response": "  generated text  "}),
        ) as opener:
            result = provider.chat(
                "private prompt",
                timeout=17,
                max_output_tokens=222,
            )

        req = opener.call_args.args[0]
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(result, "generated text")
        self.assertEqual(opener.call_args.kwargs["timeout"], 17)
        self.assertEqual(payload["model"], "llama-test")
        self.assertEqual(payload["prompt"], "private prompt")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["options"], {"num_predict": 222})

    def test_answer_chat_requests_final_content_without_thinking(self):
        provider = OllamaProvider(model="gemma4:e4b-it-qat", use_chat_api=True, think=False)
        with patch("app.ai_service.request.urlopen", return_value=urlopen_response({
            "message": {"content": "  Evidence [S1]  ", "thinking": "internal trace"},
        })) as opener:
            result = provider.chat("private prompt", timeout=45, max_output_tokens=500)
        req = opener.call_args.args[0]
        payload = json.loads(req.data)
        self.assertTrue(req.full_url.endswith("/api/chat"))
        self.assertFalse(payload["think"])
        self.assertNotIn("prompt", payload)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "private prompt"}])
        self.assertEqual(payload["options"]["num_predict"], 500)
        self.assertEqual(result, "Evidence [S1]")

    def test_thinking_only_chat_is_an_error_and_never_an_answer(self):
        provider = OllamaProvider(use_chat_api=True, think=False)
        with patch("app.ai_service.request.urlopen", return_value=urlopen_response({
            "message": {"content": "", "thinking": "private reasoning"},
        })):
            with self.assertRaisesRegex(AIProviderResponseError, "without a final answer") as raised:
                provider.chat("private prompt", timeout=45)
        self.assertNotIn("private reasoning", str(raised.exception))

    def test_malformed_chat_message_is_rejected(self):
        for response in ({"response": "wrong endpoint"}, {"message": None}, {"message": {"content": 12}}):
            with self.subTest(response=response), patch(
                "app.ai_service.request.urlopen", return_value=urlopen_response(response)
            ):
                with self.assertRaises(AIProviderResponseError):
                    OllamaProvider(use_chat_api=True).chat("prompt", timeout=3)

    def test_two_chats_do_not_store_request_controls(self):
        provider = OllamaProvider()
        responses = [
            urlopen_response({"response": "first"}),
            urlopen_response({"response": "second"}),
        ]

        with patch("app.ai_service.request.urlopen", side_effect=responses) as opener:
            provider.chat("one", timeout=2, max_output_tokens=10)
            provider.chat("two", timeout=40, max_output_tokens=900)

        first_payload = json.loads(opener.call_args_list[0].args[0].data.decode("utf-8"))
        second_payload = json.loads(opener.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertEqual(opener.call_args_list[0].kwargs["timeout"], 2)
        self.assertEqual(opener.call_args_list[1].kwargs["timeout"], 40)
        self.assertEqual(first_payload["options"]["num_predict"], 10)
        self.assertEqual(second_payload["options"]["num_predict"], 900)
        self.assertNotIn("_timeout", provider.__dict__)
        self.assertNotIn("max_tokens", provider.__dict__)

    def test_chat_omits_options_without_output_limit(self):
        provider = OllamaProvider()

        with patch(
            "app.ai_service.request.urlopen",
            return_value=urlopen_response({"response": "text"}),
        ) as opener:
            provider.chat("prompt", timeout=3)

        payload = json.loads(opener.call_args.args[0].data.decode("utf-8"))
        self.assertNotIn("options", payload)

    def test_malformed_response_raises_response_error(self):
        with patch(
            "app.ai_service.request.urlopen",
            return_value=urlopen_response({"not_response": "text"}),
        ):
            with self.assertRaises(AIProviderResponseError):
                OllamaProvider().chat("prompt", timeout=3)

    def test_timeout_raises_typed_error_without_sensitive_content(self):
        with patch("app.ai_service.request.urlopen", side_effect=TimeoutError):
            with self.assertRaises(AIProviderTimeoutError) as raised:
                OllamaProvider().chat("secret prompt", timeout=3)

        self.assertNotIn("secret prompt", str(raised.exception))

    def test_network_failure_raises_request_error(self):
        with patch(
            "app.ai_service.request.urlopen",
            side_effect=url_error.URLError("offline"),
        ):
            with self.assertRaises(AIProviderRequestError):
                OllamaProvider().chat("prompt", timeout=3)


class ClaudeProviderTests(unittest.TestCase):
    def test_readiness_is_lightweight_and_uses_existing_key_semantics(self):
        with patch("app.ai_service.request.urlopen") as opener:
            self.assertTrue(ClaudeProvider("sk-ant-test").is_ready())
            self.assertFalse(ClaudeProvider("").is_ready())
            self.assertFalse(ClaudeProvider("other-key").is_ready())

        opener.assert_not_called()

    def test_chat_parses_response_and_maps_request_controls(self):
        provider = ClaudeProvider("sk-ant-secret", model="claude-test")

        with patch(
            "app.ai_service.request.urlopen",
            return_value=urlopen_response(
                {"content": [{"text": "  cloud response  "}]}
            ),
        ) as opener:
            result = provider.chat("prompt", timeout=21, max_output_tokens=444)

        req = opener.call_args.args[0]
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(result, "cloud response")
        self.assertEqual(req.full_url, CLAUDE_API_URL)
        self.assertEqual(opener.call_args.kwargs["timeout"], 21)
        self.assertEqual(payload["model"], "claude-test")
        self.assertEqual(payload["max_tokens"], 444)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "prompt"}])
        self.assertEqual(req.get_header("X-api-key"), "sk-ant-secret")
        self.assertEqual(req.get_header("Anthropic-version"), "2023-06-01")

    def test_chat_uses_compatibility_default_output_limit(self):
        with patch(
            "app.ai_service.request.urlopen",
            return_value=urlopen_response({"content": [{"text": "response"}]}),
        ) as opener:
            ClaudeProvider("sk-ant-test").chat("prompt", timeout=30)

        payload = json.loads(opener.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["max_tokens"], CLAUDE_DEFAULT_MAX_TOKENS)

    def test_malformed_response_raises_response_error(self):
        with patch(
            "app.ai_service.request.urlopen",
            return_value=urlopen_response({"content": []}),
        ):
            with self.assertRaises(AIProviderResponseError):
                ClaudeProvider("sk-ant-test").chat("prompt", timeout=30)

    def test_http_authentication_failures_are_typed_and_hide_api_key(self):
        for status in (401, 403):
            with self.subTest(status=status):
                failure = url_error.HTTPError(
                    CLAUDE_API_URL,
                    status,
                    "denied",
                    None,
                    None,
                )
                with patch("app.ai_service.request.urlopen", side_effect=failure):
                    with self.assertRaises(AIProviderAuthenticationError) as raised:
                        ClaudeProvider("sk-ant-secret").chat("prompt", timeout=30)
                self.assertNotIn("sk-ant-secret", str(raised.exception))

    def test_timeout_raises_typed_error(self):
        with patch("app.ai_service.request.urlopen", side_effect=TimeoutError):
            with self.assertRaises(AIProviderTimeoutError):
                ClaudeProvider("sk-ant-test").chat("prompt", timeout=7)

    def test_other_http_failure_raises_request_error(self):
        failure = url_error.HTTPError(
            CLAUDE_API_URL,
            500,
            "server error",
            None,
            None,
        )
        with patch("app.ai_service.request.urlopen", side_effect=failure):
            with self.assertRaises(AIProviderRequestError):
                ClaudeProvider("sk-ant-test").chat("prompt", timeout=30)

    def test_network_failure_raises_request_error(self):
        with patch(
            "app.ai_service.request.urlopen",
            side_effect=url_error.URLError("offline"),
        ):
            with self.assertRaises(AIProviderRequestError):
                ClaudeProvider("sk-ant-test").chat("prompt", timeout=30)


if __name__ == "__main__":
    unittest.main()
