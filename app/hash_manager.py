import json
import hashlib
from pathlib import Path


def ensure_hash_db(hash_db_file: str) -> None:
    """التأكد من وجود ملف قاعدة بيانات الـ hashes."""
    path = Path(hash_db_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        with open(path, "w", encoding="utf-8") as file:
            json.dump({}, file, indent=2, ensure_ascii=False)


def load_hash_db(hash_db_file: str) -> dict:
    """تحميل قاعدة بيانات الـ hashes."""
    ensure_hash_db(hash_db_file)

    with open(hash_db_file, "r", encoding="utf-8") as file:
        return json.load(file)


def save_hash_db(hash_db_file: str, hash_db: dict) -> None:
    """حفظ قاعدة بيانات الـ hashes."""
    with open(hash_db_file, "w", encoding="utf-8") as file:
        json.dump(hash_db, file, indent=2, ensure_ascii=False)


def calculate_file_hash(file_path: Path, chunk_size: int = 65536) -> str:
    """حساب SHA256 للملف."""
    sha256 = hashlib.sha256()

    with open(file_path, "rb") as file:
        while chunk := file.read(chunk_size):
            sha256.update(chunk)

    return sha256.hexdigest()


def is_duplicate_file(file_path: Path, hash_db_file: str) -> tuple[bool, str]:
    """
    فحص هل الملف مكرر أم لا.
    يرجع:
    (True, file_hash) إذا كان مكررًا
    (False, file_hash) إذا لم يكن مكررًا
    """
    file_hash = calculate_file_hash(file_path)
    hash_db = load_hash_db(hash_db_file)

    is_duplicate = file_hash in hash_db
    return is_duplicate, file_hash


def register_file_hash(file_hash: str, stored_path: str, hash_db_file: str) -> None:
    """تسجيل hash الملف بعد نقله بنجاح."""
    hash_db = load_hash_db(hash_db_file)
    hash_db[file_hash] = stored_path
    save_hash_db(hash_db_file, hash_db)


def get_existing_file_path(file_hash: str, hash_db_file: str) -> str | None:
    """إرجاع مسار الملف الموجود إذا كان الـ hash موجودًا."""
    hash_db = load_hash_db(hash_db_file)
    return hash_db.get(file_hash)