"""Provider-independent AI transport and provider selection for FilePilot."""
from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping, Protocol
from urllib import error as url_error
from urllib import request


OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "mistral"
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_DEFAULT_MAX_TOKENS = 300


class AIProvider(Protocol):
    """Minimal contract implemented by an AI provider adapter."""

    name: str
    is_cloud: bool

    def is_ready(self, *, timeout: float = 3.0) -> bool:
        ...

    def chat(
        self,
        prompt: str,
        *,
        timeout: float,
        max_output_tokens: int | None = None,
    ) -> str:
        ...


@dataclass(frozen=True)
class AIResponse:
    """Text returned by the provider that actually handled the request."""

    text: str
    provider: str
    is_cloud: bool


class AIProviderError(Exception):
    """Base class for provider and transport failures."""


class AIProviderUnavailableError(AIProviderError):
    """No provider permitted by the selection policy is ready."""


class AIProviderTimeoutError(AIProviderError):
    """A provider request exceeded its request-specific timeout."""


class AIProviderAuthenticationError(AIProviderError):
    """A provider rejected its configured credentials."""


class AIProviderResponseError(AIProviderError):
    """A provider returned a malformed transport response."""


class AIProviderRequestError(AIProviderError):
    """A provider request failed for another HTTP or network reason."""


def _request_json(
    req: request.Request,
    *,
    timeout: float,
    provider_name: str,
) -> Any:
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read()
    except url_error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AIProviderAuthenticationError(
                f"{provider_name} authentication failed"
            ) from exc
        raise AIProviderRequestError(f"{provider_name} request failed") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise AIProviderTimeoutError(f"{provider_name} request timed out") from exc
    except url_error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise AIProviderTimeoutError(
                f"{provider_name} request timed out"
            ) from exc
        raise AIProviderRequestError(f"{provider_name} request failed") from exc
    except OSError as exc:
        raise AIProviderRequestError(f"{provider_name} request failed") from exc

    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
        raise AIProviderResponseError(
            f"{provider_name} returned an invalid response"
        ) from exc


@dataclass(frozen=True)
class OllamaProvider:
    """Local Ollama adapter using its tags and generate HTTP endpoints."""

    model: str = OLLAMA_MODEL
    base_url: str = OLLAMA_BASE_URL

    name: ClassVar[str] = "ollama"
    is_cloud: ClassVar[bool] = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    def is_ready(self, *, timeout: float = 3.0) -> bool:
        try:
            req = request.Request(f"{self.base_url}/api/tags")
            with request.urlopen(req, timeout=timeout):
                return True
        except Exception:
            return False

    def chat(
        self,
        prompt: str,
        *,
        timeout: float,
        max_output_tokens: int | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }
        if max_output_tokens is not None:
            payload["options"] = {"num_predict": max_output_tokens}

        req = request.Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        data = _request_json(req, timeout=timeout, provider_name=self.name)

        if not isinstance(data, dict) or not isinstance(data.get("response"), str):
            raise AIProviderResponseError(
                f"{self.name} returned an invalid response"
            )
        return data["response"].strip()


@dataclass(frozen=True)
class ClaudeProvider:
    """Anthropic Claude adapter using FilePilot's existing Messages API setup."""

    api_key: str = field(repr=False)
    model: str = CLAUDE_MODEL
    api_url: str = CLAUDE_API_URL

    name: ClassVar[str] = "claude"
    is_cloud: ClassVar[bool] = True

    def is_ready(self, *, timeout: float = 3.0) -> bool:
        del timeout
        return bool(self.api_key and self.api_key.startswith("sk-ant-"))

    def chat(
        self,
        prompt: str,
        *,
        timeout: float,
        max_output_tokens: int | None = None,
    ) -> str:
        output_limit = (
            CLAUDE_DEFAULT_MAX_TOKENS
            if max_output_tokens is None
            else max_output_tokens
        )
        payload = {
            "model": self.model,
            "max_tokens": output_limit,
            "messages": [{"role": "user", "content": prompt}],
        }
        req = request.Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        data = _request_json(req, timeout=timeout, provider_name=self.name)

        try:
            text = data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderResponseError(
                f"{self.name} returned an invalid response"
            ) from exc
        if not isinstance(text, str):
            raise AIProviderResponseError(
                f"{self.name} returned an invalid response"
            )
        return text.strip()


@dataclass(frozen=True)
class AIService:
    """Resolve ready providers while preventing local-to-cloud escalation."""

    selected_provider: str = "ollama"
    ollama_provider: AIProvider | None = field(default_factory=OllamaProvider)
    claude_provider: AIProvider | None = None

    @staticmethod
    def _is_ready(provider: AIProvider | None, *, timeout: float) -> bool:
        if provider is None:
            return False
        try:
            return provider.is_ready(timeout=timeout)
        except Exception:
            return False

    def is_selected_provider_ready(self, *, timeout: float = 3.0) -> bool:
        if self.selected_provider == "ollama":
            return self._is_ready(self.ollama_provider, timeout=timeout)
        if self.selected_provider == "claude":
            return self._is_ready(self.claude_provider, timeout=timeout)
        return False

    def _resolve_provider(
        self,
        *,
        allow_fallback: bool,
        readiness_timeout: float = 3.0,
    ) -> AIProvider | None:
        if self.selected_provider == "claude":
            if self._is_ready(self.claude_provider, timeout=readiness_timeout):
                return self.claude_provider
            if allow_fallback and self._is_ready(
                self.ollama_provider,
                timeout=readiness_timeout,
            ):
                return self.ollama_provider
            return None

        # Missing and unknown selections retain FilePilot's safe Ollama behavior.
        if self._is_ready(self.ollama_provider, timeout=readiness_timeout):
            return self.ollama_provider
        return None

    def get_active_provider_name(
        self,
        *,
        allow_fallback: bool = True,
        timeout: float = 3.0,
    ) -> str:
        provider = self._resolve_provider(
            allow_fallback=allow_fallback,
            readiness_timeout=timeout,
        )
        return provider.name if provider is not None else "none"

    def is_available(self, *, timeout: float = 3.0) -> bool:
        return self._resolve_provider(
            allow_fallback=True,
            readiness_timeout=timeout,
        ) is not None

    def chat(
        self,
        prompt: str,
        *,
        timeout: float = 30,
        max_output_tokens: int | None = None,
        allow_fallback: bool = True,
    ) -> AIResponse:
        provider = self._resolve_provider(allow_fallback=allow_fallback)
        if provider is None:
            raise AIProviderUnavailableError("No usable AI provider is available")

        text = provider.chat(
            prompt,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
        )
        return AIResponse(
            text=text,
            provider=provider.name,
            is_cloud=provider.is_cloud,
        )


def create_ai_service(ai_config: Mapping[str, Any] | None = None) -> AIService:
    """Build an uncached service from FilePilot's existing ``ai`` config section."""

    config = ai_config or {}
    selected_provider = config.get("provider", "ollama")
    ollama = OllamaProvider(model=config.get("ollama_model", OLLAMA_MODEL))
    api_key = config.get("claude_api_key", "")
    claude = ClaudeProvider(api_key=api_key) if api_key else None
    return AIService(
        selected_provider=selected_provider,
        ollama_provider=ollama,
        claude_provider=claude,
    )
