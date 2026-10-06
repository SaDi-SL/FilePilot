from __future__ import annotations

import hashlib
import importlib.util
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.application_paths import get_application_paths
from app.content_reader import extract_file_content_result
from app.ocr_runtime import discover_ocr_runtime
from app.semantic_search import (
    SemanticSearchError,
    decode_vector,
    encode_vector,
    normalize_vector,
)


SEARCH_APPLICATION_ID = 0x46505349  # "FPSI"
SEARCH_SCHEMA_VERSION = 4
SEARCH_EXTRACTION_PIPELINE_REVISION = "9b-richer-extraction-ocr-v1"
MAX_SEARCH_RESULTS = 100


class SearchIndexError(RuntimeError):
    """Raised when the local search index cannot be used safely."""


@dataclass(frozen=True)
class IndexedFile:
    path: Path
    filename: str
    extension: str
    size_bytes: int
    modified_ns: int
    category: str | None
    extraction_status: str
    extraction_fingerprint: str
    indexed_at_utc: str


@dataclass(frozen=True)
class SearchResult:
    path: Path
    filename: str
    extension: str
    category: str | None
    snippet: str
    rank: float


@dataclass(frozen=True)
class SemanticSearchResult:
    path: Path
    filename: str
    extension: str
    category: str | None
    score: float


def current_extraction_fingerprint() -> str:
    """Fingerprint extraction semantics and local capabilities that affect searchable content."""
    runtime = discover_ocr_runtime()
    pdf_renderer_available = importlib.util.find_spec("pypdfium2") is not None
    payload = "|".join(
        (
            SEARCH_EXTRACTION_PIPELINE_REVISION,
            f"ocr_available={int(runtime.available)}",
            "ocr_languages=" + ",".join(sorted(language.casefold() for language in runtime.languages)),
            f"pdf_renderer={int(pdf_renderer_available)}",
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SearchIndex:
    """Persistent local metadata and full-text index for FilePilot files."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        path = Path(database_path or get_application_paths().search_index_file)
        if not path.is_absolute():
            raise SearchIndexError("Search index path must be absolute")
        self.database_path = path.resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        try:
            with closing(self._connect()) as connection, connection:
                application_id = connection.execute(
                    "PRAGMA application_id"
                ).fetchone()[0]
                user_version = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]

                if application_id not in (0, SEARCH_APPLICATION_ID):
                    raise SearchIndexError(
                        "Search database belongs to another application"
                    )
                if user_version > SEARCH_SCHEMA_VERSION:
                    raise SearchIndexError(
                        "Search database schema is newer than this FilePilot build"
                    )

                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS files (
                        path TEXT PRIMARY KEY NOT NULL,
                        filename TEXT NOT NULL,
                        extension TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
                        modified_ns INTEGER NOT NULL CHECK(modified_ns >= 0),
                        category TEXT,
                        content TEXT NOT NULL DEFAULT '',
                        extraction_status TEXT NOT NULL,
                        extraction_fingerprint TEXT NOT NULL DEFAULT '',
                        indexed_at_utc TEXT NOT NULL
                    );

                    CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                        filename,
                        path,
                        category,
                        content,
                        content='files',
                        content_rowid='rowid'
                    );

                    CREATE TABLE IF NOT EXISTS file_embeddings (
                        path TEXT PRIMARY KEY NOT NULL,
                        vector BLOB NOT NULL,
                        dimensions INTEGER NOT NULL CHECK(dimensions > 0),
                        provider TEXT NOT NULL,
                        model TEXT NOT NULL,
                        embedding_fingerprint TEXT NOT NULL,
                        embedded_at_utc TEXT NOT NULL,
                        FOREIGN KEY(path) REFERENCES files(path) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_file_embeddings_fingerprint
                    ON file_embeddings(embedding_fingerprint);

                    CREATE TABLE IF NOT EXISTS file_embedding_chunks (
                        path TEXT NOT NULL,
                        chunk_index INTEGER NOT NULL CHECK(chunk_index >= 0),
                        vector BLOB NOT NULL,
                        dimensions INTEGER NOT NULL CHECK(dimensions > 0),
                        provider TEXT NOT NULL,
                        model TEXT NOT NULL,
                        embedding_fingerprint TEXT NOT NULL,
                        embedded_at_utc TEXT NOT NULL,
                        PRIMARY KEY(path, chunk_index),
                        FOREIGN KEY(path) REFERENCES files(path) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_file_embedding_chunks_fingerprint
                    ON file_embedding_chunks(embedding_fingerprint);

                    CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
                        INSERT INTO files_fts(rowid, filename, path, category, content)
                        VALUES (new.rowid, new.filename, new.path, new.category, new.content);
                    END;

                    CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
                        INSERT INTO files_fts(files_fts, rowid, filename, path, category, content)
                        VALUES ('delete', old.rowid, old.filename, old.path, old.category, old.content);
                    END;

                    CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
                        INSERT INTO files_fts(files_fts, rowid, filename, path, category, content)
                        VALUES ('delete', old.rowid, old.filename, old.path, old.category, old.content);
                        INSERT INTO files_fts(rowid, filename, path, category, content)
                        VALUES (new.rowid, new.filename, new.path, new.category, new.content);
                    END;
                    """
                )
                if user_version < 2:
                    columns = {
                        row["name"]
                        for row in connection.execute("PRAGMA table_info(files)").fetchall()
                    }
                    if "extraction_fingerprint" not in columns:
                        connection.execute(
                            "ALTER TABLE files ADD COLUMN "
                            "extraction_fingerprint TEXT NOT NULL DEFAULT ''"
                        )

                connection.execute(f"PRAGMA application_id = {SEARCH_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version = {SEARCH_SCHEMA_VERSION}")
        except SearchIndexError:
            raise
        except sqlite3.Error as error:
            raise SearchIndexError(f"Search index initialization failed: {error}") from error

    def index_file(
        self,
        file_path: str | Path,
        *,
        category: str | None = None,
        max_chars: int = 100_000,
    ) -> IndexedFile:
        source = Path(file_path).resolve()
        try:
            before = source.stat()
        except OSError as error:
            raise SearchIndexError(f"File cannot be indexed: {error}") from error
        if not source.is_file():
            raise SearchIndexError("Only regular files can be indexed")

        extraction = extract_file_content_result(
            source,
            max_chars=max_chars,
            lowercase=False,
            max_pdf_pages=20,
            max_docx_paragraphs=None,
        )
        content = extraction.text

        try:
            after = source.stat()
        except OSError as error:
            raise SearchIndexError(
                "File disappeared while search content was being extracted"
            ) from error
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise SearchIndexError(
                "File changed while search content was being extracted"
            )

        indexed_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        extraction_fingerprint = current_extraction_fingerprint()
        extraction_status = (
            "indexed"
            if content
            else {
                "ocr_required": "ocr_required",
                "ocr_unavailable": "ocr_unavailable",
                "extraction_failed": "extraction_failed",
                "unsupported_format": "unsupported_format",
            }.get(extraction.status, "metadata_only")
        )
        normalized_category = category.strip() if isinstance(category, str) and category.strip() else None

        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO files (
                        path, filename, extension, size_bytes, modified_ns,
                        category, content, extraction_status, extraction_fingerprint,
                        indexed_at_utc
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        filename = excluded.filename,
                        extension = excluded.extension,
                        size_bytes = excluded.size_bytes,
                        modified_ns = excluded.modified_ns,
                        category = excluded.category,
                        content = excluded.content,
                        extraction_status = excluded.extraction_status,
                        extraction_fingerprint = excluded.extraction_fingerprint,
                        indexed_at_utc = excluded.indexed_at_utc
                    """,
                    (
                        str(source),
                        source.name,
                        source.suffix.lower(),
                        after.st_size,
                        after.st_mtime_ns,
                        normalized_category,
                        content,
                        extraction_status,
                        extraction_fingerprint,
                        indexed_at,
                    ),
                )
                connection.execute(
                    "DELETE FROM file_embeddings WHERE path = ?",
                    (str(source),),
                )
                connection.execute(
                    "DELETE FROM file_embedding_chunks WHERE path = ?",
                    (str(source),),
                )
        except sqlite3.Error as error:
            raise SearchIndexError(f"File indexing failed: {error}") from error

        return IndexedFile(
            path=source,
            filename=source.name,
            extension=source.suffix.lower(),
            size_bytes=after.st_size,
            modified_ns=after.st_mtime_ns,
            category=normalized_category,
            extraction_status=extraction_status,
            extraction_fingerprint=extraction_fingerprint,
            indexed_at_utc=indexed_at,
        )

    def remove_file(self, file_path: str | Path) -> bool:
        source = Path(file_path).resolve()
        try:
            with closing(self._connect()) as connection, connection:
                cursor = connection.execute(
                    "DELETE FROM files WHERE path = ?",
                    (str(source),),
                )
                return cursor.rowcount > 0
        except sqlite3.Error as error:
            raise SearchIndexError(f"Search index removal failed: {error}") from error

    def indexed_paths(self) -> tuple[Path, ...]:
        """Return a snapshot of indexed paths for reconciliation."""
        try:
            with closing(self._connect()) as connection, connection:
                rows = connection.execute(
                    "SELECT path FROM files ORDER BY path"
                ).fetchall()
        except sqlite3.Error as error:
            raise SearchIndexError(f"Search index read failed: {error}") from error
        return tuple(Path(row["path"]) for row in rows)

    def extraction_fingerprint(self) -> str:
        return current_extraction_fingerprint()

    def get_file(self, file_path: str | Path) -> IndexedFile | None:
        source = Path(file_path).resolve()
        try:
            with closing(self._connect()) as connection, connection:
                row = connection.execute(
                    """
                    SELECT path, filename, extension, size_bytes, modified_ns,
                           category, extraction_status, extraction_fingerprint,
                           indexed_at_utc
                    FROM files
                    WHERE path = ?
                    """,
                    (str(source),),
                ).fetchone()
        except sqlite3.Error as error:
            raise SearchIndexError(f"Search index read failed: {error}") from error
        if row is None:
            return None
        return IndexedFile(
            path=Path(row["path"]),
            filename=row["filename"],
            extension=row["extension"],
            size_bytes=row["size_bytes"],
            modified_ns=row["modified_ns"],
            category=row["category"],
            extraction_status=row["extraction_status"],
            extraction_fingerprint=row["extraction_fingerprint"],
            indexed_at_utc=row["indexed_at_utc"],
        )

    def embedding_fingerprint_for_file(self, file_path: str | Path) -> str | None:
        source = Path(file_path).resolve()
        try:
            with closing(self._connect()) as connection, connection:
                row = connection.execute(
                    """
                    SELECT embedding_fingerprint
                    FROM file_embedding_chunks
                    WHERE path = ?
                    ORDER BY chunk_index
                    LIMIT 1
                    """,
                    (str(source),),
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        "SELECT embedding_fingerprint FROM file_embeddings WHERE path = ?",
                        (str(source),),
                    ).fetchone()
        except sqlite3.Error as error:
            raise SearchIndexError(f"Embedding metadata read failed: {error}") from error
        return None if row is None else str(row["embedding_fingerprint"])


    def semantic_document(self, file_path: str | Path, *, max_chars: int = 12_000) -> str | None:
        """Return bounded indexed text for local embedding without re-reading the source file."""
        source = Path(file_path).resolve()
        bounded_chars = max(256, min(int(max_chars), 100_000))
        try:
            with closing(self._connect()) as connection, connection:
                row = connection.execute(
                    "SELECT filename, category, content FROM files WHERE path = ?",
                    (str(source),),
                ).fetchone()
        except sqlite3.Error as error:
            raise SearchIndexError(f"Search index read failed: {error}") from error
        if row is None:
            return None
        parts = [row["filename"]]
        if row["category"]:
            parts.append(str(row["category"]))
        if row["content"]:
            parts.append(str(row["content"]))
        return "\n".join(parts)[:bounded_chars].strip()

    def semantic_document_chunks(
        self,
        file_path: str | Path,
        *,
        max_chunk_chars: int = 2_000,
        overlap_chars: int = 250,
        max_chunks: int = 24,
    ) -> tuple[str, ...]:
        """Split indexed content into bounded overlapping chunks for semantic retrieval."""
        source = Path(file_path).resolve()
        chunk_size = max(512, min(int(max_chunk_chars), 8_000))
        overlap = max(0, min(int(overlap_chars), chunk_size // 2))
        chunk_limit = max(1, min(int(max_chunks), 64))
        try:
            with closing(self._connect()) as connection, connection:
                row = connection.execute(
                    "SELECT filename, category, content FROM files WHERE path = ?",
                    (str(source),),
                ).fetchone()
        except sqlite3.Error as error:
            raise SearchIndexError(f"Search index read failed: {error}") from error
        if row is None:
            return ()

        header_parts = [str(row["filename"])]
        if row["category"]:
            header_parts.append(str(row["category"]))
        header = "\n".join(header_parts).strip()
        content = str(row["content"] or "").strip()
        if not content:
            return (header,) if header else ()

        chunks: list[str] = []
        start = 0
        step = max(1, chunk_size - overlap)
        while start < len(content) and len(chunks) < chunk_limit:
            end = min(len(content), start + chunk_size)
            body = content[start:end].strip()
            if body:
                chunks.append(f"{header}\n{body}".strip())
            if end >= len(content):
                break
            start += step
        return tuple(chunks)

    def upsert_embedding_chunks(
        self,
        file_path: str | Path,
        vectors,
        *,
        provider: str,
        model: str,
        embedding_fingerprint: str,
    ) -> None:
        """Replace all semantic chunks for one indexed file atomically."""
        source = Path(file_path).resolve()
        provider_name = provider.strip()
        model_name = model.strip()
        fingerprint = embedding_fingerprint.strip()
        vector_list = list(vectors)
        if not provider_name or not model_name or not fingerprint:
            raise SearchIndexError("Embedding metadata is incomplete")
        if not vector_list:
            raise SearchIndexError("At least one embedding chunk is required")

        encoded: list[tuple[bytes, int]] = []
        expected_dimensions: int | None = None
        try:
            for vector in vector_list:
                payload, dimensions = encode_vector(vector)
                if expected_dimensions is None:
                    expected_dimensions = dimensions
                elif dimensions != expected_dimensions:
                    raise SemanticSearchError("Embedding chunks have different dimensions")
                encoded.append((payload, dimensions))
        except SemanticSearchError as error:
            raise SearchIndexError(f"Embedding is invalid: {error}") from error

        embedded_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        try:
            with closing(self._connect()) as connection, connection:
                exists = connection.execute(
                    "SELECT 1 FROM files WHERE path = ?",
                    (str(source),),
                ).fetchone()
                if exists is None:
                    raise SearchIndexError("File must be indexed before embedding")
                connection.execute(
                    "DELETE FROM file_embedding_chunks WHERE path = ?",
                    (str(source),),
                )
                connection.execute(
                    "DELETE FROM file_embeddings WHERE path = ?",
                    (str(source),),
                )
                connection.executemany(
                    """
                    INSERT INTO file_embedding_chunks (
                        path, chunk_index, vector, dimensions, provider, model,
                        embedding_fingerprint, embedded_at_utc
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            str(source),
                            index,
                            payload,
                            dimensions,
                            provider_name,
                            model_name,
                            fingerprint,
                            embedded_at,
                        )
                        for index, (payload, dimensions) in enumerate(encoded)
                    ],
                )
        except SearchIndexError:
            raise
        except sqlite3.Error as error:
            raise SearchIndexError(f"Embedding persistence failed: {error}") from error

    def upsert_embedding(
        self,
        file_path: str | Path,
        vector,
        *,
        provider: str,
        model: str,
        embedding_fingerprint: str,
    ) -> None:
        """Persist one normalized local embedding for an already indexed file."""
        source = Path(file_path).resolve()
        provider_name = provider.strip()
        model_name = model.strip()
        fingerprint = embedding_fingerprint.strip()
        if not provider_name or not model_name or not fingerprint:
            raise SearchIndexError("Embedding metadata is incomplete")
        try:
            payload, dimensions = encode_vector(vector)
        except SemanticSearchError as error:
            raise SearchIndexError(f"Embedding is invalid: {error}") from error
        embedded_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        try:
            with closing(self._connect()) as connection, connection:
                exists = connection.execute(
                    "SELECT 1 FROM files WHERE path = ?",
                    (str(source),),
                ).fetchone()
                if exists is None:
                    raise SearchIndexError("File must be indexed before embedding")
                connection.execute(
                    """
                    INSERT INTO file_embeddings (
                        path, vector, dimensions, provider, model,
                        embedding_fingerprint, embedded_at_utc
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        vector = excluded.vector,
                        dimensions = excluded.dimensions,
                        provider = excluded.provider,
                        model = excluded.model,
                        embedding_fingerprint = excluded.embedding_fingerprint,
                        embedded_at_utc = excluded.embedded_at_utc
                    """,
                    (
                        str(source),
                        payload,
                        dimensions,
                        provider_name,
                        model_name,
                        fingerprint,
                        embedded_at,
                    ),
                )
        except SearchIndexError:
            raise
        except sqlite3.Error as error:
            raise SearchIndexError(f"Embedding persistence failed: {error}") from error

    def semantic_search(
        self,
        query_vector,
        *,
        embedding_fingerprint: str,
        limit: int = 25,
    ) -> tuple[SemanticSearchResult, ...]:
        """Return each file's best matching chunk for one exact embedding fingerprint."""
        fingerprint = embedding_fingerprint.strip()
        if not fingerprint:
            return ()
        bounded_limit = max(1, min(int(limit), MAX_SEARCH_RESULTS))
        try:
            normalized_query = normalize_vector(query_vector)
        except SemanticSearchError as error:
            raise SearchIndexError(f"Semantic query is invalid: {error}") from error

        try:
            with closing(self._connect()) as connection, connection:
                chunk_rows = connection.execute(
                    """
                    SELECT
                        files.path,
                        files.filename,
                        files.extension,
                        files.category,
                        file_embedding_chunks.vector,
                        file_embedding_chunks.dimensions
                    FROM file_embedding_chunks
                    JOIN files ON files.path = file_embedding_chunks.path
                    WHERE file_embedding_chunks.embedding_fingerprint = ?
                    """,
                    (fingerprint,),
                ).fetchall()
                legacy_rows = connection.execute(
                    """
                    SELECT
                        files.path,
                        files.filename,
                        files.extension,
                        files.category,
                        file_embeddings.vector,
                        file_embeddings.dimensions
                    FROM file_embeddings
                    JOIN files ON files.path = file_embeddings.path
                    WHERE file_embeddings.embedding_fingerprint = ?
                    """,
                    (fingerprint,),
                ).fetchall()
        except sqlite3.Error as error:
            raise SearchIndexError(f"Semantic search failed: {error}") from error

        best_by_path: dict[str, SemanticSearchResult] = {}
        for row in (*chunk_rows, *legacy_rows):
            try:
                candidate = decode_vector(row["vector"], row["dimensions"])
            except SemanticSearchError:
                continue
            if len(candidate) != len(normalized_query):
                continue
            score = sum(
                left * right
                for left, right in zip(normalized_query, candidate, strict=True)
            )
            result = SemanticSearchResult(
                path=Path(row["path"]),
                filename=row["filename"],
                extension=row["extension"],
                category=row["category"],
                score=max(-1.0, min(1.0, float(score))),
            )
            previous = best_by_path.get(str(result.path))
            if previous is None or result.score > previous.score:
                best_by_path[str(result.path)] = result

        scored = list(best_by_path.values())
        scored.sort(key=lambda item: (-item.score, item.filename.casefold()))
        return tuple(scored[:bounded_limit])

    def search_relaxed(
        self,
        query: str,
        *,
        limit: int = 50,
    ) -> tuple[SearchResult, ...]:
        """Return lexical candidates using OR + safe prefix roots for hybrid ranking."""
        bounded_limit = max(1, min(int(limit), MAX_SEARCH_RESULTS))
        match_query = self._build_relaxed_match_query(query)
        if not match_query:
            return ()

        try:
            with closing(self._connect()) as connection, connection:
                rows = connection.execute(
                    """
                    SELECT
                        files.path,
                        files.filename,
                        files.extension,
                        files.category,
                        snippet(files_fts, 3, '[', ']', ' … ', 18) AS snippet,
                        bm25(files_fts, 4.0, 1.0, 2.0, 1.0) AS rank
                    FROM files_fts
                    JOIN files ON files.rowid = files_fts.rowid
                    WHERE files_fts MATCH ?
                    ORDER BY rank, files.filename COLLATE NOCASE
                    LIMIT ?
                    """,
                    (match_query, bounded_limit),
                ).fetchall()
        except sqlite3.Error as error:
            raise SearchIndexError(f"Search failed: {error}") from error

        return tuple(
            SearchResult(
                path=Path(row["path"]),
                filename=row["filename"],
                extension=row["extension"],
                category=row["category"],
                snippet=row["snippet"] or "",
                rank=float(row["rank"]),
            )
            for row in rows
        )

    def search(self, query: str, *, limit: int = 25) -> tuple[SearchResult, ...]:
        bounded_limit = max(1, min(int(limit), MAX_SEARCH_RESULTS))
        match_query = self._build_match_query(query)
        if not match_query:
            return ()

        try:
            with closing(self._connect()) as connection, connection:
                rows = connection.execute(
                    """
                    SELECT
                        files.path,
                        files.filename,
                        files.extension,
                        files.category,
                        snippet(files_fts, 3, '[', ']', ' … ', 18) AS snippet,
                        bm25(files_fts, 4.0, 1.0, 2.0, 1.0) AS rank
                    FROM files_fts
                    JOIN files ON files.rowid = files_fts.rowid
                    WHERE files_fts MATCH ?
                    ORDER BY rank, files.filename COLLATE NOCASE
                    LIMIT ?
                    """,
                    (match_query, bounded_limit),
                ).fetchall()
        except sqlite3.Error as error:
            raise SearchIndexError(f"Search failed: {error}") from error

        return tuple(
            SearchResult(
                path=Path(row["path"]),
                filename=row["filename"],
                extension=row["extension"],
                category=row["category"],
                snippet=row["snippet"] or "",
                rank=float(row["rank"]),
            )
            for row in rows
        )

    @staticmethod
    def _build_relaxed_match_query(query: str) -> str:
        stop_words = {
            "a", "an", "and", "about", "document", "documents", "file", "files",
            "for", "in", "of", "on", "or", "the", "to", "with",
        }
        terms = [
            token.casefold()
            for token in re.findall(r"\w+", query, flags=re.UNICODE)
            if token
        ]
        roots: list[str] = []
        for term in terms[:20]:
            if term in stop_words or len(term) < 3:
                continue
            root = term
            for suffix in ("ing", "ed", "es", "s"):
                if root.endswith(suffix) and len(root) - len(suffix) >= 4:
                    root = root[:-len(suffix)]
                    break
            if root not in roots:
                roots.append(root)
        if not roots:
            return ""
        return " OR ".join(
            '"' + root.replace('"', '""') + '"*'
            for root in roots
        )

    @staticmethod
    def _build_match_query(query: str) -> str:
        terms = [
            token
            for token in re.findall(r"\w+", query, flags=re.UNICODE)
            if token
        ]
        if not terms:
            return ""
        return " AND ".join(
            '"' + term.replace('"', '""') + '"'
            for term in terms[:20]
        )
