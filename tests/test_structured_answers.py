"""Exercise real Ollama adapter payloads and fail-closed structured evidence validation."""
import json
import unittest
from unittest.mock import patch

from app.ai_service import OllamaProvider
from app.local_ai_readiness import LocalAIReadiness
from app.local_rag import LocalRAGError, LocalRAGService
from tests.test_local_rag import FakeSearch, context


def response(points=None, status="answered"):
    points = [{"text": "Supported Swedish interview notes", "sources": ["S1"]}] if points is None else points
    return {"message": {"content": json.dumps({"status": status, "points": points})}}


class StructuredAnswerTests(unittest.TestCase):
    def setUp(self):
        self.ready = patch("app.local_ai_readiness.prepare_local_ai", return_value=
            LocalAIReadiness(True, ("gemma4:e4b-it-qat",), "gemma4:e4b-it-qat", True, "bge-m3", True))
        self.ready.start()
        self.addCleanup(self.ready.stop)
        transport_patch = patch("app.ai_service._request_json", return_value=response())
        self.transport = transport_patch.start()
        self.addCleanup(transport_patch.stop)
        self.provider = OllamaProvider(model="gemma4:e4b-it-qat", use_chat_api=True, think=False)
        self.service = LocalRAGService(FakeSearch((context(text="Exact original excerpt"),)), self.provider)

    def test_real_adapter_sends_schema_and_renders_source_fields(self):
        result = self.service.ask("Summarize")
        req = self.transport.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual(req.full_url, "http://localhost:11434/api/chat")
        self.assertEqual(payload["format"]["properties"]["points"]["items"]["properties"]["sources"]["items"]["enum"], ["S1"])
        self.assertEqual(payload["options"]["temperature"], 0)
        self.assertFalse(payload["think"])
        self.assertIn("[S1]", result.answer)
        self.assertEqual(result.sources[0].excerpt, "Exact original excerpt")
        self.assertIsNone(self.provider.response_schema)  # never mutate shared provider

    def test_missing_source_on_one_point_rejects_entire_answer(self):
        self.transport.return_value = response([
            {"text": "Supported", "sources": ["S1"]}, {"text": "Unsupported", "sources": []}])
        with self.assertRaisesRegex(LocalRAGError, "missing or unknown source"):
            self.service.ask("Question")
        self.assertEqual(self.transport.call_count, 2)

    def test_unknown_source_is_not_replaced_with_known_source(self):
        self.transport.return_value = response([{"text": "Unsupported", "sources": ["S99"]}])
        with self.assertRaisesRegex(LocalRAGError, "missing or unknown source"):
            self.service.ask("Question")

    def test_inline_invented_reference_is_rejected(self):
        self.transport.return_value = response([{"text": "Wrong [S99]", "sources": ["S1"]}])
        with self.assertRaisesRegex(LocalRAGError, "inconsistent inline"):
            self.service.ask("Question")

    def test_malformed_json_is_regenerated_once_from_original_context(self):
        self.transport.side_effect = [{"message": {"content": "bad output"}}, response()]
        result = self.service.ask("Question")
        self.assertEqual(result.status, "answered")
        retry = json.loads(self.transport.call_args.args[0].data)["messages"][0]["content"]
        self.assertIn("Exact original excerpt", retry)
        self.assertNotIn("bad output", retry)

    def test_no_evidence_is_truthful_not_fabricated_citations(self):
        self.transport.return_value = response([], status="no_evidence")
        result = self.service.ask("Question")
        self.assertEqual(result.status, "no_evidence")
        self.assertEqual(result.sources, ())
        self.assertNotIn("[S1]", result.answer)

    def test_invalid_shapes_fail_with_controlled_validation(self):
        for data in ({"status": [], "points": []}, {"status": "answered", "points": []},
                     {"status": "answered", "points": [{"text": 42, "sources": ["S1"]}]},
                     {"status": "no_evidence", "points": [{"text": "x", "sources": ["S1"]}]},
                     {"status": "answered", "points": [{"text": "x", "sources": "S1"}]}):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    LocalRAGService._parse_structured(json.dumps(data), {"S1": object()})

    def test_shared_timeout_prevents_an_extra_request(self):
        self.transport.return_value = {"message": {"content": "invalid"}}
        with patch("app.local_rag.time.monotonic", side_effect=[0, 1, 46]):
            with self.assertRaisesRegex(LocalRAGError, "timed out"):
                self.service.ask("Question", timeout=45)
        self.assertEqual(self.transport.call_count, 1)

    def test_ordinary_classification_request_keeps_legacy_transport(self):
        self.transport.return_value = {"response": "pdfs"}
        self.assertEqual(OllamaProvider().chat("Classify", timeout=3), "pdfs")
        payload = json.loads(self.transport.call_args.args[0].data)
        self.assertNotIn("format", payload)
        self.assertNotIn("temperature", payload.get("options", {}))
