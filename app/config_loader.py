import json
import shutil
import sys
from pathlib import Path


def get_runtime_base_dir() -> Path:
    """
    يرجع المجلد الأساسي أثناء التشغيل:
    - في وضع التطوير: جذر المشروع
    - في وضع exe: مجلد exe نفسه
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent.parent


def get_bundle_base_dir() -> Path:
    """
    يرجع مجلد ملفات PyInstaller الداخلية عند البناء،
    أو جذر المشروع أثناء التطوير.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)

    return Path(__file__).resolve().parent.parent


def ensure_external_config_exists() -> Path:
    """
    يضمن وجود config/config.json بجانب التطبيق.
    إذا لم يكن موجودًا، يحاول نسخه من النسخة المضمّنة.
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
    يرجع مسار config الخارجي المستخدم فعليًا.
    """
    return ensure_external_config_exists()

def resolve_runtime_path(relative_path: str) -> Path:
    """
    يحول أي مسار نسبي إلى مسار فعلي بجانب التطبيق.
    """
    base_dir = get_runtime_base_dir()
    return (base_dir / relative_path).resolve()