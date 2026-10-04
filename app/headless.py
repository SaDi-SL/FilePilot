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
import threading
from pathlib import Path

from app.application_service import FilePilotService, MonitorState, StartupStatus

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
    from app.config_loader import get_resource_path
    candidates = [
        get_resource_path("icon.ico"),
        get_resource_path("icon.png"),
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

    def __init__(self, service=None) -> None:
        self.service        = service or FilePilotService()
        self.monitor        = None
        self.config         = None
        self.tray_icon      = None
        self._gui_open      = False
        self._open_gui_requested = False
        self._stop_event    = threading.Event()
        self._command_lock = threading.Lock()
        self._command_active = False
        self._desired_running = False

    def run(self) -> None:
        """Start the headless app: build monitor, start tray, auto-start monitoring."""
        logger.info("FilePilot starting in headless mode...")

        startup = self.service.bootstrap()
        self.config = self.service.config
        self.monitor = self.service.monitor

        # Auto-start monitoring if configured
        if (startup.status is StartupStatus.READY
                and self.config.get("auto_start_monitoring", True)):
            self._start_monitoring()
        elif startup.status is StartupStatus.BLOCKED:
            logger.error("Headless startup blocked: %s", startup.error)
        elif startup.status is StartupStatus.SETUP_REQUIRED:
            logger.info("Headless startup requires setup; monitoring was not started.")
        elif startup.status is StartupStatus.ERROR:
            logger.error("Headless startup failed: %s", startup.error)

        try:
            # Build and run tray (blocking)
            self._run_tray()
            if self._open_gui_requested:
                from app.gui import launch_gui
                launch_gui(service=self.service, owns_service=False)
        finally:
            self.service.shutdown()

    # ── Monitoring ────────────────────────────────────────────────────────────

    def _start_monitoring(self) -> None:
        state = self.service.start()
        self.monitor = self.service.monitor
        if state is MonitorState.RUNNING:
            logger.info("Headless monitoring started.")
            self._notify("Monitoring started")
        else:
            logger.error("Headless monitoring did not start: %s", self.service.last_error)
            self._notify(self.service.last_error or "Monitoring did not start")
        self._update_tray_title()

    def _stop_monitoring(self) -> None:
        state = self.service.stop()
        if state is MonitorState.STOPPED:
            logger.info("Headless monitoring stopped.")
            self._notify("Monitoring stopped")
        else:
            logger.error("Headless monitoring did not stop cleanly: %s", self.service.last_error)
        self._update_tray_title()

    def _status_text(self) -> str:
        state = self.service.monitor_state
        monitor = self.service.monitor
        if state is MonitorState.RUNNING and monitor is not None:
            n = len(monitor.running_folders)
            return f"Running ({n} folder{'s' if n != 1 else ''})"
        return {
            MonitorState.STARTING: "Starting",
            MonitorState.STOPPING: "Stopping",
            MonitorState.BLOCKED: "Blocked",
            MonitorState.ERROR: "Error",
        }.get(state, "Stopped")

    def _request_monitoring(self, desired_running: bool) -> None:
        """Serialize tray lifecycle commands so the latest request wins."""
        with self._command_lock:
            self._desired_running = desired_running
            if self._command_active:
                return
            self._command_active = True

        def worker():
            try:
                while True:
                    with self._command_lock:
                        desired = self._desired_running
                    if desired:
                        self._start_monitoring()
                    else:
                        self._stop_monitoring()
                    with self._command_lock:
                        if desired != self._desired_running:
                            continue
                        self._command_active = False
                    return
            except Exception:
                logger.error("Headless lifecycle command failed", exc_info=True)
                with self._command_lock:
                    self._command_active = False

        threading.Thread(target=worker, daemon=True).start()

    # ── Tray ──────────────────────────────────────────────────────────────────

    def _run_tray(self) -> None:
        import pystray
        from app.branding import APP_NAME

        def _on_start(icon, item):
            self._request_monitoring(True)

        def _on_stop(icon, item):
            self._request_monitoring(False)

        def _on_open_gui(icon, item):
            self._open_gui(icon)

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

    def _open_gui(self, icon=None) -> None:
        """Request a main-thread handoff from the tray loop to the GUI."""
        if self._gui_open:
            return
        self._gui_open = True
        self._open_gui_requested = True
        tray = icon or self.tray_icon
        if tray is not None:
            tray.stop()

    def _exit(self, icon=None) -> None:
        """Clean shutdown."""
        logger.info("FilePilot headless shutting down...")
        try:
            self.service.shutdown()
        except Exception:
            pass
        if icon:
            icon.stop()


# ── Entry point ───────────────────────────────────────────────────────────────

def run_headless(service=None) -> None:
    """Called from run.py when --headless flag is passed."""
    app = HeadlessApp(service=service)
    app.run()
