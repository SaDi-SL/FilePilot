from pathlib import Path

from app.classifier import build_extension_lookup
from app.config_loader import load_config, resolve_runtime_path, get_plugins_dir
from app.plugin_manager import PluginManager
from app.hash_manager import ensure_hash_db
from app.logger_setup import setup_logging
from app.stats import ensure_stats_file
from app.multi_watcher import MultiFolderMonitor


def build_destination_folders(base_folder: str, rules: dict) -> dict:
    """Build destination folder paths from rules."""
    destination_folders = {}
    for category in rules.keys():
        destination_folders[category] = f"{base_folder}/{category}"
    if "others" not in destination_folders:
        destination_folders["others"] = f"{base_folder}/others"
    return destination_folders


def ensure_directories(config: dict) -> None:
    """Create all required directories if they don't exist."""
    # Watch folders
    for folder in config.get("watch_folders", []):
        Path(folder["path"]).mkdir(parents=True, exist_ok=True)

    # Legacy single source folder (backwards compat)
    if "source_folder" in config:
        Path(config["source_folder"]).mkdir(parents=True, exist_ok=True)

    for folder in config["destination_folders"].values():
        Path(folder).mkdir(parents=True, exist_ok=True)

    Path(config["log_file"]).parent.mkdir(parents=True, exist_ok=True)
    Path(config["stats_file"]).parent.mkdir(parents=True, exist_ok=True)
    Path(config["history_file"]).parent.mkdir(parents=True, exist_ok=True)
    Path(config["hash_db_file"]).parent.mkdir(parents=True, exist_ok=True)


def ensure_history_file(history_file: str) -> None:
    """Create history CSV if it doesn't exist."""
    history_path = Path(history_file)
    if not history_path.exists():
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text(
            "timestamp,filename,category,status,classification_method,smart_source\n",
            encoding="utf-8",
        )


def build_monitor():
    """
    Load config, build all infrastructure, and return
    (config, MultiFolderMonitor).

    Backwards compatible: if config has legacy source_folder,
    MultiFolderMonitor.migrate_legacy_config() handles the conversion.
    """
    config = load_config()

    base_folder = config.get("organized_base_folder", "organized")
    rules = config.get("rules", {})

    config["destination_folders"] = build_destination_folders(base_folder, rules)

    # Resolve all paths to absolute
    config["organized_base_folder"] = str(resolve_runtime_path(
        config.get("organized_base_folder", "organized")
    ))

    # Resolve legacy source_folder if present
    if "source_folder" in config:
        config["source_folder"] = str(resolve_runtime_path(config["source_folder"]))

    # Resolve watch_folders paths
    resolved_folders = []
    for folder in config.get("watch_folders", []):
        resolved = dict(folder)
        resolved["path"] = str(resolve_runtime_path(folder["path"]))
        resolved_folders.append(resolved)
    if resolved_folders:
        config["watch_folders"] = resolved_folders

    config["destination_folders"] = {
        category: str(resolve_runtime_path(path))
        for category, path in config["destination_folders"].items()
    }

    config["log_file"]      = str(resolve_runtime_path(config["log_file"]))
    config["stats_file"]    = str(resolve_runtime_path(config["stats_file"]))
    config["history_file"]  = str(resolve_runtime_path(config["history_file"]))
    config["hash_db_file"]  = str(resolve_runtime_path(config["hash_db_file"]))

    ensure_directories(config)
    setup_logging(config["log_file"])
    ensure_stats_file(config["stats_file"], rules)
    ensure_history_file(config["history_file"])
    ensure_hash_db(config["hash_db_file"])

    extension_lookup = build_extension_lookup(rules)
    plugins_dir = get_plugins_dir()
    plugin_manager = PluginManager(plugins_dir)
    plugin_manager.load_plugins()

    config["plugins_dir"]     = str(plugins_dir)
    config["loaded_plugins"]  = plugin_manager.get_plugins_info()
    config["failed_plugins"]  = plugin_manager.get_failed_plugins()

    monitor = MultiFolderMonitor(
        config=config,
        extension_lookup=extension_lookup,
        plugin_manager=plugin_manager,
    )

    return config, monitor
