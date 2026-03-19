import hashlib
import json
import threading
from pathlib import Path

# ---- Module-level cache ----
# Instead of reading hash_db.json from disk on every file,
# keep a copy in memory and write only on changes.

_cache: dict | None = None
_cache_path: str | None = None
_cache_lock = threading.Lock()


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


def is_duplicate_file(file_path: Path, hash_db_file: str) -> tuple[bool, str]:
    """
    Check whether the file is a duplicate.
    Returns (True, file_hash) if duplicate, (False, file_hash) otherwise.
    """
    file_hash = calculate_file_hash(file_path)
    with _cache_lock:
        db = _get_cache(hash_db_file)
        return file_hash in db, file_hash


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
