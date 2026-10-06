from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.search_index import SearchIndex, SearchIndexError, SearchResult


@dataclass(frozen=True)
class SearchRefreshResult:
    scanned: int
    indexed: int
    unchanged: int
    removed: int
    failed: int
    errors: tuple[str, ...] = ()


class ProductSearch:
    """Reconcile and query FilePilot's local search catalog."""

    def __init__(self, index: SearchIndex | None = None) -> None:
        self._index = index or SearchIndex()

    def search(self, query: str, *, limit: int = 25) -> tuple[SearchResult, ...]:
        return self._index.search(query, limit=limit)

    def refresh(self, organized_root: str | Path) -> SearchRefreshResult:
        root = Path(organized_root).resolve()
        if not root.is_dir():
            raise SearchIndexError("Organized folder is unavailable for indexing")

        current_paths: set[Path] = set()
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
