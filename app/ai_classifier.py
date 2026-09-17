"""
ai_classifier.py — AI-powered file classification engine for FilePilot.

Supports two providers:
    - Ollama  (local, free)   — default
    - Claude  (cloud, paid)   — requires API key

Usage:
    from app.ai_classifier import AIClassifier
    ai = AIClassifier(provider="ollama")
    result = ai.classify("invoice_march_2024.pdf", categories=["invoices", "documents"])
    print(result.category, result.reason)
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Callable

from app.ai_service import AIProvider, AIService, OLLAMA_MODEL, create_ai_service

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
REQUEST_TIMEOUT  = 30


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class AIResult:
    """Result from AI classification."""
    filename:   str
    category:   str | None
    reason:     str
    provider:   str
    confident:  bool = True
    error:      str | None = None

    @property
    def ok(self) -> bool:
        return self.category is not None and self.error is None


@dataclass
class AISuggestion:
    """A suggested rule from AI analysis."""
    category:    str
    keywords:    list[str]
    extensions:  list[str]
    reason:      str
    confidence:  float = 0.8


# ── System prompt ─────────────────────────────────────────────────────────────

def _build_classify_prompt(filename: str, categories: list[str]) -> str:
    cats = ", ".join(f'"{c}"' for c in categories) if categories else "any relevant category"
    return f"""You are a file organization assistant. Classify the following filename into the most appropriate category.

Filename: {filename}
Available categories: {cats}

Rules:
- Return ONLY a JSON object, nothing else
- If no category fits, use "others"
- Be concise in the reason (max 15 words)

Required JSON format:
{{"category": "category_name", "reason": "brief reason", "confident": true}}"""


def _build_suggest_prompt(history_sample: list[dict]) -> str:
    lines = []
    for row in history_sample[-50:]:
        lines.append(f"- {row.get('filename','')} → {row.get('category','')} ({row.get('classification_method','')})")
    history_text = "\n".join(lines) if lines else "No history yet."

    return f"""You are a file organization expert. Analyze the following file processing history and suggest new smart rules.

History (last 50 files):
{history_text}

Based on patterns you see, suggest 3-5 new smart rules. Return ONLY a JSON array:
[
  {{
    "category": "category_name",
    "keywords": ["keyword1", "keyword2"],
    "extensions": [".pdf", ".docx"],
    "reason": "Why this rule makes sense",
    "confidence": 0.9
  }}
]

Focus on patterns not already covered by extension rules. Be specific and practical."""


# ── Temporary document-analyzer compatibility ─────────────────────────────────

class _LegacyProviderBridge:
    """Adapt an AIService provider for AIDocumentAnalyzer until Patch 3B-3."""

    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider
        self._timeout = REQUEST_TIMEOUT

    def is_available(self) -> bool:
        return self._provider.is_ready()

    def chat(self, prompt: str) -> str:
        return self._provider.chat(prompt, timeout=self._timeout)


# ── Main AIClassifier ─────────────────────────────────────────────────────────

class AIClassifier:
    """
    Unified AI classification interface.
    Supports Ollama (local) and Claude (cloud) with automatic fallback.
    """

    def __init__(
        self,
        provider: str = "ollama",
        claude_api_key: str = "",
        ollama_model: str = OLLAMA_MODEL,
        service: AIService | None = None,
    ) -> None:
        self._service = service or create_ai_service({
            "provider": provider,
            "claude_api_key": claude_api_key,
            "ollama_model": ollama_model,
        })
        self.provider_name = self._service.selected_provider

        # AIDocumentAnalyzer still uses these private attributes and mutates
        # _timeout. Remove this bridge when it moves to AIService in Patch 3B-3.
        self._ollama = (
            _LegacyProviderBridge(self._service.ollama_provider)
            if self._service.ollama_provider is not None
            else None
        )
        self._claude = (
            _LegacyProviderBridge(self._service.claude_provider)
            if self._service.claude_provider is not None
            else None
        )
        self._enabled = True

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def get_active_provider(self) -> str:
        """Return name of the currently active provider."""
        return self._service.get_active_provider_name()

    def is_provider_available(self) -> bool:
        """Return whether the selected provider is currently available."""
        return self._service.is_selected_provider_ready()

    def is_available(self) -> bool:
        return self._service.is_available()

    # ── Classification ────────────────────────────────────────────────────────

    def classify(
        self,
        filename: str,
        categories: list[str] | None = None,
    ) -> AIResult:
        """
        Classify a file synchronously.
        Returns AIResult with category and reason.
        """
        if not self._enabled:
            return AIResult(filename, None, "AI disabled", "none")

        provider = self.get_active_provider()
        if provider == "none":
            return AIResult(filename, None, "No AI provider available", "none",
                            error="Ollama not running or Claude API key not set")

        try:
            prompt   = _build_classify_prompt(filename, categories or [])
            response = self._service.chat(
                prompt,
                timeout=REQUEST_TIMEOUT,
                max_output_tokens=300,
            )
            return self._parse_classify_response(
                filename,
                response.text,
                response.provider,
            )
        except Exception as e:
            msg = str(e)
            logger.error(f"AI classify error: {msg}")
            return AIResult(filename, None, msg, provider, error=msg)

    def classify_async(
        self,
        filename: str,
        categories: list[str] | None,
        on_done: Callable[[AIResult], None],
    ) -> None:
        """Classify in a background thread. Calls on_done(result) when complete."""
        def _run():
            result = self.classify(filename, categories)
            on_done(result)
        threading.Thread(target=_run, daemon=True).start()

    # ── Rule suggestions ──────────────────────────────────────────────────────

    def suggest_rules(
        self,
        history: list[dict],
        on_done: Callable[[list[AISuggestion], str | None], None],
    ) -> None:
        """
        Analyze history and suggest new rules in a background thread.
        Calls on_done(suggestions, error) when complete.
        """
        def _run():
            provider = self.get_active_provider()
            if provider == "none":
                on_done([], "No AI provider available")
                return
            try:
                prompt   = _build_suggest_prompt(history)
                response = self._service.chat(
                    prompt,
                    timeout=REQUEST_TIMEOUT,
                    max_output_tokens=800,
                )
                suggestions = self._parse_suggestions(response.text)
                on_done(suggestions, None)
            except Exception as e:
                on_done([], str(e))

        threading.Thread(target=_run, daemon=True).start()

    # ── Internal parsers ──────────────────────────────────────────────────────

    def _parse_classify_response(
        self, filename: str, response: str, provider: str
    ) -> AIResult:
        """Parse JSON response from AI into AIResult."""
        try:
            # Extract JSON from response (model may add extra text)
            start = response.find("{")
            end   = response.rfind("}") + 1
            if start == -1 or end == 0:
                raise ValueError("No JSON found in response")

            data      = json.loads(response[start:end])
            category  = data.get("category", "others").strip().lower()
            reason    = data.get("reason", "AI classified")
            confident = data.get("confident", True)

            if category == "others":
                category = None

            return AIResult(
                filename=filename,
                category=category,
                reason=reason,
                provider=provider,
                confident=confident,
            )
        except Exception as e:
            logger.warning(f"Failed to parse AI response: {e}\nResponse: {response[:200]}")
            return AIResult(filename, None, "Could not parse AI response",
                            provider, error=str(e))

    def _parse_suggestions(self, response: str) -> list[AISuggestion]:
        """Parse JSON array of rule suggestions."""
        try:
            start = response.find("[")
            end   = response.rfind("]") + 1
            if start == -1 or end == 0:
                return []

            items = json.loads(response[start:end])
            suggestions = []
            for item in items:
                s = AISuggestion(
                    category=item.get("category", "").strip(),
                    keywords=item.get("keywords", []),
                    extensions=item.get("extensions", []),
                    reason=item.get("reason", ""),
                    confidence=float(item.get("confidence", 0.8)),
                )
                if s.category:
                    suggestions.append(s)
            return suggestions
        except Exception as e:
            logger.warning(f"Failed to parse suggestions: {e}")
            return []


# ── Global instance helper ────────────────────────────────────────────────────

_instance: AIClassifier | None = None


def get_ai_classifier(config: dict) -> AIClassifier:
    """Return (or create) the global AIClassifier from app config."""
    global _instance
    if _instance is None:
        ai_config = config.get("ai", {})
        _instance = AIClassifier(
            provider=ai_config.get("provider", "ollama"),
            claude_api_key=ai_config.get("claude_api_key", ""),
            ollama_model=ai_config.get("ollama_model", OLLAMA_MODEL),
        )
    return _instance


def reset_ai_classifier() -> None:
    """Reset the global instance (call after config changes)."""
    global _instance
    _instance = None
