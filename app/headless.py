"""
headless.py — Headless mode for FilePilot.

Runs the full file automation engine without a GUI window.
Only a system tray icon is shown, with controls to start/stop
monitoring and open the full GUI if needed.

Usage:
    python run.py --headless

Tray menu:
    FilePilot (title)
    ─────────────────
    Status: Running / Stopped
    ─────────────────
    Start Monitoring
    Stop Monitoring
    ─────────────────
    Open GUI
    ─────────────────
    Exit
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Tray image helper (same logic as GUI) ─────────────────────────────────────

def _build_tray_image(icon_path: Path | None = None):
    from PIL import Image, ImageDraw
    if icon_path and icon_path.exists():
        try:
            return Image.open(icon_path)
        except Exception:
            pass
    image = Image.new("RGB", (64, 64), color=(37, 99, 235))
    draw = ImageDraw.Draw(image)
    draw.rectangle((14, 14, 50, 50), fill=(255, 255, 255))
    draw.rectangle((22, 22, 42, 42), fill=(37, 99, 235))
    return image


def _get_icon_path() -> Path | None:
    from app.config_loader import get_runtime_base_dir
    candidates = [
        get_runtime_base_dir() / "icon.ico",
        get_runtime_base_dir() / "icon.png",
        Path(getattr(sys, "_MEIPASS", "")) / "icon.ico",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


# ── Headless app ──────────────────────────────────────────────────────────────

class HeadlessApp:
    """
    Full FilePilot automation running without a GUI window.
    Controlled entirely via the system tray icon.
    """

    def __init__(self) -> None:
        self.monitor        = None
        self.config         = None
        self.tray_icon      = None
        self._gui_open      = False
        self._stop_event    = threading.Event()

    def run(self) -> None:
        """Start the headless app: build monitor, start tray, auto-start monitoring."""
        logger.info("FilePilot starting in headless mode...")

        # Build monitor
        from app.main import build_monitor
        self.config, self.monitor = build_monitor()

        # Auto-start monitoring if configured
        if self.config.get("auto_start_monitoring", True):
            self._start_monitoring()

        # Build and run tray (blocking)
        self._run_tray()

    # ── Monitoring ────────────────────────────────────────────────────────────

    def _start_monitoring(self) -> None:
        if self.monitor and not self.monitor.is_running:
            self.monitor.start_all()
            logger.info("Headless monitoring started.")
            self._notify("Monitoring started")
            self._update_tray_title()

    def _stop_monitoring(self) -> None:
        if self.monitor and self.monitor.is_running:
            self.monitor.stop_all()
            logger.info("Headless monitoring stopped.")
            self._notify("Monitoring stopped")
            self._update_tray_title()

    def _status_text(self) -> str:
        if self.monitor and self.monitor.is_running:
            n = len(self.monitor.running_folders)
            return f"Running ({n} folder{'s' if n != 1 else ''})"
        return "Stopped"

    # ── Tray ──────────────────────────────────────────────────────────────────

    def _run_tray(self) -> None:
        import pystray
        from app.branding import APP_NAME

        def _on_start(icon, item):
            threading.Thread(target=self._start_monitoring, daemon=True).start()

        def _on_stop(icon, item):
            threading.Thread(target=self._stop_monitoring, daemon=True).start()

        def _on_open_gui(icon, item):
            threading.Thread(target=self._open_gui, daemon=True).start()

        def _on_exit(icon, item):
            self._exit(icon)

        menu = pystray.Menu(
            pystray.MenuItem(
                lambda item: f"Status: {self._status_text()}",
                lambda: None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start Monitoring", _on_start),
            pystray.MenuItem("Stop Monitoring",  _on_stop),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open GUI",         _on_open_gui),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit",             _on_exit),
        )

        self.tray_icon = pystray.Icon(
            APP_NAME,
            _build_tray_image(_get_icon_path()),
            f"{APP_NAME} — {self._status_text()}",
            menu,
        )

        logger.info("Tray icon running. FilePilot is active in the background.")
        self.tray_icon.run()   # blocks until exit

    def _update_tray_title(self) -> None:
        if self.tray_icon:
            from app.branding import APP_NAME
            try:
                self.tray_icon.title = f"{APP_NAME} — {self._status_text()}"
            except Exception:
                pass

    def _notify(self, message: str) -> None:
        """Send a tray notification if possible."""
        if self.tray_icon:
            from app.branding import APP_NAME
            try:
                self.tray_icon.notify(message, APP_NAME)
            except Exception:
                pass

    def _open_gui(self) -> None:
        """Launch the full GUI window (once)."""
        if self._gui_open:
            return
        self._gui_open = True
        try:
            from app.gui import launch_gui
            launch_gui()
        except Exception as e:
            logger.error(f"Failed to open GUI: {e}")
        finally:
            self._gui_open = False

    def _exit(self, icon=None) -> None:
        """Clean shutdown."""
        logger.info("FilePilot headless shutting down...")
        if self.monitor:
            try:
                self.monitor.stop_all()
            except Exception:
                pass
        if icon:
            icon.stop()


# ── Entry point ───────────────────────────────────────────────────────────────

def run_headless() -> None:
    """Called from run.py when --headless flag is passed."""
    app = HeadlessApp()
    app.run()
