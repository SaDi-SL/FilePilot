import json
import shutil
import sys
from pathlib import Path

def get_runtime_base_dir() -> Path:
    """
    Return the base directory at runtime:
    - In development mode: project root
    - In exe mode: directory of the exe
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent.parent


def get_bundle_base_dir() -> Path:
    """
    Return PyInstaller internal files directory when built,
    or project root during development.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)

    return Path(__file__).resolve().parent.parent


def ensure_external_config_exists() -> Path:
    """
    Ensure config/config.json exists next to the app.
    If missing, copy it from the bundled version.
    """
    runtime_base = get_runtime_base_dir()
    bundle_base = get_bundle_base_dir()

    external_config_dir = runtime_base / "config"
    external_config_file = external_config_dir / "config.json"

    bundled_config_file = bundle_base / "config" / "config.json"

    external_config_dir.mkdir(parents=True, exist_ok=True)

    if not external_config_file.exists():
        if bundled_config_file.exists():
            shutil.copy2(bundled_config_file, external_config_file)
        else:
            raise FileNotFoundError(
                f"Bundled config file not found: {bundled_config_file}"
            )

    return external_config_file


def load_config() -> dict:
    config_file = ensure_external_config_exists()

    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    with open(config_file, "r", encoding="utf-8") as file:
        return json.load(file)


def get_config_path() -> Path:
    """
    Return the external config path currently in use.
    """
    return ensure_external_config_exists()

def resolve_runtime_path(relative_path: str) -> Path:
    """
    Convert any relative path to an absolute path next to the app.
    """
    base_dir = get_runtime_base_dir()
    return (base_dir / relative_path).resolve()

def get_plugins_dir() -> Path:
    """
    Return the plugins directory next to the app.
    """
    return resolve_runtime_path("plugins")

def get_smart_rules_path() -> Path:
    """
    Return the smart_rules.json path next to the app.
    """
    return resolve_runtime_path("config/smart_rules.json")

def get_notifications_path() -> Path:
    """
    Return the notifications file path next to the app.
    """
    return resolve_runtime_path("reports/notifications.json")