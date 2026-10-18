"""Explicit update checks and privacy-preserving support diagnostics."""
import json
import platform
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib import error, request
from urllib.parse import urlparse

from app.ocr_runtime import discover_ocr_runtime
from app.product_identity import PRODUCT_IDENTITY

RELEASES_API = "https://api.github.com/repos/SaDi-SL/FilePilot/releases/latest"


@dataclass(frozen=True)
class UpdateCheck:
    status: str
    message: str
    url: str = ""


def version_tuple(value):
    match = re.fullmatch(r"[vV]?(\d+)\.(\d+)\.(\d+)(?:\+[\w.-]+)?", value or "")
    return tuple(map(int, match.groups())) if match else None


def check_updates():
    req = request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json", "User-Agent": "FilePilot"})
    try:
        with request.urlopen(req, timeout=5) as response:
            raw = response.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise ValueError("Release response too large")
        data = json.loads(raw)
        latest = data.get("tag_name", "")
        latest_version = version_tuple(latest)
        url = data.get("html_url", "")
        parsed = urlparse(url)
        if (not latest_version or data.get("draft") or data.get("prerelease")
                or parsed.scheme != "https" or parsed.netloc != "github.com"
                or not parsed.path.startswith("/SaDi-SL/FilePilot/releases/tag/")):
            raise ValueError("Unexpected release metadata")
    except error.HTTPError as exc:
        if exc.code == 404:
            return UpdateCheck("none", "No published stable release was found.")
        return UpdateCheck("error", "GitHub could not check releases. Try again later.")
    except Exception:
        return UpdateCheck("error", "Could not check releases. Check your connection and try again.")
    current = version_tuple(PRODUCT_IDENTITY.version)
    if current is None:
        return UpdateCheck("unknown", f"Latest published release: {latest}. Your build version cannot be compared.", url)
    if latest_version > current:
        return UpdateCheck("available", f"A newer stable release is published: {latest}.", url)
    return UpdateCheck("current", f"Latest published stable release: {latest}. This build: {PRODUCT_IDENTITY.display_version}.", url)


def diagnostics(service):
    runtime = discover_ocr_runtime()
    catalog = service.library_snapshot()
    return {
        "schema": 1, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "product": PRODUCT_IDENTITY.product_name, "version": PRODUCT_IDENTITY.display_version,
        "platform": platform.system(), "architecture": platform.machine(), "python": platform.python_version(),
        "monitor": service.monitor_state.value,
        "startup": service.startup_status.value if service.startup_status else "initializing",
        "ocr_available": runtime.available, "ocr_languages": list(runtime.languages),
        "cataloged_files": catalog.total, "files_with_text": catalog.searchable,
        "files_with_vectors": catalog.semantic,
        "note": "No file names, file contents, folder paths, credentials or raw logs are included.",
    }
