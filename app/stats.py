import csv
import json
from datetime import datetime
from pathlib import Path


def build_default_stats(rules: dict) -> dict:
    """
    بناء stats افتراضي ديناميكي من القواعد.
    """
    stats = {
        "total_files": 0,
        "failed": 0
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

    # إذا كان الملف موجودًا، نتأكد أنه يحتوي كل الفئات الجديدة
    with open(path, "r", encoding="utf-8") as file:
        stats = json.load(file)

    updated = False
    default_stats = build_default_stats(rules)

    for key in default_stats:
        if key not in stats:
            stats[key] = default_stats[key]
            updated = True

    if updated:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(stats, file, indent=2, ensure_ascii=False)


def ensure_history_file(history_file: str) -> None:
    path = Path(history_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        with open(path, "w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["filename", "category", "status", "timestamp"])


def update_stats(stats_file: str, category: str, rules: dict, success: bool = True) -> None:
    ensure_stats_file(stats_file, rules)

    with open(stats_file, "r", encoding="utf-8") as file:
        stats = json.load(file)

    if success:
        stats["total_files"] += 1

        if category not in stats:
            stats[category] = 0

        stats[category] += 1
    else:
        stats["failed"] += 1

    with open(stats_file, "w", encoding="utf-8") as file:
        json.dump(stats, file, indent=2, ensure_ascii=False)


def append_history(history_file: str, filename: str, category: str, status: str) -> None:
    ensure_history_file(history_file)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(history_file, "a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow([filename, category, status, timestamp])