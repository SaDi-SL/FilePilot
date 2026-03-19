"""
plugin_marketplace.py — Plugin Marketplace engine for FilePilot.

Fetches the plugin registry from GitHub, checks installed plugins,
downloads and installs new plugins, updates existing ones, and removes them.

Registry URL:
    https://raw.githubusercontent.com/SaDi-SL/SaDi-SL-plugins/main/registry.json
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
from pathlib import Path
from typing import Callable
from urllib import request, error as url_error

logger = logging.getLogger(__name__)

REGISTRY_URL = (
    "https://raw.githubusercontent.com/SaDi-SL/SaDi-SL-plugins/main/registry.json"
)
FETCH_TIMEOUT = 10   # seconds


# ── Status constants ──────────────────────────────────────────────────────────
STATUS_INSTALLED   = "installed"
STATUS_UPDATABLE   = "update available"
STATUS_NOT_INSTALLED = "not installed"


# ── Result dataclass ──────────────────────────────────────────────────────────

class MarketplaceResult:
    """Returned by install/update/remove operations."""
    def __init__(self, ok: bool, message: str, plugin_id: str = "") -> None:
        self.ok        = ok
        self.message   = message
        self.plugin_id = plugin_id

    def __repr__(self) -> str:
        return f"MarketplaceResult(ok={self.ok}, message={self.message!r})"


# ── Main class ────────────────────────────────────────────────────────────────

class PluginMarketplace:
    """
    Manages the plugin registry and installation lifecycle.

    All network operations run on background threads.
    Results are delivered via callbacks to stay UI-responsive.
    """

    def __init__(self, plugins_dir: Path) -> None:
        self.plugins_dir = Path(plugins_dir)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self._registry: list[dict] = []
        self._registry_loaded = False

    # ── Registry ──────────────────────────────────────────────────────────────

    def fetch_registry(
        self,
        on_done: Callable[[list[dict], str | None], None],
    ) -> None:
        """
        Fetch the plugin registry from GitHub in a background thread.
        Calls on_done(plugins_list, error_message) when complete.
        error_message is None on success.
        """
        def _fetch():
            try:
                req = request.Request(
                    REGISTRY_URL,
                    headers={"User-Agent": "FilePilot/1.0"},
                )
                with request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self._registry = data
                self._registry_loaded = True
                logger.info(f"Registry loaded: {len(data)} plugins")
                on_done(data, None)
            except url_error.URLError as e:
                msg = f"Network error: {e.reason}"
                logger.warning(msg)
                on_done([], msg)
            except Exception as e:
                msg = f"Failed to load registry: {e}"
                logger.error(msg)
                on_done([], msg)

        threading.Thread(target=_fetch, daemon=True).start()

    def get_cached_registry(self) -> list[dict]:
        """Return the last fetched registry (may be empty if never fetched)."""
        return self._registry

    # ── Status ────────────────────────────────────────────────────────────────

    def get_plugin_status(self, plugin: dict) -> str:
        """
        Return STATUS_INSTALLED, STATUS_UPDATABLE, or STATUS_NOT_INSTALLED
        for a registry entry.
        """
        plugin_file = self.plugins_dir / f"{plugin['id']}.py"
        meta_file   = self.plugins_dir / f"{plugin['id']}.meta.json"

        if not plugin_file.exists():
            return STATUS_NOT_INSTALLED

        # Check version
        if meta_file.exists():
            try:
                with open(meta_file, encoding="utf-8") as f:
                    local_meta = json.load(f)
                local_ver  = local_meta.get("version", "0.0.0")
                remote_ver = plugin.get("version", "0.0.0")
                if self._version_gt(remote_ver, local_ver):
                    return STATUS_UPDATABLE
            except Exception:
                pass

        return STATUS_INSTALLED

    def list_installed(self) -> list[dict]:
        """Return list of installed plugin IDs with their local metadata."""
        installed = []
        for meta_file in self.plugins_dir.glob("*.meta.json"):
            try:
                with open(meta_file, encoding="utf-8") as f:
                    meta = json.load(f)
                plugin_id = meta_file.stem.replace(".meta", "")
                meta["id"] = plugin_id
                installed.append(meta)
            except Exception:
                pass
        return installed

    # ── Install / Update / Remove ─────────────────────────────────────────────

    def install(
        self,
        plugin: dict,
        on_done: Callable[[MarketplaceResult], None],
    ) -> None:
        """Download and install a plugin. Calls on_done when complete."""
        threading.Thread(
            target=self._do_install,
            args=(plugin, on_done),
            daemon=True,
        ).start()

    def update(
        self,
        plugin: dict,
        on_done: Callable[[MarketplaceResult], None],
    ) -> None:
        """Update an installed plugin to the latest version."""
        # Update = re-install (same flow, overwrites files)
        self.install(plugin, on_done)

    def remove(self, plugin_id: str) -> MarketplaceResult:
        """
        Remove an installed plugin (synchronous — fast, just deletes files).
        Returns MarketplaceResult immediately.
        """
        plugin_file = self.plugins_dir / f"{plugin_id}.py"
        meta_file   = self.plugins_dir / f"{plugin_id}.meta.json"

        removed = False
        for f in [plugin_file, meta_file]:
            if f.exists():
                f.unlink()
                removed = True

        if removed:
            logger.info(f"Plugin removed: {plugin_id}")
            return MarketplaceResult(True, f"Plugin '{plugin_id}' removed.", plugin_id)
        else:
            return MarketplaceResult(False, f"Plugin '{plugin_id}' not found.", plugin_id)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _do_install(
        self,
        plugin: dict,
        on_done: Callable[[MarketplaceResult], None],
    ) -> None:
        plugin_id    = plugin["id"]
        download_url = plugin.get("download_url", "")

        if not download_url:
            on_done(MarketplaceResult(False, "No download URL.", plugin_id))
            return

        try:
            # Download plugin.py
            plugin_code = self._download_text(download_url)
            plugin_file = self.plugins_dir / f"{plugin_id}.py"
            plugin_file.write_text(plugin_code, encoding="utf-8")

            # Write local meta
            meta = {
                "id":          plugin_id,
                "name":        plugin.get("name", plugin_id),
                "version":     plugin.get("version", "0.0.0"),
                "author":      plugin.get("author", ""),
                "description": plugin.get("description", ""),
                "tags":        plugin.get("tags", []),
                "homepage":    plugin.get("homepage", ""),
            }
            meta_file = self.plugins_dir / f"{plugin_id}.meta.json"
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)

            logger.info(f"Plugin installed: {plugin_id} v{meta['version']}")
            on_done(MarketplaceResult(
                True,
                f"'{meta['name']}' v{meta['version']} installed successfully.",
                plugin_id,
            ))

        except url_error.URLError as e:
            msg = f"Download failed: {e.reason}"
            logger.error(f"Plugin install error ({plugin_id}): {msg}")
            on_done(MarketplaceResult(False, msg, plugin_id))
        except Exception as e:
            msg = f"Install error: {e}"
            logger.error(f"Plugin install error ({plugin_id}): {msg}")
            on_done(MarketplaceResult(False, msg, plugin_id))

    def _download_text(self, url: str) -> str:
        req = request.Request(url, headers={"User-Agent": "FilePilot/1.0"})
        with request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            return resp.read().decode("utf-8")

    @staticmethod
    def _version_gt(v1: str, v2: str) -> bool:
        """Return True if v1 > v2 (simple semver compare)."""
        def _parts(v):
            try:
                return tuple(int(x) for x in v.split("."))
            except Exception:
                return (0, 0, 0)
        return _parts(v1) > _parts(v2)
