import csv
import json
import threading
from datetime import datetime
from pathlib import Path

_stats_lock = threading.Lock()


def build_default_stats(rules: dict) -> dict:
    """Build default stats dynamically from rules."""
    stats = {
        "total_files": 0,
        "failed": 0,
    }
    for category in rules.keys():
        stats[category] = 0
    if "others" not in stats:
        stats["others"] = 0
    return stats


def ensure_stats_file(stats_file: str, rules: dict) -> None:
    path = Path(stats_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        default_stats = build_default_stats(rules)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(default_stats, file, indent=2, ensure_ascii=False)
        return

    # If file exists, ensure it contains all new categories
    with open(path, "r", encoding="utf-8") as file:
        stats = json.load(file)

    updated = False
    for key in build_default_stats(rules):
        if key not in stats:
            stats[key] = 0
            updated = True

    if updated:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(stats, file, indent=2, ensure_ascii=False)


def update_stats(stats_file: str, category: str, rules: dict, success: bool = True) -> None:
    """Update statistics in a thread-safe manner."""
    with _stats_lock:
        ensure_stats_file(stats_file, rules)

        with open(stats_file, "r", encoding="utf-8") as file:
            stats = json.load(file)

        if success:
            stats["total_files"] += 1
            stats[category] = stats.get(category, 0) + 1
        else:
            stats["failed"] += 1

        with open(stats_file, "w", encoding="utf-8") as file:
            json.dump(stats, file, indent=2, ensure_ascii=False)


# ---- History ----
# Header matches append_history_entry in mover.py
_HISTORY_FIELDNAMES = [
    "timestamp",
    "filename",
    "category",
    "status",
    "classification_method",
    "smart_source",
]


def append_history(
    history_file: str,
    filename: str,
    category: str,
    status: str,
    classification_method: str = "extension",
    smart_source: str = "",
) -> None:
    """
    Append a record to the history file in unified format (6 columns).
    """
    history_path = Path(history_file)
    history_path.parent.mkdir(parents=True, exist_ok=True)

    file_exists = history_path.exists() and history_path.stat().st_size > 0

    with open(history_path, "a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=_HISTORY_FIELDNAMES)

        if not file_exists:
            writer.writeheader()

        writer.writerow({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "filename": filename,
            "category": category,
            "status": status,
            "classification_method": classification_method,
            "smart_source": smart_source,
        })
