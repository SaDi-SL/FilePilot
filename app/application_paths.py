from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.product_identity import PRODUCT_IDENTITY


class ApplicationPathError(RuntimeError):
    """Raised when FilePilot cannot establish a safe writable location."""


@dataclass(frozen=True)
class ApplicationPaths:
    """Authoritative read-only and writable roots for one FilePilot process."""

    installed: bool
    install_root: Path
    resource_root: Path
    user_data_root: Path

    @property
    def config_dir(self) -> Path:
        return self.user_data_root / "config"

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.json"

    @property
    def data_dir(self) -> Path:
        return self.user_data_root / "data"

    @property
    def journal_file(self) -> Path:
        return self.data_dir / "operations.sqlite3"

    @property
    def search_index_file(self) -> Path:
        return self.data_dir / "search.sqlite3"

    @property
    def logs_dir(self) -> Path:
        return self.user_data_root / "logs"

    @property
    def reports_dir(self) -> Path:
        return self.user_data_root / "reports"

    @property
    def backups_dir(self) -> Path:
        return self.user_data_root / "backups"

    @property
    def plugins_dir(self) -> Path:
        return self.user_data_root / "plugins"

    @property
    def reminders_dir(self) -> Path:
        return self.user_data_root / "reminders"

    @property
    def default_config_file(self) -> Path:
        return self.resource_root / "config" / "default_config.json"

    def resource(self, *parts: str) -> Path:
        return self.resource_root.joinpath(*parts)


def resolve_application_paths(
    *,
    frozen: bool | None = None,
    executable: str | Path | None = None,
    bundle_root: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
    source_root: str | Path | None = None,
) -> ApplicationPaths:
    """Resolve paths without consulting the process current working directory."""
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    project_root = (
        Path(source_root).resolve()
        if source_root is not None
        else Path(__file__).resolve().parent.parent
    )

    if not is_frozen:
        return ApplicationPaths(
            installed=False,
            install_root=project_root,
            resource_root=project_root,
            user_data_root=project_root,
        )

    executable_path = Path(executable or sys.executable).resolve()
    install_root = executable_path.parent
    if bundle_root is not None:
        resource_root = Path(bundle_root).resolve()
    elif hasattr(sys, "_MEIPASS"):
        resource_root = Path(sys._MEIPASS).resolve()
    else:
        resource_root = install_root

    env = os.environ if environment is None else environment
    local_app_data = env.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise ApplicationPathError(
            "LOCALAPPDATA is unavailable; FilePilot will not write beside its executable"
        )
    local_root = Path(local_app_data).expanduser()
    if not local_root.is_absolute():
        raise ApplicationPathError("LOCALAPPDATA must be an absolute path")

    return ApplicationPaths(
        installed=True,
        install_root=install_root,
        resource_root=resource_root,
        user_data_root=local_root / PRODUCT_IDENTITY.product_name,
    )


def get_application_paths() -> ApplicationPaths:
    return resolve_application_paths()
