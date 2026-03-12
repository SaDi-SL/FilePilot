from pathlib import Path

from app.classifier import build_extension_lookup
from app.config_loader import load_config, resolve_runtime_path
from app.hash_manager import ensure_hash_db
from app.logger_setup import setup_logging
from app.stats import ensure_stats_file
from app.watcher import FileMonitor


def build_destination_folders(base_folder: str, rules: dict) -> dict:
    """
    بناء مجلدات الوجهة اعتمادًا على قواعد التصنيف.
    """
    destination_folders = {}

    for category in rules.keys():
        destination_folders[category] = f"{base_folder}/{category}"

    if "others" not in destination_folders:
        destination_folders["others"] = f"{base_folder}/others"

    return destination_folders


def ensure_directories(config: dict) -> None:
    """
    التأكد من وجود كل المجلدات المطلوبة.
    """
    Path(config["source_folder"]).mkdir(parents=True, exist_ok=True)

    for folder in config["destination_folders"].values():
        Path(folder).mkdir(parents=True, exist_ok=True)

    Path(config["log_file"]).parent.mkdir(parents=True, exist_ok=True)
    Path(config["stats_file"]).parent.mkdir(parents=True, exist_ok=True)
    Path(config["history_file"]).parent.mkdir(parents=True, exist_ok=True)
    Path(config["hash_db_file"]).parent.mkdir(parents=True, exist_ok=True)


def ensure_history_file(history_file: str) -> None:
    """
    إنشاء ملف history إذا لم يكن موجودًا.
    """
    history_path = Path(history_file)

    if not history_path.exists():
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text(
            "timestamp,filename,category,status\n",
            encoding="utf-8"
        )


def build_monitor():
    config = load_config()

    base_folder = config.get("organized_base_folder", "organized")
    rules = config.get("rules", {})

    config["destination_folders"] = build_destination_folders(base_folder, rules)

    # تحويل كل المسارات إلى مسارات فعلية بجانب التطبيق
    config["source_folder"] = str(resolve_runtime_path(config["source_folder"]))
    config["organized_base_folder"] = str(resolve_runtime_path(config["organized_base_folder"]))

    config["destination_folders"] = {
        category: str(resolve_runtime_path(path))
        for category, path in config["destination_folders"].items()
    }

    config["log_file"] = str(resolve_runtime_path(config["log_file"]))
    config["stats_file"] = str(resolve_runtime_path(config["stats_file"]))
    config["history_file"] = str(resolve_runtime_path(config["history_file"]))
    config["hash_db_file"] = str(resolve_runtime_path(config["hash_db_file"]))

    ensure_directories(config)

    setup_logging(config["log_file"])
    ensure_stats_file(config["stats_file"], rules)
    ensure_history_file(config["history_file"])
    ensure_hash_db(config["hash_db_file"])

    extension_lookup = build_extension_lookup(rules)
    monitor = FileMonitor(config, extension_lookup)

    return config, monitor