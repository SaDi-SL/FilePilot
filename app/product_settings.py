from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum

from app.product_configuration import (
    ConfigurationDataState,
    ConfigurationSaveStatus,
    ProductConfigurationError,
    ProductConfigurationStore,
)


SUPPORTED_AI_PROVIDERS = ("ollama", "claude")


class SettingEffect(str, Enum):
    MONITORING_STOPPED = "monitoring_stopped"
    INFORMATIONAL = "informational"


class AIConfigurationStatus(str, Enum):
    DISABLED = "disabled"
    LOCAL_CONFIGURED = "local_configured"
    CLOUD_CONFIGURED = "cloud_configured"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class SettingsValidationIssue:
    field: str
    code: str
    message: str


@dataclass(frozen=True)
class GeneralSettings:
    processing_wait_seconds: float
    duplicate_event_window_seconds: float


@dataclass(frozen=True)
class StartupSettings:
    summary: str
    effect: SettingEffect = SettingEffect.INFORMATIONAL


@dataclass(frozen=True)
class AISettings:
    automatic_classification: bool
    provider: str
    ollama_model: str
    credential_configured: bool
    status: AIConfigurationStatus
    status_text: str

    def __repr__(self) -> str:
        return (
            "AISettings(automatic_classification="
            f"{self.automatic_classification!r}, provider={self.provider!r}, "
            f"ollama_model={self.ollama_model!r}, "
            f"credential_configured={self.credential_configured!r}, "
            f"status={self.status!r}, status_text={self.status_text!r})"
        )


@dataclass(frozen=True)
class PrivacySettings:
    summary: str
    cloud_summary: str


@dataclass(frozen=True)
class ProductSettingsCandidate:
    processing_wait_seconds: float
    duplicate_event_window_seconds: float
    automatic_ai_classification: bool
    ai_provider: str
    ollama_model: str


@dataclass(frozen=True)
class SettingsValidationResult:
    valid: bool
    candidate: ProductSettingsCandidate
    issues: tuple[SettingsValidationIssue, ...] = ()


@dataclass(frozen=True)
class ProductSettingsSnapshot:
    state: ConfigurationDataState
    revision: str | None = None
    general: GeneralSettings | None = None
    startup: StartupSettings | None = None
    ai: AISettings | None = None
    privacy: PrivacySettings | None = None
    changes_allowed: bool = False
    issues: tuple[SettingsValidationIssue, ...] = ()
    error: str | None = None

    @classmethod
    def loading(cls) -> "ProductSettingsSnapshot":
        return cls(ConfigurationDataState.LOADING)

    @property
    def candidate(self) -> ProductSettingsCandidate | None:
        if self.general is None or self.ai is None:
            return None
        return ProductSettingsCandidate(
            self.general.processing_wait_seconds,
            self.general.duplicate_event_window_seconds,
            self.ai.automatic_classification,
            self.ai.provider,
            self.ai.ollama_model,
        )


@dataclass(frozen=True)
class SettingsSaveResult:
    status: ConfigurationSaveStatus
    message: str
    snapshot: ProductSettingsSnapshot | None = None
    issues: tuple[SettingsValidationIssue, ...] = ()


class ProductSettingsStore:
    """Secret-redacted Settings projection over the authoritative config store."""

    def __init__(self, configuration_store: ProductConfigurationStore) -> None:
        self._configuration_store = configuration_store

    def read_snapshot(self, *, changes_allowed: bool) -> ProductSettingsSnapshot:
        try:
            document, revision, _ = self._configuration_store.read_document()
            candidate = self.candidate_from_document(document)
            validation = self.validate(
                candidate,
                credential_configured=self._credential_configured(document),
            )
        except FileNotFoundError:
            return ProductSettingsSnapshot(
                ConfigurationDataState.EMPTY,
                error="FilePilot configuration is not available.",
            )
        except (json.JSONDecodeError, ProductConfigurationError) as error:
            return ProductSettingsSnapshot(
                ConfigurationDataState.INVALID,
                error=f"FilePilot settings are invalid: {error}",
            )
        except OSError as error:
            return ProductSettingsSnapshot(
                ConfigurationDataState.ERROR,
                error=f"FilePilot settings could not be read safely: {error}",
            )

        normalized = validation.candidate
        credential_configured = self._credential_configured(document)
        status, status_text = self._ai_status(
            normalized,
            credential_configured=credential_configured,
        )
        return ProductSettingsSnapshot(
            state=ConfigurationDataState.AVAILABLE,
            revision=revision,
            general=GeneralSettings(
                normalized.processing_wait_seconds,
                normalized.duplicate_event_window_seconds,
            ),
            startup=StartupSettings(
                "A normal launch does not start monitoring. The existing --startup "
                "launch mode starts monitoring and hides the classic window. Windows "
                "sign-in registration is not changed from this Settings page."
            ),
            ai=AISettings(
                normalized.automatic_ai_classification,
                normalized.ai_provider,
                normalized.ollama_model,
                credential_configured,
                status,
                status_text,
            ),
            privacy=PrivacySettings(
                "FilePilot organizes configured local folders and stores operation "
                "history, diagnostics, and configuration locally.",
                "Ollama is the local provider. Claude is a cloud provider; when selected, "
                "AI requests can send filenames, category names, history context, or "
                "document excerpts to Anthropic. No provider is contacted to open Settings.",
            ),
            changes_allowed=changes_allowed,
            issues=validation.issues,
        )

    def candidate_from_document(self, document: dict) -> ProductSettingsCandidate:
        ai = document.get("ai", {})
        if not isinstance(ai, dict):
            raise ProductConfigurationError("ai must be an object")
        return ProductSettingsCandidate(
            document.get("processing_wait_seconds", 5),
            document.get("duplicate_event_window_seconds", 3),
            ai.get("enabled", False),
            ai.get("provider", "ollama"),
            ai.get("ollama_model", "mistral"),
        )

    def validate(
        self,
        candidate: ProductSettingsCandidate,
        *,
        credential_configured: bool,
    ) -> SettingsValidationResult:
        issues: list[SettingsValidationIssue] = []
        wait = self._number(
            candidate.processing_wait_seconds,
            "general.processing_wait_seconds",
            "Processing wait",
            issues,
        )
        duplicate_window = self._number(
            candidate.duplicate_event_window_seconds,
            "general.duplicate_event_window_seconds",
            "Duplicate event window",
            issues,
        )
        enabled = candidate.automatic_ai_classification
        if not isinstance(enabled, bool):
            issues.append(
                SettingsValidationIssue(
                    "ai.enabled",
                    "INVALID_AI_ENABLED",
                    "Automatic AI classification must be enabled or disabled.",
                )
            )
            enabled = False
        provider = (
            candidate.ai_provider.strip().lower()
            if isinstance(candidate.ai_provider, str)
            else ""
        )
        if provider not in SUPPORTED_AI_PROVIDERS:
            issues.append(
                SettingsValidationIssue(
                    "ai.provider",
                    "INVALID_AI_PROVIDER",
                    "Choose Local Ollama or Anthropic Claude.",
                )
            )
        model = (
            candidate.ollama_model.strip()
            if isinstance(candidate.ollama_model, str)
            else ""
        )
        if provider == "ollama" and not model:
            issues.append(
                SettingsValidationIssue(
                    "ai.ollama_model",
                    "OLLAMA_MODEL_REQUIRED",
                    "Enter the configured Ollama model name.",
                )
            )
        if len(model) > 200 or any(character in model for character in "\r\n\0"):
            issues.append(
                SettingsValidationIssue(
                    "ai.ollama_model",
                    "INVALID_OLLAMA_MODEL",
                    "The Ollama model name must be one line and at most 200 characters.",
                )
            )
        if enabled and provider == "claude" and not credential_configured:
            issues.append(
                SettingsValidationIssue(
                    "ai.provider",
                    "CLAUDE_CREDENTIAL_REQUIRED",
                    "Claude cannot be enabled because no credential is configured.",
                )
            )
        normalized = ProductSettingsCandidate(
            wait,
            duplicate_window,
            enabled,
            provider,
            model,
        )
        return SettingsValidationResult(not issues, normalized, tuple(issues))

    def validate_against_document(
        self,
        candidate: ProductSettingsCandidate,
        document: dict,
    ) -> SettingsValidationResult:
        return self.validate(
            candidate,
            credential_configured=self._credential_configured(document),
        )

    def document_for_candidate(
        self,
        current: dict,
        candidate: ProductSettingsCandidate,
    ) -> dict:
        credential_configured = self._credential_configured(current)
        validation = self.validate(
            candidate,
            credential_configured=credential_configured,
        )
        if not validation.valid:
            raise ProductConfigurationError("Settings candidate is invalid")
        normalized = validation.candidate
        document = dict(current)
        document["processing_wait_seconds"] = normalized.processing_wait_seconds
        document["duplicate_event_window_seconds"] = (
            normalized.duplicate_event_window_seconds
        )
        existing_ai = current.get("ai", {})
        ai = dict(existing_ai) if isinstance(existing_ai, dict) else {}
        ai["enabled"] = normalized.automatic_ai_classification
        ai["provider"] = normalized.ai_provider
        ai["ollama_model"] = normalized.ollama_model
        document["ai"] = ai
        return document

    @staticmethod
    def _number(value, field, label, issues) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            issues.append(
                SettingsValidationIssue(
                    field,
                    "INVALID_NUMBER",
                    f"{label} must be a number from 0 to 3600 seconds.",
                )
            )
            return 0.0
        normalized = float(value)
        if not math.isfinite(normalized) or not 0 <= normalized <= 3600:
            issues.append(
                SettingsValidationIssue(
                    field,
                    "NUMBER_OUT_OF_RANGE",
                    f"{label} must be from 0 to 3600 seconds.",
                )
            )
        return normalized

    @staticmethod
    def _credential_configured(document: dict) -> bool:
        ai = document.get("ai", {})
        return bool(
            isinstance(ai, dict)
            and isinstance(ai.get("claude_api_key"), str)
            and ai["claude_api_key"].strip()
        )

    @staticmethod
    def _ai_status(
        candidate: ProductSettingsCandidate,
        *,
        credential_configured: bool,
    ) -> tuple[AIConfigurationStatus, str]:
        if not candidate.automatic_ai_classification:
            return AIConfigurationStatus.DISABLED, "Automatic AI classification is disabled."
        if candidate.ai_provider == "ollama" and candidate.ollama_model:
            return (
                AIConfigurationStatus.LOCAL_CONFIGURED,
                "Local Ollama is configured. Availability is not checked on this page.",
            )
        if candidate.ai_provider == "claude" and credential_configured:
            return (
                AIConfigurationStatus.CLOUD_CONFIGURED,
                "Claude credentials are configured but not verified on this page.",
            )
        return (
            AIConfigurationStatus.INCOMPLETE,
            "AI configuration is incomplete.",
        )
