"""Persistent library roots and indexing diagnostics; never changes source files."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class IndexCancelled(Exception):
    pass


class IndexControl:
    def __init__(self):
        self.cancelled = threading.Event()
        self.paused = threading.Event()

    def checkpoint(self):
        while self.paused.is_set() and not self.cancelled.wait(0.1):
            pass
        if self.cancelled.is_set():
            raise IndexCancelled()


@dataclass(frozen=True)
class IndexProgress:
    stage: str
    completed: int
    total: int | None
    filename: str = ""


@dataclass(frozen=True)
class LibraryFile:
    path: Path
    status: str
    indexed_at: str
    semantic: bool
    issue: str = ""


@dataclass(frozen=True)
class LibrarySnapshot:
    roots: tuple[Path, ...]
    total: int
    searchable: int
    semantic: int
    files: tuple[LibraryFile, ...]
    last_run: dict
    truncated: bool = False


class LibraryCatalog:
    def __init__(self, index):
        self.index = index
        self.database_path = index.database_path
        with closing(self._connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS library_roots(path TEXT PRIMARY KEY NOT NULL);
                CREATE TABLE IF NOT EXISTS library_issues(path TEXT NOT NULL, stage TEXT NOT NULL,
                    message TEXT NOT NULL, PRIMARY KEY(path, stage));
                CREATE TABLE IF NOT EXISTS library_state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def roots(self):
        with closing(self._connect()) as db:
            return tuple(Path(r[0]) for r in db.execute("SELECT path FROM library_roots ORDER BY path"))

    def add_root(self, path):
        candidate = Path(path).expanduser()
        if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_dir():
            raise ValueError("Choose an existing, local folder to add to the library.")
        resolved = candidate.resolve()
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO library_roots VALUES (?)", (str(resolved),))
        return resolved

    def remove_root(self, path):
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM library_roots WHERE path = ?", (str(Path(path).resolve()),))

    def issue(self, path, stage, message=""):
        with closing(self._connect()) as db, db:
            if message:
                db.execute("INSERT OR REPLACE INTO library_issues VALUES (?,?,?)",
                           (str(path), stage, str(message)[:1000]))
            else:
                db.execute("DELETE FROM library_issues WHERE path=? AND stage=?", (str(path), stage))

    def prune_outside_roots(self, roots):
        with closing(self._connect()) as db, db:
            rows = db.execute("SELECT path, stage FROM library_issues").fetchall()
            for row in rows:
                path = Path(row["path"])
                if not any(path == root or path.is_relative_to(root) for root in roots):
                    db.execute("DELETE FROM library_issues WHERE path=? AND stage=?", (row["path"], row["stage"]))

    def finish(self, result):
        data = {"finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "scanned": result.scanned, "indexed": result.indexed,
                "failed": result.failed + result.semantic_failed, "cancelled": result.cancelled,
                "semantic_available": result.semantic_available}
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO library_state VALUES ('last_run', ?)", (json.dumps(data),))


    def snapshot(self, limit=500):
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            total = db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            searchable = db.execute("SELECT COUNT(*) FROM files WHERE extraction_status='indexed'").fetchone()[0]
            semantic = db.execute("SELECT COUNT(DISTINCT path) FROM file_embedding_chunks").fetchone()[0]
            rows = db.execute('''SELECT f.path, f.extraction_status, f.indexed_at_utc, f.size_bytes, f.modified_ns,
                EXISTS(SELECT 1 FROM file_embedding_chunks e WHERE e.path=f.path) AS semantic,
                (SELECT GROUP_CONCAT(message, '; ') FROM library_issues i WHERE i.path=f.path) AS issue
                FROM files f ORDER BY f.path LIMIT ?''', (max(1, min(int(limit), 2000)),)).fetchall()
            state = db.execute("SELECT value FROM library_state WHERE key='last_run'").fetchone()
            missing_issues = db.execute("SELECT path, message FROM library_issues WHERE path NOT IN (SELECT path FROM files) LIMIT 100").fetchall()
        files = []
        for r in rows:
            path = Path(r['path'])
            status, issue = r['extraction_status'], r['issue'] or ""
            try:
                stat = path.stat()
                if stat.st_size != r["size_bytes"] or stat.st_mtime_ns != r["modified_ns"]:
                    status, issue = "stale", "File changed since indexing. Update the library."
            except OSError:
                status, issue = "unavailable", "File is not accessible. Check its folder, then update."
            files.append(LibraryFile(path, status, r['indexed_at_utc'], bool(r['semantic']), issue))
        files.extend(LibraryFile(Path(r['path']), 'failed', '', False, r['message']) for r in missing_issues)
        return LibrarySnapshot(self.roots(), total, searchable, semantic, tuple(files),
                               json.loads(state[0]) if state else {}, total > len(rows))
