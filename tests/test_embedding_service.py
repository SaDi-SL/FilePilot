import json
import unittest
from urllib import error as url_error
from unittest.mock import MagicMock, patch

from app.ai_service import AIProviderResponseError, AIProviderRequestError
from app.embedding_service import (
    MAX_EMBED_INPUT_CHARS,
    OllamaEmbeddingProvider,
)


def urlopen_response(payload):
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    context = MagicMock()
    context.__enter__.return_value = response
    return context


class OllamaEmbeddingProviderTests(unittest.TestCase):
    def test_embed_uses_local_api_and_normalizes_vector(self):
        provider = OllamaEmbeddingProvider(
            model="nomic-embed-text",
            base_url="http://local/",
        )
        with patch(
            "app.ai_service.request.urlopen",
            return_value=urlopen_response({"embeddings": [[3.0, 4.0]]}),
        ) as opener:
            response = provider.embed("private document", timeout=17)

        req = opener.call_args.args[0]
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(req.full_url, "http://local/api/embed")
        self.assertEqual(payload["model"], "nomic-embed-text")
        self.assertEqual(payload["input"], "search_document: private document")
        self.assertTrue(payload["truncate"])
        self.assertEqual(opener.call_args.kwargs["timeout"], 17)
        self.assertAlmostEqual(response.vector[0], 0.6)
        self.assertAlmostEqual(response.vector[1], 0.8)
        self.assertEqual(response.provider, "ollama")
        self.assertEqual(response.model, "nomic-embed-text")

    def test_query_task_uses_search_query_prefix(self):
        provider = OllamaEmbeddingProvider()
        with patch(
            "app.ai_service.request.urlopen",
            return_value=urlopen_response({"embeddings": [[1.0, 0.0]]}),
        ) as opener:
            provider.embed("train testing", task="query")

        payload = json.loads(opener.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["input"], "search_query: train testing")

    def test_bge_m3_uses_raw_document_and_query_text(self):
        provider = OllamaEmbeddingProvider(model="bge-m3")
        responses = [
            urlopen_response({"embeddings": [[1.0, 0.0]]}),
            urlopen_response({"embeddings": [[1.0, 0.0]]}),
        ]
        with patch(
            "app.ai_service.request.urlopen",
            side_effect=responses,
        ) as opener:
            provider.embed("swedish engineering document", task="document")
            provider.embed("train testing", task="query")

        first_payload = json.loads(
            opener.call_args_list[0].args[0].data.decode("utf-8")
        )
        second_payload = json.loads(
            opener.call_args_list[1].args[0].data.decode("utf-8")
        )
        self.assertEqual(first_payload["input"], "swedish engineering document")
        self.assertEqual(second_payload["input"], "train testing")

    def test_invalid_task_is_rejected_without_network(self):
        provider = OllamaEmbeddingProvider()
        with patch("app.ai_service.request.urlopen") as opener:
            with self.assertRaises(ValueError):
                provider.embed("document", task="other")
        opener.assert_not_called()

    def test_embed_bounds_input_before_local_request(self):
        provider = OllamaEmbeddingProvider()
        content = "x" * (MAX_EMBED_INPUT_CHARS + 100)
        with patch(
            "app.ai_service.request.urlopen",
            return_value=urlopen_response({"embeddings": [[1.0, 0.0]]}),
        ) as opener:
            provider.embed(content)

        payload = json.loads(opener.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(len(payload["input"]), MAX_EMBED_INPUT_CHARS)

    def test_embed_rejects_empty_input_without_network(self):
        provider = OllamaEmbeddingProvider()
        with patch("app.ai_service.request.urlopen") as opener:
            with self.assertRaises(ValueError):
                provider.embed("   ")
        opener.assert_not_called()

    def test_malformed_or_zero_vector_is_typed_failure(self):
        provider = OllamaEmbeddingProvider()
        for payload in (
            {"not_embeddings": []},
            {"embeddings": []},
            {"embeddings": [[0.0, 0.0]]},
        ):
            with self.subTest(payload=payload):
                with patch(
                    "app.ai_service.request.urlopen",
                    return_value=urlopen_response(payload),
                ):
                    with self.assertRaises(AIProviderResponseError):
                        provider.embed("document")

    def test_readiness_requires_exact_local_model_family(self):
        provider = OllamaEmbeddingProvider(model="nomic-embed-text")
        payload = {
            "models": [
                {"name": "qwen3:8b"},
                {"name": "nomic-embed-text:latest"},
            ]
        }
        with patch(
            "app.ai_service.request.urlopen",
            return_value=urlopen_response(payload),
        ):
            self.assertTrue(provider.is_ready())

    def test_readiness_is_false_when_model_is_missing_or_ollama_offline(self):
        provider = OllamaEmbeddingProvider(model="nomic-embed-text")
        with patch(
            "app.ai_service.request.urlopen",
            return_value=urlopen_response({"models": [{"name": "qwen3:8b"}]}),
        ):
            self.assertFalse(provider.is_ready())

        with patch(
            "app.ai_service.request.urlopen",
            side_effect=url_error.URLError("offline"),
        ):
            self.assertFalse(provider.is_ready())


if __name__ == "__main__":
    unittest.main()
