import logging
import threading
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

_DEBOUNCE_SECONDS = 0.5


class PluginChangeHandler(FileSystemEventHandler):
    def __init__(self, reload_callback):
        self.reload_callback = reload_callback
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def on_any_event(self, event):
        if event.is_directory:
            return

        if not event.src_path.endswith(".py"):
            return

        # Debounce: cancel any previous timer and start a new one.
        # This prevents 3-5 reloads per single save.
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(_DEBOUNCE_SECONDS, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self):
        logging.info("Plugin change detected — reloading plugins")
        try:
            self.reload_callback()
        except Exception as error:
            logging.error(f"Plugin reload failed: {error}", exc_info=True)


class PluginWatcher:
    def __init__(self, plugins_dir: Path, reload_callback):
        self.plugins_dir = plugins_dir
        self.reload_callback = reload_callback
        self.observer = Observer()

    def start(self):
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        handler = PluginChangeHandler(self.reload_callback)
        self.observer.schedule(handler, str(self.plugins_dir), recursive=False)
        self.observer.start()
        logging.info(f"Plugin watcher started: {self.plugins_dir}")

    def stop(self):
        self.observer.stop()
        self.observer.join()
        logging.info("Plugin watcher stopped.")
