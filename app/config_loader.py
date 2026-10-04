import json
import os
import tempfile
from pathlib import Path

from app.application_paths import get_application_paths

def get_runtime_base_dir() -> Path:
    """
    Return the writable runtime root.

    Source mode retains the repository-root behavior. Installed mode uses the
    current user's LocalAppData directory and never writes beside the EXE.
    """
    return get_application_paths().user_data_root


def get_bundle_base_dir() -> Path:
    """
    Return PyInstaller internal files directory when built,
    or project root during development.
    """
    return get_application_paths().resource_root


def get_resource_path(*parts: str) -> Path:
    """Return a read-only application resource path."""
    return get_application_paths().resource(*parts)


def get_external_config_path() -> Path:
    """Return the runtime config path without creating or copying anything."""
    return get_application_paths().config_file


def ensure_external_config_exists() -> Path:
    """
    Seed a missing user config from the sanitized bundled defaults.

    The exclusive create guarantees that an existing user configuration is
    never overwritten during first run or an upgrade.
    """
    bundle_base = get_bundle_base_dir()
    external_config_file = get_external_config_path()
    external_config_dir = external_config_file.parent

    bundled_config_file = bundle_base / "config" / "default_config.json"

    external_config_dir.mkdir(parents=True, exist_ok=True)

    if not external_config_file.exists():
        if bundled_config_file.exists():
            temporary_name = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=".config-",
                    suffix=".tmp",
                    dir=external_config_dir,
                    delete=False,
                ) as destination:
                    temporary_name = destination.name
                    destination.write(bundled_config_file.read_bytes())
                    destination.flush()
                    os.fsync(destination.fileno())
                os.link(temporary_name, external_config_file)
            except FileExistsError:
                pass
            finally:
                if temporary_name is not None:
                    Path(temporary_name).unlink(missing_ok=True)
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
