import csv
import json
import os
import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from app.config_loader import get_config_path, get_notifications_path, get_plugins_dir
from app.branding import APP_COPYRIGHT, APP_DEVELOPER, APP_EMAIL, APP_NAME, APP_TAGLINE, APP_VERSION, APP_WEBSITE
from app.i18n import t, set_language, get_language, available_languages
from app.main import build_monitor
from app.startup_manager import disable_startup, enable_startup, launched_from_startup
from app.notification_center import NotificationCenter
from app.auto_backup import AutoBackupManager
from app.plugin_watcher import PluginWatcher
from app.smart_classifier import load_smart_rules, save_smart_rules
from app.application_service import FilePilotService, StartupStatus

from app.gui_toast import ToastManager
from app.gui_theme import ThemeMixin
from app.gui_builder import BuilderMixin
from app.gui_monitoring import MonitoringMixin
from app.gui_actions import ActionsMixin
from app.gui_dashboard import (
    build_dashboard_page, draw_classification_chart,
    refresh_top_categories_view, refresh_recent_activity_view,
)
from app.gui_notifications import (
    build_notifications_page, add_notification,
    refresh_notifications_view, clear_notifications,
)
from app.gui_tools import (
    build_tools_page, reload_plugins_from_gui,
    open_plugins_folder, create_plugin_template,
)
from app.gui_wizard import (
    check_first_run_wizard, _wizard_browse_folder,
    save_first_run_setup, open_welcome_wizard,
)




class FileAutomationGUI(
    ThemeMixin,
    BuilderMixin,
    MonitoringMixin,
    ActionsMixin,
):
    """
    Main application class for FilePilot.
    Composed from focused mixin modules:
      ThemeMixin      — color palette and ttk styles
      BuilderMixin    — UI layout and widget construction
      MonitoringMixin — start/stop, tray, live callbacks, theme/language
      ActionsMixin    — rules, settings, stats, history, logs, plugins
    External mixins (via module functions):
      gui_dashboard, gui_notifications, gui_tools, gui_wizard
    """

    # ── External module functions bound as methods ────────────────────────────
    build_dashboard_page      = build_dashboard_page
    draw_classification_chart = draw_classification_chart
    refresh_top_categories_view = refresh_top_categories_view
    refresh_recent_activity_view = refresh_recent_activity_view

    build_notifications_page  = build_notifications_page
    add_notification          = add_notification
    refresh_notifications_view = refresh_notifications_view
    clear_notifications       = clear_notifications

    build_tools_page          = build_tools_page
    reload_plugins_from_gui   = reload_plugins_from_gui
    open_plugins_folder       = open_plugins_folder
    create_plugin_template    = create_plugin_template

    check_first_run_wizard    = check_first_run_wizard
    _wizard_browse_folder     = _wizard_browse_folder
    save_first_run_setup      = save_first_run_setup
    open_welcome_wizard       = open_welcome_wizard

    # ── Also import toggle_run_at_startup from gui_tools ─────────────────────
    from app.gui_tools import toggle_run_at_startup

    def __init__(self, root, service=None, owns_service=True):
        self.root = root
        self.service = service or FilePilotService()
        self._owns_service = owns_service
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("1380x920")
        self.root.minsize(1240, 860)
        self.set_window_icon()

        startup = self.service.bootstrap()
        if startup.status is not StartupStatus.READY:
            self.config = startup.config or {
                "source_folder": "incoming",
                "organized_base_folder": "organized",
            }
            self.monitor = None
            self._initialize_pre_runtime_shell(startup)
            return

        self.config = self.service.config
        self.monitor = self.service.monitor
        self._pre_runtime_shell = False

        # Start auto-backup scheduler
        self.backup_manager = AutoBackupManager(self.config)
        self.backup_manager.start()

        # Load saved language from config
        saved_lang = self.config.get("language", "en")
        try:
            set_language(saved_lang)
        except Exception:
            set_language("en")

        self.notification_center = NotificationCenter(
            storage_path=get_notifications_path()
        )
        self.notifications_count_var = tk.StringVar(value="0")
        
        plugins_dir = get_plugins_dir()
        self.first_run_completed = self.config.get("first_run_completed", False)
        self.plugin_watcher = PluginWatcher(
            plugins_dir,
            self._queue_plugin_reload,
        )

        self.plugin_watcher.start()
        
        self.monitor_thread = None
        self.rule_entries = {}
        self.nav_buttons = {}
        self.history_rows_cache = []
        self.tray_icon = None
        self.is_hidden_to_tray = False
        self.started_from_startup = launched_from_startup()

        self.logs_window = None
        self.logs_text_widget = None
        self.logs_search_var = tk.StringVar()
        self.logs_level_var = tk.StringVar(value="All")
        self.logs_auto_refresh_var = tk.BooleanVar(value=False)
        self.logs_line_count_var = tk.StringVar(value="Lines: 0")
        self.logs_displayed_content = ""
        self.logs_auto_refresh_job = None

        # Live update & auto-refresh state
        self._auto_refresh_job = None
        self._dot_pulse_job = None
        self._dot_phase = 0
        self._live_callback_job = None
        self._live_callback_generation = 0
        self._start_live_callback_pump()
        self._activity_unsubscribe = self.service.subscribe_activity(
            self._make_live_callback()
        )
        self._state_unsubscribe = self.service.subscribe_state(
            self._queue_service_state
        )

        self.status_var = tk.StringVar(value=t("status_stopped"))
        self.last_file_var = tk.StringVar(value="No file processed yet")
        self.status_bar_var = tk.StringVar(value="Ready")

        self.source_folder_var = tk.StringVar(value=self.config.get("source_folder", "incoming"))
        self.organized_base_var = tk.StringVar(value=self.config.get("organized_base_folder", "organized"))

        self.processing_wait_var = tk.StringVar(
            value=str(self.config.get("processing_wait_seconds", 5))
        )
        self.duplicate_window_var = tk.StringVar(
            value=str(self.config.get("duplicate_event_window_seconds", 3))
        )
        self.archive_by_date_var = tk.BooleanVar(
            value=self.config.get("archive_by_date", False)
        )
        # Read real startup state from the shortcut, not from config
        from app.startup_manager import is_startup_enabled
        self.run_at_startup_var = tk.BooleanVar(
            value=is_startup_enabled()
        )

        self.total_files_var = tk.StringVar(value="0")
        self.failed_files_var = tk.StringVar(value="0")
        self.duplicates_var = tk.StringVar(value="0")
        self.documents_var = tk.StringVar(value="0")
        self.rules_count_var = tk.StringVar(value="0")
        self.plugin_classified_var = tk.StringVar(value="0")
        self.smart_classified_var = tk.StringVar(value="0")
        self.content_classified_var = tk.StringVar(value="0")
        self.top_category_var = tk.StringVar(value="-")
        self.extension_classified_var = tk.StringVar(value="0")
        self.recent_activity_count_var = tk.StringVar(value="0")
        
        self.plugins_loaded_count_var = tk.StringVar(value="0")
        self.plugins_failed_count_var = tk.StringVar(value="0")
        
        self.new_rule_name_var = tk.StringVar()
        self.new_rule_extensions_var = tk.StringVar()
        self.smart_rule_entries = {}
        self.new_smart_category_var = tk.StringVar()
        self.new_smart_keywords_var = tk.StringVar()
        
        self.history_search_var = tk.StringVar()
        self.history_category_var = tk.StringVar(value="All")
        self.history_status_var = tk.StringVar(value="All")

        self.current_page = None

        self.style = ttk.Style()
        self.theme_mode = "dark"
        self.configure_theme()

        self.toast_manager = ToastManager(self.root, self.colors)

        self.create_layout()
        self.refresh_stats()
        self.refresh_history()
        self.update_rules_count()
        self.refresh_plugins_view()
        self.refresh_notifications_view()
        self._apply_monitor_state(self.service.monitor_state)
        self.root.after(300, self.check_first_run_wizard)

        # Show language wizard if language not yet configured
        if not self.config.get("language"):
            self.root.after(500, self.open_language_wizard)
        loaded_plugins = len(self.config.get("loaded_plugins", []))
        self.add_notification(
            "info",
            "Application Started",
            f"FilePilot started successfully. Loaded plugins: {loaded_plugins}"
        )
        # Update nav badge with existing notification count on startup
        self.root.after(100, self._update_notif_badge)
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_close_to_tray)
        self.handle_startup_launch()

    def _initialize_pre_runtime_shell(self, startup):
        """Show setup or blocked state without constructing runtime subsystems."""
        self._pre_runtime_shell = True
        self.first_run_completed = False
        self.theme_mode = "dark"
        self.style = ttk.Style()
        self.configure_theme()
        self.toast_manager = ToastManager(self.root, self.colors)

        outer = tk.Frame(self.root, bg=self.colors["bg"])
        outer.pack(fill="both", expand=True, padx=48, pady=48)
        title = (
            "Setup required"
            if startup.status is StartupStatus.SETUP_REQUIRED
            else "FilePilot cannot start monitoring"
        )
        detail = startup.error or "Monitoring is unavailable until this state is resolved."
        if startup.blocking_operation_ids:
            detail += "\n\nOperations requiring review:\n" + "\n".join(
                startup.blocking_operation_ids
            )

        tk.Label(
            outer,
            text=title,
            bg=self.colors["bg"],
            fg=self.colors["text"],
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="w")
        tk.Label(
            outer,
            text=detail,
            bg=self.colors["bg"],
            fg=self.colors["muted"],
            font=("Segoe UI", 11),
            justify="left",
            wraplength=850,
        ).pack(anchor="w", pady=(12, 24))

        if startup.status is StartupStatus.SETUP_REQUIRED:
            ttk.Button(
                outer,
                text="Open Setup Wizard",
                style="Primary.TButton",
                command=self.open_welcome_wizard,
            ).pack(anchor="w")
            self.root.after(100, self.open_welcome_wizard)
        else:
            reasons = "\n".join(startup.blocking_reasons)
            if reasons:
                tk.Label(
                    outer,
                    text=reasons,
                    bg=self.colors["bg"],
                    fg=self.colors["muted"],
                    font=("Segoe UI", 10),
                    justify="left",
                    wraplength=850,
                ).pack(anchor="w", pady=(0, 24))

        ttk.Button(
            outer,
            text="Exit",
            style="Secondary.TButton",
            command=self.root.destroy,
        ).pack(anchor="w", pady=(12, 0))
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

    def activate_after_setup(self, start_now: bool) -> None:
        """Replace the setup-only shell with the configured application."""
        for widget in self.root.winfo_children():
            widget.destroy()
        self.__init__(
            self.root,
            service=self.service,
            owns_service=self._owns_service,
        )
        if start_now and self.service.startup_status is StartupStatus.READY:
            self.root.after(0, self.start_monitoring)


def launch_gui(service=None, owns_service=True):
    root = tk.Tk()
    app = FileAutomationGUI(root, service=service, owns_service=owns_service)
    root.mainloop()

