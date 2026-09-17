import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.ai_classifier import (
    AIClassifier,
    AIResult,
    AISuggestion,
    get_ai_classifier,
    reset_ai_classifier,
)
from app.ai_document_analyzer import AIDocumentAnalyzer, DocumentAnalysis


class ImmediateThread:
    def __init__(self, target, daemon=False):
        self.target = target
        self.daemon = daemon

    def start(self):
        self.target()


class AIProviderBehaviorTests(unittest.TestCase):
    def tearDown(self):
        reset_ai_classifier()

    @staticmethod
    def _provider(available, response="", failure=None):
        provider = MagicMock()
        provider.is_available.return_value = available
        provider._timeout = 30
        if failure is not None:
            provider.chat.side_effect = failure
        else:
            provider.chat.return_value = response
        return provider

    def _classifier(
        self,
        selected="ollama",
        ollama_available=True,
        claude_available=True,
        ollama_response="",
        claude_response="",
        ollama_failure=None,
        claude_failure=None,
    ):
        classifier = AIClassifier(
            provider=selected,
            claude_api_key="sk-ant-test",
        )
        classifier._ollama = self._provider(
            ollama_available,
            ollama_response,
            ollama_failure,
        )
        classifier._claude = self._provider(
            claude_available,
            claude_response,
            claude_failure,
        )
        return classifier

    def test_provider_selection_and_fallback_matrix(self):
        cases = [
            ("ollama", True, True, "ollama"),
            ("claude", True, True, "claude"),
            ("claude", True, False, "ollama"),
            ("ollama", False, True, "none"),
            ("claude", False, False, "none"),
        ]

        for selected, ollama_available, claude_available, expected in cases:
            with self.subTest(
                selected=selected,
                ollama_available=ollama_available,
                claude_available=claude_available,
            ):
                classifier = self._classifier(
                    selected,
                    ollama_available,
                    claude_available,
                )

                self.assertEqual(classifier.get_active_provider(), expected)
                self.assertEqual(classifier.is_available(), expected != "none")

    def test_missing_and_unknown_provider_configuration_use_safe_ollama_default(self):
        default_classifier = get_ai_classifier({})
        self.assertEqual(default_classifier.provider_name, "ollama")

        unknown_classifier = self._classifier(selected="unexpected")
        self.assertEqual(unknown_classifier.get_active_provider(), "ollama")

        unknown_classifier._ollama.is_available.return_value = False
        self.assertEqual(unknown_classifier.get_active_provider(), "none")

    def test_classify_parses_success_and_reports_provider_actually_used(self):
        response = json.dumps({
            "category": " Reports ",
            "reason": "Quarterly figures detected",
            "confident": False,
        })
        classifier = self._classifier(
            selected="claude",
            claude_available=False,
            ollama_response=response,
        )

        result = classifier.classify("quarterly.txt", ["reports", "documents"])

        self.assertIsInstance(result, AIResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.category, "reports")
        self.assertEqual(result.reason, "Quarterly figures detected")
        self.assertFalse(result.confident)
        self.assertEqual(result.provider, "ollama")
        classifier._ollama.chat.assert_called_once()
        classifier._claude.chat.assert_not_called()

    def test_classify_malformed_response_returns_failed_result(self):
        classifier = self._classifier(ollama_response="not JSON")

        result = classifier.classify("unknown.bin")

        self.assertIsInstance(result, AIResult)
        self.assertFalse(result.ok)
        self.assertIsNone(result.category)
        self.assertEqual(result.reason, "Could not parse AI response")
        self.assertEqual(result.provider, "ollama")
        self.assertIn("No JSON found", result.error)

    def test_classify_provider_failure_returns_failed_result(self):
        classifier = self._classifier(
            ollama_failure=RuntimeError("provider request failed")
        )

        result = classifier.classify("unknown.bin")

        self.assertIsInstance(result, AIResult)
        self.assertFalse(result.ok)
        self.assertEqual(result.provider, "ollama")
        self.assertEqual(result.error, "provider request failed")

    @patch("app.ai_classifier.threading.Thread", ImmediateThread)
    def test_suggest_rules_parses_valid_response_and_preserves_reasoning(self):
        response = json.dumps([{
            "category": "receipts",
            "keywords": ["receipt", "total"],
            "extensions": [".pdf"],
            "reason": "Repeated receipt filenames",
            "confidence": 0.95,
        }])
        classifier = self._classifier(
            selected="claude",
            claude_response=response,
        )
        callback = MagicMock()

        classifier.suggest_rules([{"filename": "receipt.pdf"}], callback)

        suggestions, error = callback.call_args.args
        self.assertIsNone(error)
        self.assertEqual(len(suggestions), 1)
        self.assertIsInstance(suggestions[0], AISuggestion)
        self.assertEqual(suggestions[0].category, "receipts")
        self.assertEqual(suggestions[0].keywords, ["receipt", "total"])
        self.assertEqual(suggestions[0].extensions, [".pdf"])
        self.assertEqual(suggestions[0].reason, "Repeated receipt filenames")
        self.assertEqual(suggestions[0].confidence, 0.95)
        classifier._claude.chat.assert_called_once()
        classifier._ollama.chat.assert_not_called()

    @patch("app.ai_classifier.threading.Thread", ImmediateThread)
    def test_suggest_rules_malformed_response_fails_safely(self):
        classifier = self._classifier(ollama_response="not JSON")
        callback = MagicMock()

        classifier.suggest_rules([], callback)

        callback.assert_called_once_with([], None)

    @patch("app.ai_classifier.threading.Thread", ImmediateThread)
    def test_suggest_rules_provider_failure_uses_error_callback(self):
        classifier = self._classifier(
            ollama_failure=RuntimeError("suggestion request failed")
        )
        callback = MagicMock()

        classifier.suggest_rules([], callback)

        callback.assert_called_once_with([], "suggestion request failed")

    def test_manual_analysis_succeeds_when_automatic_classification_is_disabled(self):
        response = json.dumps({
            "doc_type": "invoice",
            "category": "invoices",
            "smart_folder": "Invoices/2026",
            "summary": "Invoice for consulting services.",
            "key_dates": [{
                "label": "Payment due",
                "date": "2026-10-01",
                "description": "Invoice payment deadline",
                "remind_days_before": 5,
            }],
            "entities": {"amount": "$125.00"},
            "tips": ["Schedule payment"],
            "confidence": 0.92,
        })
        classifier = self._classifier(ollama_response=response)
        classifier.disable()

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "invoice.txt"
            file_path.write_text("Invoice total: $125.00", encoding="utf-8")
            result = AIDocumentAnalyzer(classifier).analyze(
                file_path,
                ["invoices", "documents"],
            )

        self.assertIsInstance(result, DocumentAnalysis)
        self.assertTrue(result.ok)
        self.assertEqual(result.doc_type, "invoice")
        self.assertEqual(result.category, "invoices")
        self.assertEqual(result.provider, "ollama")
        self.assertEqual(result.key_dates[0].label, "Payment due")
        classifier._ollama.chat.assert_called_once()

    def test_manual_analysis_malformed_response_returns_failed_result(self):
        classifier = self._classifier(ollama_response="not JSON")

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "document.txt"
            file_path.write_text("Document content", encoding="utf-8")
            result = AIDocumentAnalyzer(classifier).analyze(file_path)

        self.assertIsInstance(result, DocumentAnalysis)
        self.assertFalse(result.ok)
        self.assertEqual(result.summary, "Could not parse AI response")
        self.assertIn("No JSON in response", result.error)

    def test_manual_analysis_provider_failure_returns_failed_result(self):
        classifier = self._classifier(
            ollama_failure=RuntimeError("analysis request failed")
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "document.txt"
            file_path.write_text("Document content", encoding="utf-8")
            result = AIDocumentAnalyzer(classifier).analyze(file_path)

        self.assertIsInstance(result, DocumentAnalysis)
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "analysis request failed")
        self.assertEqual(result.summary, "Analysis failed: analysis request failed")


if __name__ == "__main__":
    unittest.main()
