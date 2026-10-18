from __future__ import annotations

import ctypes
import traceback
import sys

from app.application_paths import get_application_paths
from app.config_loader import ensure_external_config_exists
from app.product_identity import PRODUCT_IDENTITY


def _report_startup_error(error: Exception) -> None:
    message = f"{PRODUCT_IDENTITY.product_name} could not start.\n\n{error}"
    try:
        paths = get_application_paths()
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        (paths.logs_dir / "startup-error.log").write_text(
            traceback.format_exc(),
            encoding="utf-8",
        )
    except Exception:
        pass
    try:
        ctypes.windll.user32.MessageBoxW(0, message, PRODUCT_IDENTITY.product_name, 0x10)
    except Exception:
        pass


def main() -> int:
    """Launch the packaged professional Qt application."""
    if len(sys.argv) > 1 and sys.argv[1] == "--package-ocr-check":
        if len(sys.argv) != 4:
            return 2
        try:
            from app.package_validation import check_ocr
            return check_ocr(sys.argv[2], sys.argv[3])
        except Exception:
            return 1
    try:
        ensure_external_config_exists()
        from app.ui.qt.application import run_qt

        return run_qt()
    except Exception as error:
        _report_startup_error(error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
