from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import request

from app.ai_service import (
    AIProviderRequestError,
    AIProviderResponseError,
    OLLAMA_BASE_URL,
    _request_json,
)
from app.semantic_search import SemanticSearchError, normalize_vector


DEFAULT_OLLAMA_EMBEDDING_MODEL = "bge-m3"
MAX_EMBED_INPUT_CHARS = 12_000


@dataclass(frozen=True)
class EmbeddingResponse:
    vector: tuple[float, ...]
    provider: str
    model: str


@dataclass(frozen=True)
class OllamaEmbeddingProvider:
    """Local-only Ollama embedding adapter using the native /api/embed endpoint."""

    model: str = DEFAULT_OLLAMA_EMBEDDING_MODEL
    base_url: str = OLLAMA_BASE_URL

    provider_name = "ollama"

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))
        if not self.model.strip():
            raise ValueError("Embedding model name is required")

    def embed(
        self,
        text: str,
        *,
        timeout: float = 30.0,
        task: str = "document",
    ) -> EmbeddingResponse:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Embedding input must contain text")
        if task not in {"document", "query"}:
            raise ValueError("Embedding task must be document or query")
        model_family = self.model.casefold().split(":", 1)[0]
        if model_family == "nomic-embed-text":
            prefix = "search_document: " if task == "document" else "search_query: "
        else:
            prefix = ""
        bounded = (prefix + text)[:MAX_EMBED_INPUT_CHARS]
        payload: dict[str, Any] = {
            "model": self.model,
            "input": bounded,
            "truncate": True,
        }
        req = request.Request(
            f"{self.base_url}/api/embed",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        data = _request_json(
            req,
            timeout=timeout,
            provider_name=f"{self.provider_name} embeddings",
        )

        try:
            embeddings = data["embeddings"]
            vector = embeddings[0]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderResponseError(
                "ollama embeddings returned an invalid response"
            ) from exc
        if not isinstance(vector, list):
            raise AIProviderResponseError(
                "ollama embeddings returned an invalid response"
            )

        try:
            normalized = normalize_vector(vector)
        except (SemanticSearchError, TypeError, ValueError) as exc:
            raise AIProviderResponseError(
                "ollama embeddings returned an invalid vector"
            ) from exc

        return EmbeddingResponse(
            vector=normalized,
            provider=self.provider_name,
            model=self.model,
        )

    def is_ready(self, *, timeout: float = 3.0) -> bool:
        try:
            req = request.Request(f"{self.base_url}/api/tags")
            data = _request_json(
                req,
                timeout=timeout,
                provider_name=f"{self.provider_name} embeddings",
            )
        except AIProviderRequestError:
            return False
        except Exception:
            return False

        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            return False
        requested = self.model.casefold()
        for item in models:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("model")
            if isinstance(name, str) and name.casefold().split(":")[0] == requested.split(":")[0]:
                return True
        return False
