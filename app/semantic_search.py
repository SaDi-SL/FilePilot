from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from typing import Iterable


SEMANTIC_PIPELINE_REVISION = "9c-semantic-search-v3-model-aware-task-formatting"
MAX_EMBEDDING_DIMENSIONS = 4096


class SemanticSearchError(RuntimeError):
    """Raised when semantic vector data is invalid or cannot be compared safely."""


@dataclass(frozen=True)
class SemanticVector:
    values: tuple[float, ...]
    provider: str
    model: str
    fingerprint: str


def embedding_fingerprint(
    *,
    provider: str,
    model: str,
    dimensions: int,
    revision: str = SEMANTIC_PIPELINE_REVISION,
) -> str:
    normalized_provider = provider.strip().casefold()
    normalized_model = model.strip()
    if not normalized_provider or not normalized_model:
        raise SemanticSearchError("Embedding provider and model are required")
    if not isinstance(dimensions, int) or isinstance(dimensions, bool):
        raise SemanticSearchError("Embedding dimensions must be an integer")
    if dimensions < 1 or dimensions > MAX_EMBEDDING_DIMENSIONS:
        raise SemanticSearchError("Embedding dimensions are outside the supported range")
    payload = "|".join(
        (
            revision,
            f"provider={normalized_provider}",
            f"model={normalized_model}",
            f"dimensions={dimensions}",
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_vector(values: Iterable[float]) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    if not vector or len(vector) > MAX_EMBEDDING_DIMENSIONS:
        raise SemanticSearchError("Embedding vector has unsupported dimensions")
    if any(not math.isfinite(value) for value in vector):
        raise SemanticSearchError("Embedding vector contains a non-finite value")
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude <= 0.0:
        raise SemanticSearchError("Embedding vector magnitude must be greater than zero")
    return tuple(value / magnitude for value in vector)


def encode_vector(values: Iterable[float]) -> tuple[bytes, int]:
    vector = normalize_vector(values)
    return struct.pack(f"<{len(vector)}f", *vector), len(vector)


def decode_vector(payload: bytes, dimensions: int) -> tuple[float, ...]:
    if not isinstance(dimensions, int) or dimensions < 1 or dimensions > MAX_EMBEDDING_DIMENSIONS:
        raise SemanticSearchError("Stored embedding dimensions are invalid")
    expected_size = dimensions * 4
    if len(payload) != expected_size:
        raise SemanticSearchError("Stored embedding payload size is invalid")
    values = struct.unpack(f"<{dimensions}f", payload)
    if any(not math.isfinite(value) for value in values):
        raise SemanticSearchError("Stored embedding contains a non-finite value")
    return tuple(float(value) for value in values)


def cosine_similarity(
    left: Iterable[float],
    right: Iterable[float],
) -> float:
    left_vector = normalize_vector(left)
    right_vector = normalize_vector(right)
    if len(left_vector) != len(right_vector):
        raise SemanticSearchError("Embedding vectors have different dimensions")
    score = sum(a * b for a, b in zip(left_vector, right_vector, strict=True))
    return max(-1.0, min(1.0, float(score)))
