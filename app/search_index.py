from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.application_paths import get_application_paths
from app.content_reader import extract_file_content


SEARCH_APPLICATION_ID = 0x46505349  # "FPSI"
SEARCH_SCHEMA_VERSION = 1
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
    indexed_at_utc: str


@dataclass(frozen=True)
class SearchResult:
    path: Path
    filename: str
    extension: str
    category: str | None
    snippet: str
    rank: float


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

        content = extract_file_content(
            source,
            max_chars=max_chars,
            lowercase=False,
            max_pdf_pages=20,
            max_docx_paragraphs=None,
        )

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
        extraction_status = "indexed" if content else "metadata_only"
        normalized_category = category.strip() if isinstance(category, str) and category.strip() else None

        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO files (
                        path, filename, extension, size_bytes, modified_ns,
                        category, content, extraction_status, indexed_at_utc
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        filename = excluded.filename,
                        extension = excluded.extension,
                        size_bytes = excluded.size_bytes,
                        modified_ns = excluded.modified_ns,
                        category = excluded.category,
                        content = excluded.content,
                        extraction_status = excluded.extraction_status,
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
                        indexed_at,
                    ),
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

    def get_file(self, file_path: str | Path) -> IndexedFile | None:
        source = Path(file_path).resolve()
        try:
            with closing(self._connect()) as connection, connection:
                row = connection.execute(
                    """
                    SELECT path, filename, extension, size_bytes, modified_ns,
                           category, extraction_status, indexed_at_utc
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
            indexed_at_utc=row["indexed_at_utc"],
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
