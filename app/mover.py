import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

from app.classifier import get_file_category
from app.stats import update_stats, append_history
from app.hash_manager import is_duplicate_file, register_file_hash, get_existing_file_path


def generate_unique_destination(destination_path: Path) -> Path:
    """توليد اسم جديد إذا كان الملف موجودًا مسبقًا."""
    if not destination_path.exists():
        return destination_path

    stem = destination_path.stem
    suffix = destination_path.suffix
    parent = destination_path.parent

    counter = 1
    while True:
        new_name = f"{stem}({counter}){suffix}"
        new_path = parent / new_name
        if not new_path.exists():
            return new_path
        counter += 1


def get_dated_destination_dir(base_dir: Path, archive_by_date: bool) -> Path:
    """
    إذا كانت الأرشفة حسب التاريخ مفعلة،
    نضيف مجلد فرعي مثل 2026-03
    """
    if not archive_by_date:
        return base_dir

    date_folder = datetime.now().strftime("%Y-%m")
    return base_dir / date_folder


def move_file_with_retries(
    source_file: Path,
    destination_folders: dict,
    extension_lookup: dict,
    stats_file: str,
    history_file: str,
    hash_db_file: str,
    archive_by_date: bool,
    rules: dict,
    retries: int = 8,
    delay: int = 2
) -> None:
    """محاولة نقل الملف عدة مرات مع فحص التكرار بالـ hash والأرشفة حسب التاريخ."""
    category = get_file_category(source_file, extension_lookup)

    print(f"[DEBUG] File name: {source_file.name}")
    print(f"[DEBUG] Suffix read: {repr(source_file.suffix.lower().strip())}")
    print(f"[DEBUG] Category chosen: {category}")

    if category not in destination_folders:
        category = "others"

    if not source_file.exists():
        print(f"[DEBUG] File no longer exists before hashing: {source_file}")
        logging.warning(f"File no longer exists before hashing: {source_file}")
        append_history(history_file, source_file.name, category, "disappeared")
        return

    try:
        duplicate, file_hash = is_duplicate_file(source_file, hash_db_file)

        if duplicate:
            existing_path = get_existing_file_path(file_hash, hash_db_file)
            logging.info(
                f"Duplicate file skipped: {source_file.name} | category: {category} | existing: {existing_path}"
            )
            append_history(history_file, source_file.name, category, "duplicate_skipped")
            print(f"[DEBUG] Duplicate detected. Existing file: {existing_path}")

            source_file.unlink(missing_ok=True)
            return

    except Exception as error:
        logging.error(f"Hash check failed for {source_file.name}: {error}")
        append_history(history_file, source_file.name, category, "hash_check_failed")
        print(f"[DEBUG] Hash check failed: {error}")
        return

    base_destination_dir = Path(destination_folders[category])
    destination_dir = get_dated_destination_dir(base_destination_dir, archive_by_date)
    destination_dir.mkdir(parents=True, exist_ok=True)

    destination_file = destination_dir / source_file.name
    final_destination = generate_unique_destination(destination_file)

    last_error = None

    for attempt in range(1, retries + 1):
        try:
            if not source_file.exists():
                print(f"[DEBUG] File no longer exists: {source_file}")
                logging.warning(f"File no longer exists: {source_file}")
                append_history(history_file, source_file.name, category, "disappeared")
                return

            shutil.move(str(source_file), str(final_destination))

            register_file_hash(file_hash, str(final_destination), hash_db_file)

            logging.info(
                f"Moved file: {source_file.name} | category: {category} | to: {final_destination}"
            )
            update_stats(stats_file, category, rules, success=True)
            append_history(history_file, source_file.name, category, "success")
            print(f"[DEBUG] Moved to: {final_destination}")
            return

        except PermissionError as error:
            last_error = error
            print(f"[DEBUG] Attempt {attempt}: PermissionError -> {source_file.name}: {error}")
            time.sleep(delay)

        except OSError as error:
            last_error = error
            print(f"[DEBUG] Attempt {attempt}: OSError -> {source_file.name}: {error}")
            time.sleep(delay)

        except Exception as error:
            last_error = error
            print(f"[DEBUG] Attempt {attempt}: Unexpected error -> {source_file.name}: {error}")
            time.sleep(delay)

    logging.error(f"Failed to move file after retries: {source_file.name} | error: {last_error}")
    update_stats(stats_file, category, rules, success=False)
    append_history(history_file, source_file.name, category, "failed")
    print(f"[DEBUG] Failed to move after retries: {source_file.name}")