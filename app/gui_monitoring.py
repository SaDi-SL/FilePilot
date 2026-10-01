"""
gui_monitoring.py — Monitoring control, tray, live updates, theme/language for FilePilot.
Mixin class: MonitoringMixin
"""
import json
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from app.branding import APP_NAME, APP_VERSION, APP_DEVELOPER
from app.i18n import t, set_language, get_language, available_languages
from app.main import build_monitor
from app.gui_toast import ToastManager
from app.application_service import ActivityEvent, MonitorState, StartupStatus


class MonitoringMixin:
    """Handles start/stop monitoring, tray, live callbacks, dot animation, theme & language."""

    def get_icon_path(self) -> Path | None:
        if getattr(sys, "frozen", False):
            base_path = Path(sys.executable).parent
        else:
            base_path = Path(__file__).resolve().parent.parent

        icon_path = base_path / "icon.ico"
        if icon_path.exists():
            return icon_path
        return None

    def set_window_icon(self):
        icon_path = self.get_icon_path()
        if icon_path is not None:
            try:
                self.root.iconbitmap(str(icon_path))
            except Exception:
                pass

    def handle_startup_launch(self):
        if not self.started_from_startup:
            return

        self.setup_tray_icon()
        self.start_monitoring()
        self.root.after(1000, self.hide_to_tray)

    def _make_live_callback(self):
        """Create a worker-safe callback consumed by the Tk main thread."""
        if not hasattr(self, "_live_callback_queue"):
            self._live_callback_queue = queue.SimpleQueue()
        self._live_callback_generation = (
            getattr(self, "_live_callback_generation", 0) + 1
        )
        generation = self._live_callback_generation

        def _callback(*args):
            if len(args) == 1 and isinstance(args[0], ActivityEvent):
                event = args[0]
            else:
                filename, category, status = args
                event = ActivityEvent(Path(filename), category, status)
            self._live_callback_queue.put((generation, event))
        return _callback

    def _queue_service_state(self, state: MonitorState) -> None:
        if not hasattr(self, "_service_state_queue"):
            self._service_state_queue = queue.SimpleQueue()
        self._service_state_queue.put(state)

    def _start_live_callback_pump(self):
        if not hasattr(self, "_live_callback_queue"):
            self._live_callback_queue = queue.SimpleQueue()
        if not hasattr(self, "_monitor_error_queue"):
            self._monitor_error_queue = queue.SimpleQueue()
        if not hasattr(self, "_service_state_queue"):
            self._service_state_queue = queue.SimpleQueue()
        if not hasattr(self, "_plugin_reload_queue"):
            self._plugin_reload_queue = queue.SimpleQueue()
        if getattr(self, "_live_callback_job", None) is None:
            self._live_callback_job = self.root.after(
                50,
                self._drain_live_callbacks,
            )

    def _drain_live_callbacks(self):
        self._live_callback_job = None
        generation = getattr(self, "_live_callback_generation", 0)
        for _ in range(50):
            try:
                item_generation, event = (
                    self._live_callback_queue.get_nowait()
                )
            except queue.Empty:
                break
            if item_generation == generation:
                self.last_activity_event = event
                self._on_file_processed(
                    event.filename,
                    event.category,
                    event.status,
                )
        for _ in range(10):
            try:
                state = self._service_state_queue.get_nowait()
            except queue.Empty:
                break
            self._apply_monitor_state(state)
        for _ in range(10):
            try:
                error = self._monitor_error_queue.get_nowait()
            except queue.Empty:
                break
            messagebox.showerror("Error", f"Failed to start monitoring:\n{error}")
        try:
            self._plugin_reload_queue.get_nowait()
        except queue.Empty:
            pass
        else:
            self.reload_plugins_from_gui()
        self._start_live_callback_pump()

    def _queue_plugin_reload(self):
        if not hasattr(self, "_plugin_reload_queue"):
            self._plugin_reload_queue = queue.SimpleQueue()
        self._plugin_reload_queue.put(True)

    def _stop_live_callback_pump(self):
        job = getattr(self, "_live_callback_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
            self._live_callback_job = None

    def _on_file_processed(
        self,
        filename: str,
        category: str,
        status: str,
    ):
        """Called on the main thread after each file is processed."""
        try:
            self.last_file_var.set(filename)
            self.refresh_stats()
            self.refresh_recent_activity_view()

            # Update history tab directly without re-reading the full file
            self._append_to_history_tree(filename, category, status)

            # Update mini live feed on dashboard
            self._push_live_feed(filename, category, status)

            # Update notifications count
            self.notifications_count_var.set(str(self.notification_center.count()))

            icon = {
                "moved": "✔",
                "duplicate": "•",
                "hash_check_failed": "✖",
                "failed": "✖",
                "error": "✖",
                "unknown": "•",
            }.get(status, "•")
            destination = None
            event = getattr(self, "last_activity_event", None)
            if (
                event is not None
                and event.filename == filename
                and event.category == category
                and event.status == status
            ):
                destination = event.actual_destination or event.duplicate_target
            outcome = str(destination) if destination is not None else category
            self.status_bar_var.set(f"{icon} Processed: {filename} → {outcome}")

            # Send tray notification when app is hidden
            if self.is_hidden_to_tray and self.tray_icon is not None:
                try:
                    self.tray_icon.notify(f"{filename} → {outcome}", APP_NAME)
                except Exception:
                    pass
        except Exception:
            pass

    def _append_to_history_tree(self, filename: str, category: str, status: str):
        """Append one row to history_tree directly without re-reading the full file."""
        if not hasattr(self, "history_tree"):
            return
        try:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # Insert at top
            self.history_tree.insert("", 0, values=(filename, category, status, timestamp))
            # Update the cache
            self.history_rows_cache.append({
                "filename": filename, "category": category,
                "status": status, "timestamp": timestamp,
                "classification_method": "", "smart_source": ""
            })
            # Keep cache capped at 500 rows
            if len(self.history_rows_cache) > 500:
                self.history_rows_cache = self.history_rows_cache[-500:]
        except Exception:
            pass

    def _push_live_feed(self, filename: str, category: str, status: str) -> None:
        """Push one entry to the mini live feed on the dashboard (max 5 cards)."""
        if not hasattr(self, "live_feed_frame"):
            return
        try:
            from datetime import datetime
            timestamp = datetime.now().strftime("%H:%M:%S")

            frame = self.live_feed_frame

            # Hide empty placeholder
            if hasattr(self, "live_feed_empty"):
                self.live_feed_empty.pack_forget()

            # Status color
            status_colors = {
                "moved":             self.colors["stat_green"],
                "duplicate":         self.colors["stat_amber"],
                "duplicate_skipped": self.colors["stat_amber"],
                "hash_check_failed": self.colors["stat_red"],
                "failed":            self.colors["stat_red"],
                "disappeared":       self.colors["muted"],
            }
            dot_color = status_colors.get(status, self.colors["muted"])

            # Build card
            card = tk.Frame(frame, bg=self.colors["card_2"],
                            highlightbackground=self.colors["border"],
                            highlightthickness=1)

            # Dot
            dot = tk.Canvas(card, width=8, height=8,
                            bg=self.colors["card_2"], highlightthickness=0)
            dot.pack(side="left", padx=(10, 6), pady=8)
            dot.create_oval(1, 1, 7, 7, fill=dot_color, outline="")

            # Time
            tk.Label(card, text=timestamp,
                     bg=self.colors["card_2"], fg=self.colors["muted"],
                     font=("Consolas", 8), padx=0).pack(side="left", padx=(0, 8))

            # Filename (truncated)
            short_name = filename if len(filename) <= 36 else filename[:33] + "..."
            tk.Label(card, text=short_name,
                     bg=self.colors["card_2"], fg=self.colors["text"],
                     font=("Segoe UI", 9)).pack(side="left")

            # Arrow
            tk.Label(card, text="->",
                     bg=self.colors["card_2"], fg=self.colors["muted"],
                     font=("Segoe UI", 9)).pack(side="left", padx=(6, 4))

            # Category
            tk.Label(card, text=category,
                     bg=self.colors["card_2"], fg=dot_color,
                     font=("Segoe UI", 9, "bold")).pack(side="left")

            # Insert at top
            card.pack(fill="x", pady=(0, 3), before=frame.winfo_children()[0]
                      if frame.winfo_children() else None)

            # Keep only last 5 cards
            cards = [w for w in frame.winfo_children()
                     if isinstance(w, tk.Frame)]
            if len(cards) > 5:
                cards[-1].destroy()

        except Exception:
            pass

    def _start_dot_pulse(self):
        """Animated green dot indicating that monitoring is active."""
        self._dot_phase = 0
        self._animate_dot()

    def _animate_dot(self):
        if getattr(self, "service", None) is not None:
            if self.service.monitor_state is not MonitorState.RUNNING:
                return
        elif not self.monitor.is_running:
            return
        try:
            # Pulse: dot expands and shrinks between 2 and 6 pixels
            phase = self._dot_phase % 20
            r = 2 + 1.5 * (1 - abs(phase - 10) / 10)
            cx, cy = 4, 4
            self._status_dot.coords(self._dot_oval,
                                    cx - r, cy - r, cx + r, cy + r)
            self._status_dot.itemconfig(self._dot_oval, fill=self.colors["success"])
            self._dot_phase += 1
            self._dot_pulse_job = self.root.after(80, self._animate_dot)
        except Exception:
            pass

    def _stop_dot_pulse(self):
        if self._dot_pulse_job:
            try:
                self.root.after_cancel(self._dot_pulse_job)
            except Exception:
                pass
            self._dot_pulse_job = None
        try:
            self._status_dot.coords(self._dot_oval, 1, 1, 7, 7)
            self._status_dot.itemconfig(self._dot_oval, fill=self.colors["danger_fg"])
        except Exception:
            pass

    def _start_auto_refresh(self):
        """Auto-refresh the dashboard every 10 seconds."""
        def _tick():
            if getattr(self, "service", None) is not None:
                if self.service.monitor_state is not MonitorState.RUNNING:
                    return
            elif not self.monitor.is_running:
                return
            try:
                self.refresh_stats()
                self.refresh_recent_activity_view()
            except Exception:
                pass
            self._auto_refresh_job = self.root.after(10_000, _tick)

        self._auto_refresh_job = self.root.after(10_000, _tick)

    def _stop_auto_refresh(self):
        if self._auto_refresh_job:
            self.root.after_cancel(self._auto_refresh_job)
            self._auto_refresh_job = None

    # ---- Keyboard shortcut handlers ----

    def _kb_refresh(self):
        self.refresh_stats()
        self.refresh_history()
        self.status_bar_var.set("Refreshed (Ctrl+R / F5)")

    def _kb_toggle_monitor(self):
        service = getattr(self, "service", None)
        if service is not None:
            if service.monitor_state in {MonitorState.STARTING, MonitorState.RUNNING}:
                self.stop_monitoring()
            else:
                self.start_monitoring()
        elif self.monitor.is_running:
            self.stop_monitoring()
        else:
            self.start_monitoring()

    def _kb_save(self):
        page = self.current_page
        if page == "settings":
            self.save_settings()
        elif page == "rules":
            self.save_rules()

    def start_monitoring(self):
        service = getattr(self, "service", None)
        if service is not None:
            if service.startup_status is not StartupStatus.READY:
                self._apply_monitor_state(
                    MonitorState.BLOCKED
                    if service.startup_status is StartupStatus.BLOCKED
                    else MonitorState.ERROR
                )
                return
            command_active = getattr(self, "_service_command_active", False)
            desired = getattr(self, "_desired_monitor_running", False)
            if (service.monitor_state in {MonitorState.STARTING, MonitorState.RUNNING}
                    and not (command_active and not desired)):
                return
            self._request_service_monitoring(True)
            return

        if self.monitor.is_running:
            return

        self._start_live_callback_pump()
        monitor = self.monitor
        # Bind callback for live updates
        monitor.set_file_processed_callback(self._make_live_callback())

        def run_monitor():
            try:
                monitor.start_all()
            except Exception as error:
                self._monitor_error_queue.put(str(error))

        self.monitor_thread_monitor = monitor
        self.monitor_thread = threading.Thread(target=run_monitor, daemon=True)
        self.monitor_thread.start()

        self.status_var.set(t("status_running"))
        try:
            self.header_status.config(
                text="Running",
                bg=self.colors["success_bg"],
                fg=self.colors["success"],
            )
            self._status_badge.config(
                bg=self.colors["success_bg"],
                highlightbackground=self.colors["success_border"],
            )
            self._status_dot.config(bg=self.colors["success_bg"])
        except Exception:
            pass
        self.status_bar_var.set("Monitoring started  (Ctrl+M to stop)")
        try: self.start_button.config(state="disabled")
        except Exception: pass
        try: self.stop_button.config(state="normal")
        except Exception: pass
        self.toast_manager.show_toast("Monitoring started.", "success")
        self.add_notification("success", "Monitoring Started", "File monitoring started.")

        self._start_dot_pulse()
        self._start_auto_refresh()

    def stop_monitoring(self):
        service = getattr(self, "service", None)
        if service is not None:
            command_active = getattr(self, "_service_command_active", False)
            if service.monitor_state is MonitorState.BLOCKED:
                return
            if service.monitor_state is MonitorState.STOPPED and not command_active:
                return
            self._request_service_monitoring(False)
            return

        monitor_thread = getattr(self, "monitor_thread", None)
        start_in_progress = monitor_thread is not None and monitor_thread.is_alive()
        if not self.monitor.is_running and not start_in_progress:
            return

        self._stop_dot_pulse()
        self._stop_auto_refresh()

        if start_in_progress and monitor_thread is not threading.current_thread():
            monitor_thread.join()
        self._stop_monitor_lifecycle()
        self.status_var.set(t("status_stopped"))
        try:
            self.header_status.config(
                text="Stopped",
                bg=self.colors["danger"],
                fg=self.colors["danger_fg"],
            )
            self._status_badge.config(
                bg=self.colors["danger"],
                highlightbackground=self.colors["danger_border"],
            )
            self._status_dot.config(bg=self.colors["danger"])
        except Exception:
            pass
        self.status_bar_var.set("Monitoring stopped  (Ctrl+M to start)")
        try: self.start_button.config(state="normal")
        except Exception: pass
        try: self.stop_button.config(state="disabled")
        except Exception: pass
        # Do not rebuild monitor here — keep it so start_monitoring can rebind the callback
        self.toast_manager.show_toast("Monitoring stopped.", "warning")
        self.add_notification("warning", "Monitoring Stopped", "File monitoring stopped.")

    def _request_service_monitoring(self, desired_running: bool) -> None:
        """Serialize GUI lifecycle requests so the latest request wins."""
        if not hasattr(self, "_service_command_lock"):
            self._service_command_lock = threading.Lock()
            self._service_command_active = False
            self._desired_monitor_running = False
        with self._service_command_lock:
            self._desired_monitor_running = desired_running
            if self._service_command_active:
                return
            self._service_command_active = True

        def run_commands():
            try:
                while True:
                    with self._service_command_lock:
                        desired = self._desired_monitor_running
                    state = self.service.start() if desired else self.service.stop()
                    with self._service_command_lock:
                        if desired != self._desired_monitor_running:
                            continue
                        self._service_command_active = False
                    if state in {MonitorState.ERROR, MonitorState.BLOCKED}:
                        self._monitor_error_queue.put(
                            self.service.last_error or "Monitoring lifecycle failed"
                        )
                    return
            except Exception as error:
                with self._service_command_lock:
                    self._service_command_active = False
                self._monitor_error_queue.put(str(error))

        self.monitor_thread = threading.Thread(target=run_commands, daemon=True)
        self.monitor_thread.start()

    def _stop_monitor_lifecycle(self):
        service = getattr(self, "service", None)
        if service is not None:
            service.stop()
            self.monitor = service.monitor
            return
        monitor = getattr(self, "monitor", None)
        if monitor is None:
            return
        stop_all = getattr(monitor, "stop_all", None)
        if callable(stop_all):
            stop_all()
            return
        stop = getattr(monitor, "stop", None)
        if callable(stop):
            stop()

    def exit_application(self):
        if hasattr(self, "plugin_watcher"):
            try:
                self.plugin_watcher.stop()
            except Exception:
                pass

        try:
            monitor_thread = getattr(self, "monitor_thread", None)
            if (monitor_thread is not None and monitor_thread.is_alive()
                    and monitor_thread is not threading.current_thread()):
                monitor_thread.join()
            if getattr(self, "_owns_service", True):
                self._stop_monitor_lifecycle()
        except Exception:
            pass

        if hasattr(self, "backup_manager"):
            try:
                self.backup_manager.stop()
            except Exception:
                pass

        self._stop_dot_pulse()
        self._stop_auto_refresh()
        self._stop_live_callback_pump()

        for name in ("_activity_unsubscribe", "_state_unsubscribe"):
            unsubscribe = getattr(self, name, None)
            if callable(unsubscribe):
                unsubscribe()

        try:
            if self.tray_icon is not None:
                self.tray_icon.stop()
        except Exception:
            pass

        try:
            self.close_logs_viewer()
        except Exception:
            pass
        
        self.root.destroy()

    def create_tray_image(self):
        icon_path = self.get_icon_path()
        if icon_path is not None:
            try:
                return Image.open(icon_path)
            except Exception:
                pass

        image = Image.new("RGB", (64, 64), color=(37, 99, 235))
        draw = ImageDraw.Draw(image)
        draw.rectangle((14, 14, 50, 50), fill=(255, 255, 255))
        draw.rectangle((22, 22, 42, 42), fill=(37, 99, 235))
        return image

    def setup_tray_icon(self):
        if self.tray_icon is not None:
            return

        menu = pystray.Menu(
            pystray.MenuItem("Show", self.restore_from_tray),
            pystray.MenuItem("Start Monitoring", self.tray_start_monitoring),
            pystray.MenuItem("Stop Monitoring", self.tray_stop_monitoring),
            pystray.MenuItem("Exit", self.tray_exit_application),
        )

        self.tray_icon = pystray.Icon(
            APP_NAME,
            self.create_tray_image(),
            APP_NAME,
            menu,
        )

        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def on_close_to_tray(self):
        self.hide_to_tray()

    def hide_to_tray(self):
        self.setup_tray_icon()
        self.root.withdraw()
        self.is_hidden_to_tray = True

        if not self.started_from_startup:
            self.toast_manager.show_toast("App minimized to tray.", "info")

    def restore_from_tray(self, icon=None, item=None):
        self.root.after(0, self._restore_window)

    def _restore_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.is_hidden_to_tray = False

    def tray_start_monitoring(self, icon=None, item=None):
        self.root.after(0, self.start_monitoring)

    def tray_stop_monitoring(self, icon=None, item=None):
        self.root.after(0, self.stop_monitoring)

    def tray_exit_application(self, icon=None, item=None):
        self.root.after(0, self.exit_application)

    def toggle_theme(self):
        # 1) Save running state before destroying widgets
        was_running = (
            self.service.monitor_state is MonitorState.RUNNING
            if getattr(self, "service", None) is not None
            else self.monitor.is_running
        )

        # 2) Cancel recurring jobs before destroying widgets
        self._stop_dot_pulse()
        self._stop_auto_refresh()

        self.theme_mode = "light" if self.theme_mode == "dark" else "dark"
        self.configure_theme()

        if self.logs_window and self.logs_window.winfo_exists():
            try:
                self.logs_window.destroy()
            except Exception:
                pass
            self.logs_window = None
            self.logs_text_widget = None

        for widget in self.root.winfo_children():
            widget.destroy()

        self.toast_manager = ToastManager(self.root, self.colors)

        self.create_layout()
        self.refresh_stats()
        self.refresh_history()
        self.update_rules_count()
        self.render_smart_rule_entries()
        self.refresh_plugins_view()
        self.refresh_notifications_view()

        # 3) Rebind callback and restart if was running
        if was_running:
            self.status_var.set(t("status_running"))
            try:
                self.header_status.config(
                    text="Running",
                    bg=self.colors["success_bg"],
                    fg=self.colors["success"],
                )
                self._status_badge.config(
                    bg=self.colors["success_bg"],
                    highlightbackground=self.colors["success_border"],
                )
                self._status_dot.config(bg=self.colors["success_bg"])
            except Exception:
                pass
            try: self.start_button.config(state="disabled")
            except Exception: pass
            try: self.stop_button.config(state="normal")
            except Exception: pass
            self._start_dot_pulse()
            self._start_auto_refresh()

        self._update_notif_badge()
        self.toast_manager.show_toast(
            f"Switched to {self.theme_mode} mode.",
            "info"
        )

    def change_language(self, lang_code: str) -> None:
        """Switch language and rebuild the UI."""
        try:
            set_language(lang_code)
            # Save to config
            from app.config_loader import get_config_path
            import json
            cfg_path = get_config_path()
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["language"] = lang_code
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

        # Rebuild UI with new language (same as toggle_theme)
        was_running = (
            self.service.monitor_state is MonitorState.RUNNING
            if getattr(self, "service", None) is not None
            else self.monitor.is_running
        )
        self._stop_dot_pulse()
        self._stop_auto_refresh()

        if self.logs_window and self.logs_window.winfo_exists():
            try:
                self.logs_window.destroy()
            except Exception:
                pass
            self.logs_window = None
            self.logs_text_widget = None

        for widget in self.root.winfo_children():
            widget.destroy()

        self.toast_manager = ToastManager(self.root, self.colors)
        self.create_layout()
        self.refresh_stats()
        self.refresh_history()
        self.update_rules_count()
        self.render_smart_rule_entries()
        self.refresh_plugins_view()
        self.refresh_notifications_view()

        if was_running:
            try:
                self.header_status.config(text=t("status_running"),
                                          bg=self.colors["success_bg"],
                                          fg=self.colors["success"])
                self._status_badge.config(bg=self.colors["success_bg"],
                                          highlightbackground=self.colors["success_border"])
                self._status_dot.config(bg=self.colors["success_bg"])
            except Exception:
                pass
            try: self.start_button.config(state="disabled")
            except Exception: pass
            try: self.stop_button.config(state="normal")
            except Exception: pass
            self._start_dot_pulse()
            self._start_auto_refresh()

        self._update_notif_badge()
        self.toast_manager.show_toast(t("msg_lang_restart"), "info")

    def _apply_monitor_state(self, state: MonitorState) -> None:
        """Render the service's confirmed monitor state on the Tk thread."""
        previous = getattr(self, "_displayed_monitor_state", None)
        self._displayed_monitor_state = state
        if state is MonitorState.RUNNING:
            self.status_var.set(t("status_running"))
            self.header_status.config(
                text="Running",
                bg=self.colors["success_bg"],
                fg=self.colors["success"],
            )
            self._status_badge.config(
                bg=self.colors["success_bg"],
                highlightbackground=self.colors["success_border"],
            )
            self._status_dot.config(bg=self.colors["success_bg"])
            self.start_button.config(state="disabled")
            self.stop_button.config(state="normal")
            self.status_bar_var.set("Monitoring started  (Ctrl+M to stop)")
            self._start_dot_pulse()
            self._start_auto_refresh()
            if previous is not MonitorState.RUNNING:
                self.toast_manager.show_toast("Monitoring started.", "success")
                self.add_notification("success", "Monitoring Started", "File monitoring started.")
            return

        self._stop_dot_pulse()
        self._stop_auto_refresh()
        if state in {MonitorState.STARTING, MonitorState.STOPPING}:
            text = "Starting..." if state is MonitorState.STARTING else "Stopping..."
            self.status_var.set(text)
            self.header_status.config(text=text)
            self.start_button.config(state="disabled")
            self.stop_button.config(state="disabled")
            self.status_bar_var.set(text)
            return

        if state is MonitorState.STOPPED:
            text = "Stopped"
            status_text = "Monitoring stopped  (Ctrl+M to start)"
            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")
        elif state is MonitorState.BLOCKED:
            text = "Blocked"
            status_text = getattr(self.service, "last_error", None) or "Recovery review required"
            self.start_button.config(state="disabled")
            self.stop_button.config(state="disabled")
        else:
            text = "Error"
            status_text = getattr(self.service, "last_error", None) or "Monitoring error"
            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")
        self.status_var.set(text)
        self.header_status.config(
            text=text,
            bg=self.colors["danger"],
            fg=self.colors["danger_fg"],
        )
        self._status_badge.config(
            bg=self.colors["danger"],
            highlightbackground=self.colors["danger_border"],
        )
        self._status_dot.config(bg=self.colors["danger"])
        self.status_bar_var.set(status_text)

    def open_language_wizard(self) -> None:
        """Show language selection wizard on first launch."""
        wiz = tk.Toplevel(self.root)
        wiz.title("FilePilot — Language / اللغة / Langue / Dil")
        wiz.geometry("480x380")
        wiz.resizable(False, False)
        wiz.configure(bg=self.colors["bg"])
        wiz.transient(self.root)
        wiz.grab_set()

        icon_path = self.get_icon_path()
        if icon_path:
            try:
                wiz.iconbitmap(str(icon_path))
            except Exception:
                pass

        outer = tk.Frame(wiz, bg=self.colors["bg"])
        outer.pack(fill="both", expand=True, padx=32, pady=28)

        # Title in all 4 languages
        tk.Label(outer, text="FilePilot",
                 bg=self.colors["bg"], fg=self.colors["text"],
                 font=("Segoe UI", 18, "bold")).pack(anchor="w")

        tk.Label(outer,
                 text="Choose your language  /  اختر لغتك  /  Choisissez votre langue  /  Dilinizi secin",
                 bg=self.colors["bg"], fg=self.colors["muted"],
                 font=("Segoe UI", 9), wraplength=400, justify="left").pack(anchor="w", pady=(4, 20))

        tk.Frame(outer, bg=self.colors["border_2"], height=1).pack(fill="x", pady=(0, 20))

        langs = available_languages()
        selected_var = tk.StringVar(value="en")

        for code, name in langs:
            row = tk.Frame(outer, bg=self.colors["bg"])
            row.pack(fill="x", pady=3)
            tk.Radiobutton(
                row, text=f"  {name}",
                variable=selected_var, value=code,
                bg=self.colors["bg"], fg=self.colors["text"],
                selectcolor=self.colors["panel_2"],
                activebackground=self.colors["bg"],
                activeforeground=self.colors["text"],
                font=("Segoe UI", 11), cursor="hand2",
            ).pack(anchor="w")

        tk.Frame(outer, bg=self.colors["border_2"], height=1).pack(fill="x", pady=(20, 16))

        def _confirm():
            code = selected_var.get()
            wiz.destroy()
            if code != get_language():
                self.change_language(code)

        tk.Button(outer, text="Continue  /  متابعة  /  Continuer  /  Devam",
                  bg=self.colors["accent"], fg="white",
                  activebackground=self.colors["accent_2"], activeforeground="white",
                  relief="flat", bd=0, padx=20, pady=8,
                  font=("Segoe UI", 10, "bold"), cursor="hand2",
                  command=_confirm).pack(anchor="e")
