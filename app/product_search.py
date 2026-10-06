from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.embedding_service import OllamaEmbeddingProvider
from app.search_index import (
    SearchIndex,
    SearchIndexError,
    SearchResult,
    SemanticSearchResult,
)
from app.semantic_search import embedding_fingerprint


@dataclass(frozen=True)
class SearchRefreshResult:
    scanned: int
    indexed: int
    unchanged: int
    removed: int
    failed: int
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class HybridSearchResult:
    path: Path
    filename: str
    extension: str
    category: str | None
    score: float
    semantic_score: float | None
    lexical_rank: int | None
    snippet: str = ""


@dataclass(frozen=True)
class SemanticRefreshResult:
    scanned: int
    embedded: int
    unchanged: int
    skipped: int
    failed: int
    errors: tuple[str, ...] = ()


class ProductSearch:
    """Reconcile and query FilePilot's local search catalog."""

    def __init__(
        self,
        index: SearchIndex | None = None,
        embedding_provider: OllamaEmbeddingProvider | None = None,
    ) -> None:
        self._index = index or SearchIndex()
        self._embedding_provider = embedding_provider

    def search(self, query: str, *, limit: int = 25) -> tuple[SearchResult, ...]:
        return self._index.search(query, limit=limit)

    def refresh_semantic_embeddings(
        self,
        *,
        timeout: float = 30.0,
    ) -> SemanticRefreshResult:
        provider = self._embedding_provider
        if provider is None:
            raise SearchIndexError("Semantic search provider is not configured")
        if not provider.is_ready(timeout=min(timeout, 3.0)):
            raise SearchIndexError("Local semantic search model is unavailable")

        scanned = embedded = unchanged = skipped = failed = 0
        errors: list[str] = []
        for path in self._index.indexed_paths():
            scanned += 1
            try:
                chunks = self._index.semantic_document_chunks(path)
                if not chunks:
                    skipped += 1
                    continue

                responses = [
                    provider.embed(chunk, timeout=timeout, task="document")
                    for chunk in chunks
                ]
                first = responses[0]
                fingerprint = embedding_fingerprint(
                    provider=first.provider,
                    model=first.model,
                    dimensions=len(first.vector),
                )
                if self._index.embedding_fingerprint_for_file(path) == fingerprint:
                    unchanged += 1
                    continue

                self._index.upsert_embedding_chunks(
                    path,
                    [response.vector for response in responses],
                    provider=first.provider,
                    model=first.model,
                    embedding_fingerprint=fingerprint,
                )
                embedded += 1
            except Exception as error:
                failed += 1
                errors.append(f"{path.name}: {error}")

        return SemanticRefreshResult(
            scanned=scanned,
            embedded=embedded,
            unchanged=unchanged,
            skipped=skipped,
            failed=failed,
            errors=tuple(errors),
        )

    def hybrid_search(
        self,
        query: str,
        *,
        limit: int = 25,
        timeout: float = 30.0,
    ) -> tuple[HybridSearchResult, ...]:
        """Fuse semantic and lexical evidence with reciprocal-rank fusion."""
        if not isinstance(query, str) or not query.strip():
            return ()

        candidate_limit = max(10, min(100, int(limit) * 4))
        semantic = self.semantic_search(
            query,
            limit=candidate_limit,
            timeout=timeout,
        )
        lexical = self._index.search_relaxed(
            query,
            limit=candidate_limit,
        )

        # RRF is deliberately rank-based so BM25 and cosine remain on their
        # native scales. Lexical evidence gets a modest boost so a generic
        # semantic near-match cannot outrank a result that also contains
        # concrete query evidence.
        rrf_k = 60.0
        semantic_weight = 1.0
        lexical_weight = 1.35
        combined: dict[Path, dict] = {}

        for rank, item in enumerate(semantic, start=1):
            entry = combined.setdefault(
                item.path,
                {
                    "filename": item.filename,
                    "extension": item.extension,
                    "category": item.category,
                    "score": 0.0,
                    "semantic_score": None,
                    "lexical_rank": None,
                    "snippet": "",
                },
            )
            entry["score"] += semantic_weight / (rrf_k + rank)
            entry["semantic_score"] = item.score

        for rank, item in enumerate(lexical, start=1):
            entry = combined.setdefault(
                item.path,
                {
                    "filename": item.filename,
                    "extension": item.extension,
                    "category": item.category,
                    "score": 0.0,
                    "semantic_score": None,
                    "lexical_rank": None,
                    "snippet": "",
                },
            )
            entry["score"] += lexical_weight / (rrf_k + rank)
            entry["lexical_rank"] = rank
            entry["snippet"] = item.snippet

        results = [
            HybridSearchResult(
                path=path,
                filename=entry["filename"],
                extension=entry["extension"],
                category=entry["category"],
                score=float(entry["score"]),
                semantic_score=entry["semantic_score"],
                lexical_rank=entry["lexical_rank"],
                snippet=entry["snippet"],
            )
            for path, entry in combined.items()
        ]
        results.sort(
            key=lambda item: (
                -item.score,
                -(item.semantic_score if item.semantic_score is not None else -1.0),
                item.filename.casefold(),
            )
        )
        return tuple(results[: max(1, min(int(limit), 100))])

    def semantic_search(
        self,
        query: str,
        *,
        limit: int = 25,
        timeout: float = 30.0,
    ) -> tuple[SemanticSearchResult, ...]:
        provider = self._embedding_provider
        if provider is None:
            raise SearchIndexError("Semantic search provider is not configured")
        if not isinstance(query, str) or not query.strip():
            return ()

        response = provider.embed(query, timeout=timeout, task="query")
        fingerprint = embedding_fingerprint(
            provider=response.provider,
            model=response.model,
            dimensions=len(response.vector),
        )
        return self._index.semantic_search(
            response.vector,
            embedding_fingerprint=fingerprint,
            limit=limit,
        )

    def refresh(self, organized_root: str | Path) -> SearchRefreshResult:
        root = Path(organized_root).resolve()
        if not root.is_dir():
            raise SearchIndexError("Organized folder is unavailable for indexing")

        current_paths: set[Path] = set()
        extraction_fingerprint = self._index.extraction_fingerprint()
        scanned = indexed = unchanged = failed = 0
        errors: list[str] = []

        for candidate in self._iter_regular_files(root):
            scanned += 1
            current_paths.add(candidate)
            try:
                stat = candidate.stat()
                existing = self._index.get_file(candidate)
                if (
                    existing is not None
                    and existing.size_bytes == stat.st_size
                    and existing.modified_ns == stat.st_mtime_ns
                    and existing.extraction_fingerprint == extraction_fingerprint
                ):
                    unchanged += 1
                    continue

                self._index.index_file(
                    candidate,
                    category=self._category_for(root, candidate),
                )
                indexed += 1
            except (OSError, SearchIndexError) as error:
                failed += 1
                errors.append(f"{candidate.name}: {error}")

        removed = 0
        for indexed_path in self._index.indexed_paths():
            if not self._is_within(root, indexed_path) or indexed_path not in current_paths:
                try:
                    if self._index.remove_file(indexed_path):
                        removed += 1
                except SearchIndexError as error:
                    failed += 1
                    errors.append(f"{indexed_path.name}: {error}")

        return SearchRefreshResult(
            scanned=scanned,
            indexed=indexed,
            unchanged=unchanged,
            removed=removed,
            failed=failed,
            errors=tuple(errors),
        )

    @staticmethod
    def _iter_regular_files(root: Path):
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            base = Path(directory)
            dirnames[:] = [
                name
                for name in dirnames
                if not (base / name).is_symlink()
            ]
            for filename in filenames:
                candidate = (base / filename)
                try:
                    if candidate.is_symlink() or not candidate.is_file():
                        continue
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if ProductSearch._is_within(root, resolved):
                    yield resolved

    @staticmethod
    def _category_for(root: Path, file_path: Path) -> str | None:
        try:
            relative = file_path.relative_to(root)
        except ValueError:
            return None
        if len(relative.parts) < 2:
            return None
        category = relative.parts[0].strip()
        return category or None

    @staticmethod
    def _is_within(root: Path, candidate: Path) -> bool:
        try:
            candidate.resolve().relative_to(root.resolve())
            return True
        except (OSError, ValueError):
            return False
