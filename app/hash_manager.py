import hashlib
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# ---- Module-level cache ----
# Instead of reading hash_db.json from disk on every file,
# keep a copy in memory and write only on changes.

_cache: dict | None = None
_cache_path: str | None = None
_cache_lock = threading.Lock()
_hash_operation_locks: dict[str, tuple[threading.Lock, int]] = {}
_hash_operation_locks_guard = threading.Lock()


def ensure_hash_db(hash_db_file: str) -> None:
    """Ensure the hash database file exists."""
    path = Path(hash_db_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        with open(path, "w", encoding="utf-8") as file:
            json.dump({}, file, indent=2, ensure_ascii=False)


def _load_into_cache(hash_db_file: str) -> dict:
    """Load DB from disk into cache (internal, called within lock)."""
    global _cache, _cache_path
    ensure_hash_db(hash_db_file)
    with open(hash_db_file, "r", encoding="utf-8") as file:
        _cache = json.load(file)
    _cache_path = hash_db_file
    return _cache


def _get_cache(hash_db_file: str) -> dict:
    """Return cache, loading from disk if not present (internal, called within lock)."""
    global _cache, _cache_path
    if _cache is None or _cache_path != hash_db_file:
        _load_into_cache(hash_db_file)
    return _cache  # type: ignore[return-value]


def _flush_to_disk(hash_db_file: str) -> None:
    """Write cache to disk (internal, called within lock)."""
    if _cache is not None:
        with open(hash_db_file, "w", encoding="utf-8") as file:
            json.dump(_cache, file, indent=2, ensure_ascii=False)


# ---- Public API (same interface as before) ----

def load_hash_db(hash_db_file: str) -> dict:
    """Load the hash database (returns a read copy)."""
    with _cache_lock:
        return dict(_get_cache(hash_db_file))


def save_hash_db(hash_db_file: str, hash_db: dict) -> None:
    """Save the hash database."""
    global _cache
    with _cache_lock:
        _cache = hash_db
        _flush_to_disk(hash_db_file)


def calculate_file_hash(file_path: Path, chunk_size: int = 65536) -> str:
    """Calculate SHA256 hash for the file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as file:
        while chunk := file.read(chunk_size):
            sha256.update(chunk)
    return sha256.hexdigest()


@contextmanager
def hash_operation(file_hash: str) -> Iterator[None]:
    """Serialize duplicate decisions and commits for one hash in this process."""
    with _hash_operation_locks_guard:
        entry = _hash_operation_locks.get(file_hash)
        if entry is None:
            lock = threading.Lock()
            users = 0
        else:
            lock, users = entry
        _hash_operation_locks[file_hash] = (lock, users + 1)

    try:
        with lock:
            yield
    finally:
        with _hash_operation_locks_guard:
            current_lock, users = _hash_operation_locks[file_hash]
            if users == 1:
                del _hash_operation_locks[file_hash]
            else:
                _hash_operation_locks[file_hash] = (current_lock, users - 1)


def _remove_stale_hash(file_hash: str, indexed_path: object, hash_db_file: str) -> None:
    """Remove an index entry only if it still points to the path we checked."""
    with _cache_lock:
        db = _get_cache(hash_db_file)
        if db.get(file_hash) == indexed_path:
            del db[file_hash]
            _flush_to_disk(hash_db_file)


def get_verified_file_path(file_hash: str, hash_db_file: str) -> str | None:
    """Return the indexed path only when it is still a readable matching file."""
    with _cache_lock:
        indexed_path = _get_cache(hash_db_file).get(file_hash)

    if indexed_path is None:
        return None

    try:
        path = Path(indexed_path)
        if not path.is_file() or calculate_file_hash(path) != file_hash:
            _remove_stale_hash(file_hash, indexed_path, hash_db_file)
            return None
    except Exception:
        # An unverifiable old destination cannot prove that the incoming file is a duplicate.
        _remove_stale_hash(file_hash, indexed_path, hash_db_file)
        return None

    return str(path)


def peek_verified_file_path(
    file_hash: str,
    hash_db_file: str,
) -> tuple[str | None, str | None]:
    """Read a verified index entry without creating or changing the hash database."""
    path = Path(hash_db_file)
    if not path.is_file():
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as file:
            indexed_path = json.load(file).get(file_hash)
    except Exception as error:
        return None, f"Hash index could not be read: {error}"
    if indexed_path is None:
        return None, None
    try:
        candidate = Path(indexed_path)
        if not candidate.is_file():
            return None, None
        if calculate_file_hash(candidate) != file_hash:
            return None, None
    except Exception as error:
        return None, f"Indexed duplicate evidence could not be verified: {error}"
    return str(candidate), None


def is_duplicate_file(file_path: Path, hash_db_file: str) -> tuple[bool, str]:
    """
    Check whether the file is a duplicate.
    Returns (True, file_hash) if duplicate, (False, file_hash) otherwise.
    """
    file_hash = calculate_file_hash(file_path)
    return get_verified_file_path(file_hash, hash_db_file) is not None, file_hash


def register_file_hash(file_hash: str, stored_path: str, hash_db_file: str) -> None:
    """Register the file hash after successful move."""
    with _cache_lock:
        db = _get_cache(hash_db_file)
        db[file_hash] = stored_path
        _flush_to_disk(hash_db_file)


def get_existing_file_path(file_hash: str, hash_db_file: str) -> str | None:
    """Return the existing file path if the hash exists."""
    with _cache_lock:
        return _get_cache(hash_db_file).get(file_hash)
