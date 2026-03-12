import csv
import json
import os
import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pystray
from PIL import Image, ImageDraw

from app.branding import (
    APP_COPYRIGHT,
    APP_DEVELOPER,
    APP_EMAIL,
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
    APP_WEBSITE,
)
from app.main import build_monitor
from app.config_loader import get_config_path
from app.startup_manager import disable_startup, enable_startup, launched_from_startup


class ToastManager:
    def __init__(self, root, colors):
        self.root = root
        self.colors = colors
        self.active_toasts = []

    def show_toast(self, message, level="info", duration=3000):
        bg_map = {
            "success": self.colors["success"],
            "error": self.colors["danger"],
            "warning": self.colors["warning"],
            "info": self.colors["accent"],
        }
        bg = bg_map.get(level, self.colors["accent"])

        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg=bg)

        frame = tk.Frame(toast, bg=bg, padx=14, pady=10)
        frame.pack(fill="both", expand=True)

        label = tk.Label(
            frame,
            text=message,
            bg=bg,
            fg="white",
            font=("Segoe UI", 10, "bold"),
            justify="left",
            wraplength=280,
        )
        label.pack()

        self.position_toast(toast)
        self.active_toasts.append(toast)

        def close_toast():
            if toast in self.active_toasts:
                self.active_toasts.remove(toast)
            try:
                toast.destroy()
            except Exception:
                pass
            self.reposition_all()

        toast.after(duration, close_toast)

    def position_toast(self, toast):
        self.root.update_idletasks()
        width = 320
        height = 60
        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        root_w = self.root.winfo_width()
        x = root_x + root_w - width - 20
        y = root_y + 20 + (len(self.active_toasts) * 70)
        toast.geometry(f"{width}x{height}+{x}+{y}")

    def reposition_all(self):
        self.root.update_idletasks()
        for idx, toast in enumerate(self.active_toasts):
            try:
                width = 320
                height = 60
                root_x = self.root.winfo_rootx()
                root_y = self.root.winfo_rooty()
                root_w = self.root.winfo_width()
                x = root_x + root_w - width - 20
                y = root_y + 20 + (idx * 70)
                toast.geometry(f"{width}x{height}+{x}+{y}")
            except Exception:
                pass


class FileAutomationGUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("1280x860")
        self.root.minsize(1180, 820)
        self.set_window_icon()

        self.config, self.monitor = build_monitor()
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

        self.status_var = tk.StringVar(value="Stopped")
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
        self.run_at_startup_var = tk.BooleanVar(
            value=self.config.get("run_at_startup", False)
        )

        self.total_files_var = tk.StringVar(value="0")
        self.failed_files_var = tk.StringVar(value="0")
        self.duplicates_var = tk.StringVar(value="0")
        self.documents_var = tk.StringVar(value="0")
        self.rules_count_var = tk.StringVar(value="0")

        self.new_rule_name_var = tk.StringVar()
        self.new_rule_extensions_var = tk.StringVar()

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

        self.root.protocol("WM_DELETE_WINDOW", self.on_close_to_tray)
        self.handle_startup_launch()

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

    def configure_theme(self):
        self.style.theme_use("clam")

        if self.theme_mode == "dark":
            self.colors = {
                "bg": "#0b1220",
                "panel": "#111827",
                "panel_2": "#1f2937",
                "card": "#162033",
                "text": "#f8fafc",
                "muted": "#94a3b8",
                "accent": "#3b82f6",
                "accent_2": "#2563eb",
                "active_nav": "#1d4ed8",
                "border": "#334155",
                "danger": "#ef4444",
                "danger_2": "#b91c1c",
                "success": "#10b981",
                "warning": "#f59e0b",
                "input_bg": "#0f172a",
                "log_info": "#cbd5e1",
                "log_warning": "#fbbf24",
                "log_error": "#f87171",
                "log_debug": "#93c5fd",
            }
        else:
            self.colors = {
                "bg": "#f8fafc",
                "panel": "#ffffff",
                "panel_2": "#e2e8f0",
                "card": "#ffffff",
                "text": "#0f172a",
                "muted": "#475569",
                "accent": "#2563eb",
                "accent_2": "#1d4ed8",
                "active_nav": "#dbeafe",
                "border": "#cbd5e1",
                "danger": "#dc2626",
                "danger_2": "#b91c1c",
                "success": "#059669",
                "warning": "#d97706",
                "input_bg": "#ffffff",
                "log_info": "#334155",
                "log_warning": "#b45309",
                "log_error": "#dc2626",
                "log_debug": "#2563eb",
            }

        self.root.configure(bg=self.colors["bg"])

        self.style.configure(
            "Primary.TButton",
            background=self.colors["accent"],
            foreground="white",
            borderwidth=0,
            padding=(14, 10),
            font=("Segoe UI", 10, "bold"),
            focuscolor="none",
        )
        self.style.map(
            "Primary.TButton",
            background=[("active", self.colors["accent_2"])],
            foreground=[("active", "white")],
        )

        self.style.configure(
            "Secondary.TButton",
            background=self.colors["panel_2"],
            foreground=self.colors["text"],
            borderwidth=0,
            padding=(12, 9),
            font=("Segoe UI", 10),
            focuscolor="none",
        )
        self.style.map(
            "Secondary.TButton",
            background=[("active", self.colors["border"])],
            foreground=[("active", self.colors["text"])],
        )

        self.style.configure(
            "Danger.TButton",
            background=self.colors["danger"],
            foreground="white",
            borderwidth=0,
            padding=(12, 9),
            font=("Segoe UI", 10, "bold"),
            focuscolor="none",
        )
        self.style.map(
            "Danger.TButton",
            background=[("active", self.colors["danger_2"])],
            foreground=[("active", "white")],
        )

        self.style.configure("TEntry", padding=7)
        self.style.configure("TCombobox", padding=6)

        self.style.configure(
            "Treeview",
            background=self.colors["panel"],
            fieldbackground=self.colors["panel"],
            foreground=self.colors["text"],
            bordercolor=self.colors["border"],
            rowheight=30,
            font=("Consolas", 10),
        )
        self.style.configure(
            "Treeview.Heading",
            background=self.colors["panel_2"],
            foreground=self.colors["text"],
            font=("Segoe UI", 10, "bold"),
        )
        self.style.map(
            "Treeview",
            background=[("selected", self.colors["accent"])],
            foreground=[("selected", "white")],
        )

    def create_layout(self):
        self.main_frame = tk.Frame(self.root, bg=self.colors["bg"])
        self.main_frame.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(self.main_frame, bg=self.colors["panel"], width=250)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.content_area = tk.Frame(self.main_frame, bg=self.colors["bg"])
        self.content_area.pack(side="right", fill="both", expand=True)

        self.build_sidebar()
        self.build_header()
        self.build_pages()
        self.build_status_bar()

        self.show_page("dashboard")

    def build_sidebar(self):
        logo_frame = tk.Frame(self.sidebar, bg=self.colors["panel"])
        logo_frame.pack(fill="x", padx=20, pady=(20, 10))

        title = tk.Label(
            logo_frame,
            text=APP_NAME,
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 18, "bold"),
        )
        title.pack(anchor="w")

        subtitle = tk.Label(
            logo_frame,
            text=APP_TAGLINE,
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 10),
            wraplength=190,
            justify="left",
        )
        subtitle.pack(anchor="w", pady=(4, 0))

        separator = tk.Frame(self.sidebar, bg=self.colors["border"], height=1)
        separator.pack(fill="x", padx=20, pady=15)

        nav_frame = tk.Frame(self.sidebar, bg=self.colors["panel"])
        nav_frame.pack(fill="x", padx=12)

        nav_items = [
            ("dashboard", "📊  Dashboard"),
            ("settings", "⚙  Settings"),
            ("rules", "🧩  Rules Editor"),
            ("history", "🕘  History"),
            ("tools", "🛠  Tools"),
        ]

        for key, label in nav_items:
            btn = tk.Button(
                nav_frame,
                text=label,
                bg=self.colors["panel"],
                fg=self.colors["text"],
                activebackground=self.colors["panel_2"],
                activeforeground=self.colors["text"],
                relief="flat",
                bd=0,
                padx=20,
                pady=14,
                anchor="w",
                font=("Segoe UI", 10, "bold"),
                command=lambda k=key: self.show_page(k),
                cursor="hand2",
            )

            def on_enter(e, b=btn, item_key=key):
                if self.current_page != item_key:
                    b.configure(bg=self.colors["panel_2"])

            def on_leave(e, b=btn, item_key=key):
                if self.current_page != item_key:
                    b.configure(bg=self.colors["panel"])

            btn.bind("<Enter>", on_enter)
            btn.bind("<Leave>", on_leave)

            btn.pack(fill="x", pady=4)
            self.nav_buttons[key] = btn

        bottom_frame = tk.Frame(self.sidebar, bg=self.colors["panel"])
        bottom_frame.pack(side="bottom", fill="x", padx=16, pady=18)

        self.theme_button = tk.Button(
            bottom_frame,
            text=f"Theme: {self.theme_mode.title()}",
            bg=self.colors["panel_2"],
            fg=self.colors["text"],
            activebackground=self.colors["border"],
            activeforeground=self.colors["text"],
            relief="flat",
            bd=0,
            padx=14,
            pady=10,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
            command=self.toggle_theme,
        )
        self.theme_button.pack(fill="x")

    def build_header(self):
        self.header = tk.Frame(self.content_area, bg=self.colors["panel"], height=90)
        self.header.pack(fill="x")
        self.header.pack_propagate(False)

        left = tk.Frame(self.header, bg=self.colors["panel"])
        left.pack(side="left", fill="y", padx=24, pady=16)

        self.header_title = tk.Label(
            left,
            text="Dashboard",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 20, "bold"),
        )
        self.header_title.pack(anchor="w")

        self.header_subtitle = tk.Label(
            left,
            text="Overview of the automation system",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 11),
        )
        self.header_subtitle.pack(anchor="w", pady=(4, 0))

        right = tk.Frame(self.header, bg=self.colors["panel"])
        right.pack(side="right", padx=24, pady=16)

        status_title = tk.Label(
            right,
            text="System Status",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        )
        status_title.pack(anchor="e")

        self.header_status = tk.Label(
            right,
            text="Stopped",
            bg=self.colors["panel"],
            fg=self.colors["danger"],
            font=("Segoe UI", 12, "bold"),
        )
        self.header_status.pack(anchor="e")

    def build_pages(self):
        self.pages_container = tk.Frame(self.content_area, bg=self.colors["bg"])
        self.pages_container.pack(fill="both", expand=True)

        self.pages = {
            "dashboard": tk.Frame(self.pages_container, bg=self.colors["bg"]),
            "settings": tk.Frame(self.pages_container, bg=self.colors["bg"]),
            "rules": tk.Frame(self.pages_container, bg=self.colors["bg"]),
            "history": tk.Frame(self.pages_container, bg=self.colors["bg"]),
            "tools": tk.Frame(self.pages_container, bg=self.colors["bg"]),
        }

        for page in self.pages.values():
            page.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.build_dashboard_page()
        self.build_settings_page()
        self.build_rules_page()
        self.build_history_page()
        self.build_tools_page()

    def build_status_bar(self):
        footer_text = f"{APP_NAME} v{APP_VERSION}  •  Developed by {APP_DEVELOPER}"

        self.status_bar = tk.Label(
            self.root,
            textvariable=self.status_bar_var,
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            anchor="w",
            padx=12,
            pady=8,
            font=("Segoe UI", 9),
        )
        self.status_bar.pack(fill="x", side="bottom")

        self.footer_bar = tk.Label(
            self.root,
            text=footer_text,
            bg=self.colors["bg"],
            fg=self.colors["muted"],
            anchor="e",
            padx=12,
            pady=4,
            font=("Segoe UI", 8),
        )
        self.footer_bar.pack(fill="x", side="bottom")

    def highlight_active_nav(self, active_key):
        for key, btn in self.nav_buttons.items():
            if key == active_key:
                btn.configure(
                    bg=self.colors["active_nav"],
                    fg="white",
                    padx=24
                )
            else:
                btn.configure(
                    bg=self.colors["panel"],
                    fg=self.colors["text"],
                    padx=20
                )

    def show_page(self, page_name: str):
        self.current_page = page_name
        self.pages[page_name].tkraise()
        self.highlight_active_nav(page_name)

        titles = {
            "dashboard": ("Dashboard", "Overview of the automation system"),
            "settings": ("Settings", "Configure folders and processing behavior"),
            "rules": ("Rules Editor", "Manage categories and extensions"),
            "history": ("History", "Recent processing activity"),
            "tools": ("Tools", "Maintenance and admin tools"),
        }

        title, subtitle = titles.get(page_name, ("Page", ""))
        self.header_title.config(text=title)
        self.header_subtitle.config(text=subtitle)

    def toggle_theme(self):
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
        self.toast_manager.show_toast(
            f"Switched to {self.theme_mode} mode.",
            "info"
        )

    def open_about_dialog(self):
        about_window = tk.Toplevel(self.root)
        about_window.title(f"About {APP_NAME}")
        about_window.geometry("520x340")
        about_window.resizable(False, False)
        about_window.configure(bg=self.colors["bg"])
        about_window.transient(self.root)
        about_window.grab_set()

        icon_path = self.get_icon_path()
        if icon_path is not None:
            try:
                about_window.iconbitmap(str(icon_path))
            except Exception:
                pass

        outer = tk.Frame(about_window, bg=self.colors["bg"], padx=24, pady=24)
        outer.pack(fill="both", expand=True)

        title = tk.Label(
            outer,
            text=APP_NAME,
            bg=self.colors["bg"],
            fg=self.colors["text"],
            font=("Segoe UI", 22, "bold"),
        )
        title.pack(anchor="center", pady=(0, 8))

        version = tk.Label(
            outer,
            text=f"Version {APP_VERSION}",
            bg=self.colors["bg"],
            fg=self.colors["accent"],
            font=("Segoe UI", 11, "bold"),
        )
        version.pack(anchor="center", pady=(0, 14))

        tagline = tk.Label(
            outer,
            text=APP_TAGLINE,
            bg=self.colors["bg"],
            fg=self.colors["muted"],
            font=("Segoe UI", 10),
            wraplength=430,
            justify="center",
        )
        tagline.pack(anchor="center", pady=(0, 18))

        developer = tk.Label(
            outer,
            text=f"Developed by: {APP_DEVELOPER}",
            bg=self.colors["bg"],
            fg=self.colors["text"],
            font=("Segoe UI", 10, "bold"),
        )
        developer.pack(anchor="w", pady=4)

        if APP_EMAIL:
            email = tk.Label(
                outer,
                text=f"Email: {APP_EMAIL}",
                bg=self.colors["bg"],
                fg=self.colors["text"],
                font=("Segoe UI", 10),
            )
            email.pack(anchor="w", pady=4)

        if APP_WEBSITE:
            website = tk.Label(
                outer,
                text=f"Website: {APP_WEBSITE}",
                bg=self.colors["bg"],
                fg=self.colors["text"],
                font=("Segoe UI", 10),
            )
            website.pack(anchor="w", pady=4)

        copyright_label = tk.Label(
            outer,
            text=APP_COPYRIGHT,
            bg=self.colors["bg"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        )
        copyright_label.pack(anchor="w", pady=(18, 6))

        description = tk.Label(
            outer,
            text=(
                "This application automatically monitors, organizes, archives, "
                "and manages files through a desktop control panel with smart rules, "
                "history tracking, logs viewer, and tray integration."
            ),
            bg=self.colors["bg"],
            fg=self.colors["text"],
            font=("Segoe UI", 10),
            wraplength=430,
            justify="left",
        )
        description.pack(anchor="w", pady=(4, 18))

        ttk.Button(
            outer,
            text="Close",
            style="Primary.TButton",
            command=about_window.destroy,
        ).pack(anchor="e")

    def create_info_panel(self, parent, title):
        outer = tk.Frame(parent, bg=self.colors["border"], highlightthickness=0)
        inner = tk.Frame(outer, bg=self.colors["card"], padx=10, pady=10)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        label = tk.Label(
            inner,
            text=title,
            bg=self.colors["card"],
            fg=self.colors["text"],
            font=("Segoe UI", 13, "bold"),
        )
        label.pack(anchor="w", padx=10, pady=(10, 4))
        return outer

    def create_stat_box(self, parent, title, value_var):
        box = tk.Frame(
            parent,
            bg=self.colors["card"],
            bd=0,
            highlightbackground=self.colors["border"],
            highlightthickness=1,
            padx=10,
            pady=10,
        )

        tk.Label(
            box,
            textvariable=value_var,
            bg=self.colors["card"],
            fg=self.colors["accent"],
            font=("Segoe UI", 24, "bold"),
        ).pack(anchor="w")

        tk.Label(
            box,
            text=title,
            bg=self.colors["card"],
            fg=self.colors["muted"],
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(4, 0))

        return box

    def build_dashboard_page(self):
        page = self.pages["dashboard"]

        top = tk.Frame(page, bg=self.colors["bg"])
        top.pack(fill="x", padx=20, pady=20)

        status_card = self.create_info_panel(top, "System Status")
        status_card.pack(fill="x")

        row1 = tk.Frame(status_card, bg=self.colors["card"])
        row1.pack(fill="x", padx=16, pady=(16, 8))

        tk.Label(
            row1,
            text="Current Status:",
            bg=self.colors["card"],
            fg=self.colors["muted"],
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")

        self.status_label_dashboard = tk.Label(
            row1,
            textvariable=self.status_var,
            bg=self.colors["card"],
            fg=self.colors["text"],
            font=("Segoe UI", 10, "bold"),
        )
        self.status_label_dashboard.pack(side="left", padx=(10, 0))

        row2 = tk.Frame(status_card, bg=self.colors["card"])
        row2.pack(fill="x", padx=16, pady=(0, 16))

        tk.Label(
            row2,
            text="Last Processed File:",
            bg=self.colors["card"],
            fg=self.colors["muted"],
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")

        tk.Label(
            row2,
            textvariable=self.last_file_var,
            bg=self.colors["card"],
            fg=self.colors["text"],
            font=("Segoe UI", 10),
        ).pack(side="left", padx=(10, 0))

        cards_row = tk.Frame(page, bg=self.colors["bg"])
        cards_row.pack(fill="x", padx=20, pady=(0, 10))

        self.create_stat_box(cards_row, "Total Files", self.total_files_var).pack(side="left", fill="x", expand=True, padx=6)
        self.create_stat_box(cards_row, "Failed", self.failed_files_var).pack(side="left", fill="x", expand=True, padx=6)
        self.create_stat_box(cards_row, "Duplicates", self.duplicates_var).pack(side="left", fill="x", expand=True, padx=6)
        self.create_stat_box(cards_row, "Documents", self.documents_var).pack(side="left", fill="x", expand=True, padx=6)
        self.create_stat_box(cards_row, "Rules Count", self.rules_count_var).pack(side="left", fill="x", expand=True, padx=6)

        actions_panel = self.create_info_panel(page, "Quick Actions")
        actions_panel.pack(fill="x", padx=20, pady=10)

        actions_inner = tk.Frame(actions_panel, bg=self.colors["card"])
        actions_inner.pack(fill="x", padx=12, pady=12)

        self.start_button = ttk.Button(
            actions_inner,
            text="Start Monitoring",
            style="Primary.TButton",
            command=self.start_monitoring,
        )
        self.start_button.grid(row=0, column=0, padx=6, pady=6)

        self.stop_button = ttk.Button(
            actions_inner,
            text="Stop Monitoring",
            style="Danger.TButton",
            command=self.stop_monitoring,
        )
        self.stop_button.grid(row=0, column=1, padx=6, pady=6)
        self.stop_button.state(["disabled"])

        ttk.Button(actions_inner, text="Refresh Stats", style="Secondary.TButton", command=self.refresh_stats).grid(row=0, column=2, padx=6, pady=6)
        ttk.Button(actions_inner, text="Refresh History", style="Secondary.TButton", command=self.refresh_history).grid(row=0, column=3, padx=6, pady=6)
        ttk.Button(actions_inner, text="Open Incoming Folder", style="Secondary.TButton", command=lambda: self.open_folder(self.config["source_folder"])).grid(row=1, column=0, padx=6, pady=6)
        ttk.Button(actions_inner, text="Open Organized Folder", style="Secondary.TButton", command=lambda: self.open_folder(self.config["organized_base_folder"])).grid(row=1, column=1, padx=6, pady=6)

        stats_panel = self.create_info_panel(page, "Detailed Statistics")
        stats_panel.pack(fill="both", expand=True, padx=20, pady=10)

        self.stats_text = tk.Text(
            stats_panel,
            wrap="word",
            state="disabled",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            relief="flat",
            font=("Consolas", 10),
        )
        self.stats_text.pack(fill="both", expand=True, padx=12, pady=12)

    def build_settings_page(self):
        page = self.pages["settings"]

        outer = tk.Frame(page, bg=self.colors["bg"])
        outer.pack(fill="both", expand=True, padx=20, pady=20)

        folders_panel = self.create_info_panel(outer, "Folders")
        folders_panel.pack(fill="x", pady=10)

        folders_inner = tk.Frame(folders_panel, bg=self.colors["card"])
        folders_inner.pack(fill="x", padx=12, pady=12)

        tk.Label(folders_inner, text="Incoming Folder:", bg=self.colors["card"], fg=self.colors["text"], font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(folders_inner, textvariable=self.source_folder_var, width=80).grid(row=0, column=1, sticky="w", padx=8, pady=8)
        ttk.Button(folders_inner, text="Browse", style="Secondary.TButton", command=self.browse_source_folder).grid(row=0, column=2, padx=8, pady=8)

        tk.Label(folders_inner, text="Organized Base Folder:", bg=self.colors["card"], fg=self.colors["text"], font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(folders_inner, textvariable=self.organized_base_var, width=80).grid(row=1, column=1, sticky="w", padx=8, pady=8)
        ttk.Button(folders_inner, text="Browse", style="Secondary.TButton", command=self.browse_organized_folder).grid(row=1, column=2, padx=8, pady=8)

        config_panel = self.create_info_panel(outer, "Configuration")
        config_panel.pack(fill="x", pady=10)

        config_inner = tk.Frame(config_panel, bg=self.colors["card"])
        config_inner.pack(fill="x", padx=12, pady=12)

        tk.Label(config_inner, text="Processing Wait Seconds:", bg=self.colors["card"], fg=self.colors["text"], font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(config_inner, textvariable=self.processing_wait_var, width=24).grid(row=0, column=1, sticky="w", padx=8, pady=8)

        tk.Label(config_inner, text="Duplicate Event Window Seconds:", bg=self.colors["card"], fg=self.colors["text"], font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", padx=8, pady=8)
        ttk.Entry(config_inner, textvariable=self.duplicate_window_var, width=24).grid(row=1, column=1, sticky="w", padx=8, pady=8)

        ttk.Checkbutton(
            config_inner,
            text="Archive Files By Date",
            variable=self.archive_by_date_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=10)

        buttons = tk.Frame(outer, bg=self.colors["bg"])
        buttons.pack(fill="x", pady=10)

        ttk.Button(buttons, text="Save Settings", style="Primary.TButton", command=self.save_settings).pack(side="left", padx=6)
        ttk.Button(buttons, text="Reload Settings", style="Secondary.TButton", command=self.reload_settings).pack(side="left", padx=6)

    def build_rules_page(self):
        page = self.pages["rules"]

        outer = tk.Frame(page, bg=self.colors["bg"])
        outer.pack(fill="both", expand=True, padx=20, pady=20)

        tk.Label(
            outer,
            text="Edit file extension rules. Separate extensions with commas, for example: .jpg, .jpeg, .png",
            bg=self.colors["bg"],
            fg=self.colors["muted"],
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(0, 10))

        rules_panel = self.create_info_panel(outer, "Current Rules")
        rules_panel.pack(fill="both", expand=True, pady=10)

        rules_host = tk.Frame(rules_panel, bg=self.colors["card"])
        rules_host.pack(fill="both", expand=True, padx=12, pady=12)

        canvas = tk.Canvas(rules_host, highlightthickness=0, bg=self.colors["card"])
        scrollbar = ttk.Scrollbar(rules_host, orient="vertical", command=canvas.yview)
        self.rules_inner_frame = tk.Frame(canvas, bg=self.colors["card"])

        self.rules_inner_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )

        canvas.create_window((0, 0), window=self.rules_inner_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.render_rule_entries()

        add_panel = self.create_info_panel(outer, "Add New Rule Category")
        add_panel.pack(fill="x", pady=10)

        add_inner = tk.Frame(add_panel, bg=self.colors["card"])
        add_inner.pack(fill="x", padx=12, pady=12)

        tk.Label(add_inner, text="Category Name:", bg=self.colors["card"], fg=self.colors["text"], font=("Segoe UI", 10)).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        ttk.Entry(add_inner, textvariable=self.new_rule_name_var, width=28).grid(row=0, column=1, padx=8, pady=8, sticky="w")

        tk.Label(add_inner, text="Extensions:", bg=self.colors["card"], fg=self.colors["text"], font=("Segoe UI", 10)).grid(row=1, column=0, padx=8, pady=8, sticky="w")
        ttk.Entry(add_inner, textvariable=self.new_rule_extensions_var, width=78).grid(row=1, column=1, padx=8, pady=8, sticky="w")

        ttk.Button(add_inner, text="Add Rule", style="Primary.TButton", command=self.add_new_rule).grid(
            row=0, column=2, rowspan=2, padx=10, pady=8
        )

        buttons = tk.Frame(outer, bg=self.colors["bg"])
        buttons.pack(fill="x", pady=10)

        ttk.Button(buttons, text="Save Rules", style="Primary.TButton", command=self.save_rules).pack(side="left", padx=6)
        ttk.Button(buttons, text="Reload Rules", style="Secondary.TButton", command=self.reload_rules).pack(side="left", padx=6)
        ttk.Button(buttons, text="Validate Rules", style="Secondary.TButton", command=self.validate_rules_preview).pack(side="left", padx=6)

    def build_history_page(self):
        page = self.pages["history"]

        outer = tk.Frame(page, bg=self.colors["bg"])
        outer.pack(fill="both", expand=True, padx=20, pady=20)

        filters_panel = self.create_info_panel(outer, "Search & Filters")
        filters_panel.pack(fill="x", pady=10)

        filters_inner = tk.Frame(filters_panel, bg=self.colors["card"])
        filters_inner.pack(fill="x", padx=12, pady=12)

        tk.Label(filters_inner, text="Search:", bg=self.colors["card"], fg=self.colors["text"], font=("Segoe UI", 10)).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        search_entry = ttk.Entry(filters_inner, textvariable=self.history_search_var, width=40)
        search_entry.grid(row=0, column=1, padx=8, pady=8, sticky="w")
        search_entry.bind("<KeyRelease>", lambda event: self.apply_history_filters())

        tk.Label(filters_inner, text="Category:", bg=self.colors["card"], fg=self.colors["text"], font=("Segoe UI", 10)).grid(row=0, column=2, padx=8, pady=8, sticky="w")
        self.history_category_combo = ttk.Combobox(filters_inner, textvariable=self.history_category_var, width=20, state="readonly")
        self.history_category_combo.grid(row=0, column=3, padx=8, pady=8, sticky="w")
        self.history_category_combo.bind("<<ComboboxSelected>>", lambda event: self.apply_history_filters())

        tk.Label(filters_inner, text="Status:", bg=self.colors["card"], fg=self.colors["text"], font=("Segoe UI", 10)).grid(row=0, column=4, padx=8, pady=8, sticky="w")
        self.history_status_combo = ttk.Combobox(filters_inner, textvariable=self.history_status_var, width=20, state="readonly")
        self.history_status_combo.grid(row=0, column=5, padx=8, pady=8, sticky="w")
        self.history_status_combo.bind("<<ComboboxSelected>>", lambda event: self.apply_history_filters())

        ttk.Button(filters_inner, text="Clear Filters", style="Secondary.TButton", command=self.clear_history_filters).grid(row=0, column=6, padx=8, pady=8)

        history_panel = self.create_info_panel(outer, "Recent History")
        history_panel.pack(fill="both", expand=True, pady=10)

        history_host = tk.Frame(history_panel, bg=self.colors["card"])
        history_host.pack(fill="both", expand=True, padx=12, pady=12)

        columns = ("filename", "category", "status", "timestamp")
        self.history_tree = ttk.Treeview(history_host, columns=columns, show="headings", height=22)

        self.history_tree.heading("filename", text="Filename")
        self.history_tree.heading("category", text="Category")
        self.history_tree.heading("status", text="Status")
        self.history_tree.heading("timestamp", text="Timestamp")

        self.history_tree.column("filename", width=420)
        self.history_tree.column("category", width=160)
        self.history_tree.column("status", width=170)
        self.history_tree.column("timestamp", width=190)

        scrollbar = ttk.Scrollbar(history_host, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=scrollbar.set)

        self.history_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.history_empty_label = tk.Label(
            history_host,
            text="No history results found.\nTry changing your filters or start processing files.",
            bg=self.colors["card"],
            fg=self.colors["muted"],
            font=("Segoe UI", 12),
            justify="center",
        )

    def build_tools_page(self):
        page = self.pages["tools"]

        outer = tk.Frame(page, bg=self.colors["bg"])
        outer.pack(fill="both", expand=True, padx=20, pady=20)

        tools_panel = self.create_info_panel(outer, "Management Tools")
        tools_panel.pack(fill="x", pady=10)

        tools_inner = tk.Frame(tools_panel, bg=self.colors["card"])
        tools_inner.pack(fill="x", padx=12, pady=12)

        ttk.Button(tools_inner, text="Open Log File", style="Secondary.TButton", command=self.open_log_file).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        ttk.Button(tools_inner, text="View Logs", style="Primary.TButton", command=self.open_logs_viewer).grid(row=0, column=1, padx=8, pady=8, sticky="w")
        ttk.Button(tools_inner, text="Open History File", style="Secondary.TButton", command=self.open_history_file).grid(row=0, column=2, padx=8, pady=8, sticky="w")
        ttk.Button(tools_inner, text="Open Reports Folder", style="Secondary.TButton", command=self.open_reports_folder).grid(row=0, column=3, padx=8, pady=8, sticky="w")
        ttk.Button(tools_inner, text="Export Config", style="Secondary.TButton", command=self.export_config).grid(row=1, column=0, padx=8, pady=8, sticky="w")
        ttk.Button(tools_inner, text="Import Config", style="Secondary.TButton", command=self.import_config).grid(row=1, column=1, padx=8, pady=8, sticky="w")
        ttk.Button(tools_inner, text="About", style="Primary.TButton", command=self.open_about_dialog).grid(row=1, column=2, padx=8, pady=8, sticky="w")

        startup_check = ttk.Checkbutton(
            tools_inner,
            text="Run at Windows startup",
            variable=self.run_at_startup_var,
            command=self.toggle_startup_setting,
        )
        startup_check.grid(row=2, column=0, padx=8, pady=12, sticky="w")

        tray_info = tk.Label(
            tools_inner,
            text="Closing the window will minimize the app to tray.",
            bg=self.colors["card"],
            fg=self.colors["muted"],
            font=("Segoe UI", 10),
        )
        tray_info.grid(row=2, column=1, columnspan=3, padx=8, pady=12, sticky="w")

        danger_panel = self.create_info_panel(outer, "Danger Zone")
        danger_panel.pack(fill="x", pady=10)

        danger_inner = tk.Frame(danger_panel, bg=self.colors["card"])
        danger_inner.pack(fill="x", padx=12, pady=12)

        ttk.Button(danger_inner, text="Reset Statistics", style="Secondary.TButton", command=self.reset_stats).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        ttk.Button(danger_inner, text="Reset Hash Database", style="Secondary.TButton", command=self.reset_hash_db).grid(row=0, column=1, padx=8, pady=8, sticky="w")
        ttk.Button(danger_inner, text="Exit Application", style="Danger.TButton", command=self.exit_application).grid(row=0, column=2, padx=8, pady=8, sticky="w")

        tk.Label(
            danger_inner,
            text="Warning: reset actions cannot be undone.",
            bg=self.colors["card"],
            fg=self.colors["danger"],
            font=("Segoe UI", 10, "bold"),
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=8, pady=10)

    def open_logs_viewer(self):
        if self.logs_window and self.logs_window.winfo_exists():
            self.logs_window.lift()
            self.logs_window.focus_force()
            self.refresh_logs_viewer()
            return

        self.logs_window = tk.Toplevel(self.root)
        self.logs_window.title(f"{APP_NAME} Logs Viewer")
        self.logs_window.geometry("1100x700")
        self.logs_window.minsize(900, 500)

        icon_path = self.get_icon_path()
        if icon_path is not None:
            try:
                self.logs_window.iconbitmap(str(icon_path))
            except Exception:
                pass

        self.logs_window.configure(bg=self.colors["bg"])
        self.logs_window.protocol("WM_DELETE_WINDOW", self.close_logs_viewer)

        header = tk.Frame(self.logs_window, bg=self.colors["panel"])
        header.pack(fill="x")

        header_title = tk.Label(
            header,
            text="Logs Viewer",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 16, "bold"),
        )
        header_title.pack(anchor="w", padx=16, pady=(12, 2))

        header_subtitle = tk.Label(
            header,
            text="Search, filter, auto-refresh, export, and inspect log lines inside the app.",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 10),
        )
        header_subtitle.pack(anchor="w", padx=16, pady=(0, 12))

        controls = tk.Frame(self.logs_window, bg=self.colors["bg"])
        controls.pack(fill="x", padx=16, pady=12)

        tk.Label(controls, text="Search:", bg=self.colors["bg"], fg=self.colors["text"], font=("Segoe UI", 10)).grid(row=0, column=0, padx=6, pady=6, sticky="w")
        search_entry = ttk.Entry(controls, textvariable=self.logs_search_var, width=35)
        search_entry.grid(row=0, column=1, padx=6, pady=6, sticky="w")
        search_entry.bind("<KeyRelease>", lambda event: self.filter_logs_viewer())

        tk.Label(controls, text="Level:", bg=self.colors["bg"], fg=self.colors["text"], font=("Segoe UI", 10)).grid(row=0, column=2, padx=6, pady=6, sticky="w")
        self.logs_level_combo = ttk.Combobox(
            controls,
            textvariable=self.logs_level_var,
            width=16,
            state="readonly",
            values=["All", "INFO", "WARNING", "ERROR", "DEBUG"],
        )
        self.logs_level_combo.grid(row=0, column=3, padx=6, pady=6, sticky="w")
        self.logs_level_combo.bind("<<ComboboxSelected>>", lambda event: self.filter_logs_viewer())

        ttk.Button(controls, text="Refresh", style="Primary.TButton", command=self.refresh_logs_viewer).grid(row=0, column=4, padx=6, pady=6)
        ttk.Button(controls, text="Clear View", style="Secondary.TButton", command=self.clear_logs_viewer).grid(row=0, column=5, padx=6, pady=6)
        ttk.Button(controls, text="Export Displayed Logs", style="Secondary.TButton", command=self.export_displayed_logs).grid(row=0, column=6, padx=6, pady=6)
        ttk.Button(controls, text="Copy Selected / Current Line", style="Secondary.TButton", command=self.copy_selected_or_current_log_line).grid(row=0, column=7, padx=6, pady=6)

        auto_refresh_check = ttk.Checkbutton(
            controls,
            text="Auto Refresh",
            variable=self.logs_auto_refresh_var,
            command=self.toggle_logs_auto_refresh,
        )
        auto_refresh_check.grid(row=1, column=0, padx=6, pady=8, sticky="w")

        line_count_label = tk.Label(
            controls,
            textvariable=self.logs_line_count_var,
            bg=self.colors["bg"],
            fg=self.colors["muted"],
            font=("Segoe UI", 10, "bold"),
        )
        line_count_label.grid(row=1, column=1, padx=6, pady=8, sticky="w")

        viewer_frame = tk.Frame(self.logs_window, bg=self.colors["border"])
        viewer_frame.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        inner_viewer = tk.Frame(viewer_frame, bg=self.colors["panel"])
        inner_viewer.pack(fill="both", expand=True, padx=1, pady=1)

        text_frame = tk.Frame(inner_viewer, bg=self.colors["panel"])
        text_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.logs_text_widget = tk.Text(
            text_frame,
            wrap="none",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            insertbackground=self.colors["text"],
            relief="flat",
            font=("Consolas", 10),
            undo=False,
        )

        self.logs_text_widget.tag_configure("INFO", foreground=self.colors["log_info"])
        self.logs_text_widget.tag_configure("WARNING", foreground=self.colors["log_warning"])
        self.logs_text_widget.tag_configure("ERROR", foreground=self.colors["log_error"])
        self.logs_text_widget.tag_configure("DEBUG", foreground=self.colors["log_debug"])
        self.logs_text_widget.tag_configure("DEFAULT", foreground=self.colors["text"])

        y_scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.logs_text_widget.yview)
        x_scroll = ttk.Scrollbar(text_frame, orient="horizontal", command=self.logs_text_widget.xview)

        self.logs_text_widget.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.logs_text_widget.pack(side="left", fill="both", expand=True)
        y_scroll.pack(side="right", fill="y")
        x_scroll.pack(side="bottom", fill="x")

        self.refresh_logs_viewer()

    def close_logs_viewer(self):
        self.logs_auto_refresh_var.set(False)
        if self.logs_auto_refresh_job is not None:
            try:
                self.root.after_cancel(self.logs_auto_refresh_job)
            except Exception:
                pass
            self.logs_auto_refresh_job = None

        if self.logs_window and self.logs_window.winfo_exists():
            self.logs_window.destroy()

        self.logs_window = None
        self.logs_text_widget = None

    def refresh_logs_viewer(self):
        if not self.logs_window or not self.logs_window.winfo_exists():
            return

        self.load_logs_into_viewer()
        self.filter_logs_viewer()

    def load_logs_into_viewer(self):
        log_path = Path(self.config["log_file"])

        if not log_path.exists():
            self.logs_displayed_content = ""
            if self.logs_text_widget:
                self.logs_text_widget.config(state="normal")
                self.logs_text_widget.delete("1.0", tk.END)
                self.logs_text_widget.insert("1.0", "Log file does not exist yet.")
                self.logs_text_widget.config(state="disabled")
            self.logs_line_count_var.set("Lines: 0")
            return

        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as file:
                content = file.read()

            self.logs_displayed_content = content

        except Exception as error:
            self.logs_displayed_content = f"Failed to read log file:\n{error}"

    def filter_logs_viewer(self):
        if not self.logs_text_widget:
            return

        search_text = self.logs_search_var.get().strip().lower()
        selected_level = self.logs_level_var.get()

        lines = self.logs_displayed_content.splitlines()

        filtered_lines = []
        for line in lines:
            if selected_level != "All" and selected_level not in line:
                continue

            if search_text and search_text not in line.lower():
                continue

            filtered_lines.append(line)

        self.logs_text_widget.config(state="normal")
        self.logs_text_widget.delete("1.0", tk.END)

        for line in filtered_lines:
            tag = "DEFAULT"
            if " ERROR " in line or "| ERROR |" in line or "ERROR" in line:
                tag = "ERROR"
            elif " WARNING " in line or "| WARNING |" in line or "WARNING" in line:
                tag = "WARNING"
            elif " DEBUG " in line or "| DEBUG |" in line or "DEBUG" in line:
                tag = "DEBUG"
            elif " INFO " in line or "| INFO |" in line or "INFO" in line:
                tag = "INFO"

            self.logs_text_widget.insert(tk.END, line + "\n", tag)

        self.logs_text_widget.config(state="disabled")
        self.logs_line_count_var.set(f"Lines: {len(filtered_lines)}")

    def clear_logs_viewer(self):
        if not self.logs_text_widget:
            return

        self.logs_text_widget.config(state="normal")
        self.logs_text_widget.delete("1.0", tk.END)
        self.logs_text_widget.config(state="disabled")
        self.logs_line_count_var.set("Lines: 0")
        self.toast_manager.show_toast("Logs viewer cleared.", "info")

    def export_displayed_logs(self):
        if not self.logs_text_widget:
            return

        try:
            content = self.logs_text_widget.get("1.0", tk.END).strip()
            if not content:
                messagebox.showwarning("Warning", "There are no displayed logs to export.")
                return

            target_path = filedialog.asksaveasfilename(
                title="Export Displayed Logs",
                defaultextension=".txt",
                filetypes=[("Text Files", "*.txt"), ("Log Files", "*.log"), ("All Files", "*.*")],
                initialfile="filtered_logs.txt",
            )
            if not target_path:
                return

            with open(target_path, "w", encoding="utf-8") as file:
                file.write(content)

            self.toast_manager.show_toast("Displayed logs exported successfully.", "success")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to export displayed logs:\n{error}")

    def copy_selected_or_current_log_line(self):
        if not self.logs_text_widget:
            return

        try:
            selected_text = self.logs_text_widget.selection_get().strip()
            if selected_text:
                self.root.clipboard_clear()
                self.root.clipboard_append(selected_text)
                self.toast_manager.show_toast("Selected log text copied.", "success")
                return
        except Exception:
            pass

        try:
            current_index = self.logs_text_widget.index("insert")
            line_start = f"{current_index.split('.')[0]}.0"
            line_end = f"{current_index.split('.')[0]}.end"
            current_line = self.logs_text_widget.get(line_start, line_end).strip()

            if not current_line:
                messagebox.showwarning("Warning", "No selected text or current line to copy.")
                return

            self.root.clipboard_clear()
            self.root.clipboard_append(current_line)
            self.toast_manager.show_toast("Current log line copied.", "success")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to copy log line:\n{error}")

    def toggle_logs_auto_refresh(self):
        if self.logs_auto_refresh_var.get():
            self.schedule_logs_auto_refresh()
            self.toast_manager.show_toast("Logs auto refresh enabled.", "info")
        else:
            if self.logs_auto_refresh_job is not None:
                try:
                    self.root.after_cancel(self.logs_auto_refresh_job)
                except Exception:
                    pass
                self.logs_auto_refresh_job = None
            self.toast_manager.show_toast("Logs auto refresh disabled.", "warning")

    def schedule_logs_auto_refresh(self):
        if not self.logs_auto_refresh_var.get():
            return

        if not self.logs_window or not self.logs_window.winfo_exists():
            self.logs_auto_refresh_var.set(False)
            return

        self.refresh_logs_viewer()
        self.logs_auto_refresh_job = self.root.after(2000, self.schedule_logs_auto_refresh)

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

    def start_monitoring(self):
        if self.monitor.is_running:
            return

        def run_monitor():
            try:
                self.monitor.start()
            except Exception as error:
                messagebox.showerror("Error", f"Failed to start monitoring:\n{error}")

        self.monitor_thread = threading.Thread(target=run_monitor, daemon=True)
        self.monitor_thread.start()

        self.status_var.set("Running")
        self.header_status.config(text="Running", fg=self.colors["success"])
        self.status_bar_var.set("Monitoring started.")
        self.start_button.state(["disabled"])
        self.stop_button.state(["!disabled"])
        self.toast_manager.show_toast("Monitoring started.", "success")
        self.update_last_file()

    def stop_monitoring(self):
        if not self.monitor.is_running:
            return

        self.monitor.stop()
        self.status_var.set("Stopped")
        self.header_status.config(text="Stopped", fg=self.colors["danger"])
        self.status_bar_var.set("Monitoring stopped.")
        self.start_button.state(["!disabled"])
        self.stop_button.state(["disabled"])
        self.config, self.monitor = build_monitor()
        self.toast_manager.show_toast("Monitoring stopped.", "warning")

    def exit_application(self):
        try:
            if self.monitor.is_running:
                self.monitor.stop()
        except Exception:
            pass

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

    def update_rules_count(self):
        self.rules_count_var.set(str(len(self.config.get("rules", {}))))

    def browse_source_folder(self):
        folder = filedialog.askdirectory(title="Select Incoming Folder")
        if folder:
            self.source_folder_var.set(folder)

    def browse_organized_folder(self):
        folder = filedialog.askdirectory(title="Select Organized Base Folder")
        if folder:
            self.organized_base_var.set(folder)

    def refresh_stats(self):
        stats_path = Path(self.config["stats_file"])
        if not stats_path.exists():
            return

        try:
            with open(stats_path, "r", encoding="utf-8") as file:
                stats = json.load(file)

            self.total_files_var.set(str(stats.get("total_files", 0)))
            self.failed_files_var.set(str(stats.get("failed", 0)))
            self.documents_var.set(str(stats.get("documents", 0)))

            duplicate_count = 0
            history_path = Path(self.config["history_file"])
            if history_path.exists():
                with open(history_path, "r", encoding="utf-8") as file:
                    reader = csv.DictReader(file)
                    for row in reader:
                        if row.get("status") == "duplicate_skipped":
                            duplicate_count += 1
            self.duplicates_var.set(str(duplicate_count))

            self.stats_text.config(state="normal")
            self.stats_text.delete("1.0", tk.END)

            for key, value in stats.items():
                self.stats_text.insert(tk.END, f"{key}: {value}\n")

            self.stats_text.config(state="disabled")
            self.status_bar_var.set("Statistics refreshed.")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to load stats:\n{error}")

    def refresh_history(self):
        history_path = Path(self.config["history_file"])

        if not history_path.exists():
            self.history_rows_cache = []
            self.update_history_filters_options()
            self.apply_history_filters()
            return

        try:
            with open(history_path, "r", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                self.history_rows_cache = list(reader)[-500:]

            self.update_history_filters_options()
            self.apply_history_filters()
            self.status_bar_var.set("History refreshed.")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to load history:\n{error}")

    def update_history_filters_options(self):
        categories = sorted({row.get("category", "") for row in self.history_rows_cache if row.get("category", "")})
        statuses = sorted({row.get("status", "") for row in self.history_rows_cache if row.get("status", "")})

        self.history_category_combo["values"] = ["All"] + categories
        self.history_status_combo["values"] = ["All"] + statuses

        if self.history_category_var.get() not in self.history_category_combo["values"]:
            self.history_category_var.set("All")
        if self.history_status_var.get() not in self.history_status_combo["values"]:
            self.history_status_var.set("All")

    def apply_history_filters(self):
        search_text = self.history_search_var.get().strip().lower()
        selected_category = self.history_category_var.get()
        selected_status = self.history_status_var.get()

        for item in self.history_tree.get_children():
            self.history_tree.delete(item)

        filtered_rows = []

        for row in self.history_rows_cache:
            filename = row.get("filename", "")
            category = row.get("category", "")
            status = row.get("status", "")
            timestamp = row.get("timestamp", "")

            if search_text:
                searchable = f"{filename} {category} {status} {timestamp}".lower()
                if search_text not in searchable:
                    continue

            if selected_category != "All" and category != selected_category:
                continue

            if selected_status != "All" and status != selected_status:
                continue

            filtered_rows.append(row)

        if filtered_rows:
            self.history_empty_label.pack_forget()
            for row in filtered_rows:
                self.history_tree.insert(
                    "",
                    tk.END,
                    values=(
                        row.get("filename", ""),
                        row.get("category", ""),
                        row.get("status", ""),
                        row.get("timestamp", ""),
                    ),
                )
        else:
            self.history_empty_label.pack(expand=True)

    def clear_history_filters(self):
        self.history_search_var.set("")
        self.history_category_var.set("All")
        self.history_status_var.set("All")
        self.apply_history_filters()
        self.toast_manager.show_toast("History filters cleared.", "info")

    def update_last_file(self):
        self.last_file_var.set(self.monitor.event_handler.last_processed_file)
        self.refresh_stats()
        self.refresh_history()

        if self.monitor.is_running:
            self.root.after(2000, self.update_last_file)

    def validate_rules(self, rules: dict):
        errors = []
        extension_to_category = {}

        for category, extensions in rules.items():
            category_name = category.strip()

            if not category_name:
                errors.append("Found an empty category name.")
                continue

            if not extensions:
                errors.append(f"Category '{category_name}' has no extensions.")
                continue

            for ext in extensions:
                if not ext.startswith("."):
                    errors.append(f"Extension '{ext}' in category '{category_name}' must start with a dot.")
                    continue

                if len(ext) < 2:
                    errors.append(f"Extension '{ext}' in category '{category_name}' is invalid.")
                    continue

                if ext in extension_to_category:
                    previous_category = extension_to_category[ext]
                    errors.append(
                        f"Extension '{ext}' is duplicated between '{previous_category}' and '{category_name}'."
                    )
                else:
                    extension_to_category[ext] = category_name

        return errors

    def normalize_extensions(self, extensions_text: str):
        extensions = [ext.strip() for ext in extensions_text.split(",") if ext.strip()]
        normalized = []
        seen = set()

        for ext in extensions:
            if not ext.startswith("."):
                ext = f".{ext}"
            ext = ext.lower().strip()
            if ext not in seen:
                normalized.append(ext)
                seen.add(ext)

        return normalized

    def collect_rules_from_editor(self):
        updated_rules = {}

        for category, entry_var in self.rule_entries.items():
            raw_value = entry_var.get().strip()
            normalized_extensions = self.normalize_extensions(raw_value)
            updated_rules[category] = normalized_extensions

        return updated_rules

    def validate_rules_preview(self):
        try:
            rules = self.collect_rules_from_editor()
            errors = self.validate_rules(rules)

            if errors:
                self.toast_manager.show_toast("Rules validation failed.", "error")
                messagebox.showerror("Validation Errors", "\n".join(errors))
                self.status_bar_var.set("Rules validation failed.")
            else:
                self.toast_manager.show_toast("Rules are valid.", "success")
                self.status_bar_var.set("Rules validation passed.")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to validate rules:\n{error}")

    def render_rule_entries(self):
        for widget in self.rules_inner_frame.winfo_children():
            widget.destroy()

        self.rule_entries = {}

        tk.Label(self.rules_inner_frame, text="Category", bg=self.colors["card"], fg=self.colors["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        tk.Label(self.rules_inner_frame, text="Extensions", bg=self.colors["card"], fg=self.colors["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=1, padx=8, pady=8, sticky="w")
        tk.Label(self.rules_inner_frame, text="Action", bg=self.colors["card"], fg=self.colors["text"], font=("Segoe UI", 10, "bold")).grid(row=0, column=2, padx=8, pady=8, sticky="w")

        for index, (category, extensions) in enumerate(self.config.get("rules", {}).items(), start=1):
            tk.Label(self.rules_inner_frame, text=category, bg=self.colors["card"], fg=self.colors["text"], font=("Segoe UI", 10)).grid(row=index, column=0, padx=8, pady=6, sticky="w")

            entry_var = tk.StringVar(value=", ".join(extensions))
            ttk.Entry(self.rules_inner_frame, textvariable=entry_var, width=80).grid(row=index, column=1, padx=8, pady=6, sticky="w")

            self.rule_entries[category] = entry_var

            ttk.Button(
                self.rules_inner_frame,
                text="Delete",
                style="Secondary.TButton",
                command=lambda c=category: self.delete_rule(c),
            ).grid(row=index, column=2, padx=8, pady=6, sticky="w")

    def add_new_rule(self):
        category = self.new_rule_name_var.get().strip()
        extensions_text = self.new_rule_extensions_var.get().strip()

        if not category:
            messagebox.showerror("Error", "Category name cannot be empty.")
            return

        if category in self.config.get("rules", {}):
            messagebox.showerror("Error", "This category already exists.")
            return

        normalized_extensions = self.normalize_extensions(extensions_text)
        if not normalized_extensions:
            messagebox.showerror("Error", "Please enter at least one valid extension.")
            return

        candidate_rules = dict(self.config["rules"])
        candidate_rules[category] = normalized_extensions

        errors = self.validate_rules(candidate_rules)
        if errors:
            messagebox.showerror("Validation Errors", "\n".join(errors))
            return

        self.config["rules"][category] = normalized_extensions
        self.new_rule_name_var.set("")
        self.new_rule_extensions_var.set("")
        self.render_rule_entries()
        self.update_rules_count()
        self.status_bar_var.set(f"Added new rule category: {category}")
        self.toast_manager.show_toast(f"Added rule '{category}'.", "success")

    def delete_rule(self, category):
        confirm = messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete the rule category '{category}'?",
        )
        if not confirm:
            return

        if category in self.config.get("rules", {}):
            del self.config["rules"][category]
            self.render_rule_entries()
            self.update_rules_count()
            self.status_bar_var.set(f"Deleted rule category: {category}")
            self.toast_manager.show_toast(f"Deleted rule '{category}'.", "warning")

    def save_rules(self):
        try:
            config_path = get_config_path()

            with open(config_path, "r", encoding="utf-8") as file:
                config_data = json.load(file)

            updated_rules = self.collect_rules_from_editor()
            errors = self.validate_rules(updated_rules)

            if errors:
                messagebox.showerror("Validation Errors", "\n".join(errors))
                self.status_bar_var.set("Rules were not saved due to validation errors.")
                self.toast_manager.show_toast("Rules save failed.", "error")
                return

            config_data["rules"] = updated_rules

            with open(config_path, "w", encoding="utf-8") as file:
                json.dump(config_data, file, indent=2, ensure_ascii=False)

            self.status_bar_var.set("Rules saved successfully.")
            self.toast_manager.show_toast("Rules saved successfully.", "success")
            self.reload_rules()

        except Exception as error:
            messagebox.showerror("Error", f"Failed to save rules:\n{error}")

    def reload_rules(self):
        try:
            with open("config/config.json", "r", encoding="utf-8") as file:
                config_data = json.load(file)

            self.config["rules"] = config_data.get("rules", {})
            self.render_rule_entries()
            self.update_rules_count()
            self.status_bar_var.set("Rules reloaded successfully.")
            self.toast_manager.show_toast("Rules reloaded.", "info")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to reload rules:\n{error}")

    def save_settings(self):
        try:
            config_path = get_config_path()

            with open(config_path, "r", encoding="utf-8") as file:
                config_data = json.load(file)

            config_data["source_folder"] = self.source_folder_var.get().strip()
            config_data["organized_base_folder"] = self.organized_base_var.get().strip()
            config_data["processing_wait_seconds"] = int(self.processing_wait_var.get())
            config_data["duplicate_event_window_seconds"] = int(self.duplicate_window_var.get())
            config_data["archive_by_date"] = bool(self.archive_by_date_var.get())
            config_data["run_at_startup"] = bool(self.run_at_startup_var.get())

            with open(config_path, "w", encoding="utf-8") as file:
                json.dump(config_data, file, indent=2, ensure_ascii=False)

            self.status_bar_var.set("Settings saved successfully.")
            self.toast_manager.show_toast("Settings saved successfully.", "success")
            self.reload_settings()

        except ValueError:
            messagebox.showerror("Error", "Please enter valid numeric values.")
        except Exception as error:
            messagebox.showerror("Error", f"Failed to save settings:\n{error}")

    def reload_settings(self):
        try:
            was_running = self.monitor.is_running
            if was_running:
                self.monitor.stop()

            self.config, self.monitor = build_monitor()

            self.source_folder_var.set(self.config.get("source_folder", "incoming"))
            self.organized_base_var.set(self.config.get("organized_base_folder", "organized"))
            self.processing_wait_var.set(str(self.config.get("processing_wait_seconds", 5)))
            self.duplicate_window_var.set(str(self.config.get("duplicate_event_window_seconds", 3)))
            self.archive_by_date_var.set(self.config.get("archive_by_date", False))
            self.run_at_startup_var.set(self.config.get("run_at_startup", False))

            self.refresh_stats()
            self.refresh_history()
            self.render_rule_entries()
            self.update_rules_count()

            self.status_var.set("Stopped")
            self.header_status.config(text="Stopped", fg=self.colors["danger"])
            self.start_button.state(["!disabled"])
            self.stop_button.state(["disabled"])
            self.status_bar_var.set("Settings reloaded successfully.")

            self.toast_manager.show_toast("Settings reloaded.", "info")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to reload settings:\n{error}")

    def validate_imported_config(self, config_data: dict):
        required_keys = [
            "source_folder",
            "organized_base_folder",
            "rules",
            "log_file",
            "stats_file",
            "history_file",
            "hash_db_file",
        ]

        missing = [key for key in required_keys if key not in config_data]
        if missing:
            return [f"Missing required keys: {', '.join(missing)}"]

        rules = config_data.get("rules", {})
        return self.validate_rules(rules)

    def export_config(self):
        try:
            source_path = Path("config/config.json")
            if not source_path.exists():
                messagebox.showerror("Error", "config.json not found.")
                return

            target_path = filedialog.asksaveasfilename(
                title="Export Config",
                defaultextension=".json",
                filetypes=[("JSON Files", "*.json")],
                initialfile="file_automation_config_backup.json",
            )

            if not target_path:
                return

            shutil.copyfile(source_path, target_path)
            self.status_bar_var.set("Config exported successfully.")
            self.toast_manager.show_toast("Config exported successfully.", "success")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to export config:\n{error}")

    def import_config(self):
        try:
            selected_file = filedialog.askopenfilename(
                title="Import Config",
                filetypes=[("JSON Files", "*.json")],
            )

            if not selected_file:
                return

            with open(selected_file, "r", encoding="utf-8") as file:
                imported_config = json.load(file)

            errors = self.validate_imported_config(imported_config)
            if errors:
                messagebox.showerror("Import Validation Failed", "\n".join(errors))
                self.toast_manager.show_toast("Config import failed.", "error")
                return

            confirm = messagebox.askyesno(
                "Confirm Import",
                "Importing a config will replace the current configuration.\nDo you want to continue?",
            )
            if not confirm:
                return

            config_path = get_config_path()
            with open(config_path, "w", encoding="utf-8") as file:
                json.dump(imported_config, file, indent=2, ensure_ascii=False)

            self.reload_settings()
            self.reload_rules()
            self.status_bar_var.set("Config imported successfully.")
            self.toast_manager.show_toast("Config imported successfully.", "success")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to import config:\n{error}")

    def toggle_startup_setting(self):
        try:
            config_path = get_config_path()
            with open(config_path, "r", encoding="utf-8") as file:
                config_data = json.load(file)

            enabled = bool(self.run_at_startup_var.get())
            config_data["run_at_startup"] = enabled

            with open(config_path, "w", encoding="utf-8") as file:
                json.dump(config_data, file, indent=2, ensure_ascii=False)

            if enabled:
                enable_startup()
                self.toast_manager.show_toast("Run at startup enabled.", "success")
            else:
                disable_startup()
                self.toast_manager.show_toast("Run at startup disabled.", "warning")

            self.status_bar_var.set("Startup setting updated.")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to update startup setting:\n{error}")

    def reset_stats(self):
        confirm = messagebox.askyesno(
            "Confirm Reset",
            "Are you sure you want to reset all statistics?",
        )
        if not confirm:
            return

        try:
            stats_path = Path(self.config["stats_file"])
            default_stats = {"total_files": 0, "failed": 0}

            for category in self.config.get("rules", {}).keys():
                default_stats[category] = 0

            if "others" not in default_stats:
                default_stats["others"] = 0

            with open(stats_path, "w", encoding="utf-8") as file:
                json.dump(default_stats, file, indent=2, ensure_ascii=False)

            self.refresh_stats()
            self.status_bar_var.set("Statistics reset successfully.")
            self.toast_manager.show_toast("Statistics reset successfully.", "warning")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to reset stats:\n{error}")

    def reset_hash_db(self):
        confirm = messagebox.askyesno(
            "Confirm Reset",
            "Are you sure you want to reset the hash database?\nThis will remove duplicate tracking history.",
        )
        if not confirm:
            return

        try:
            hash_db_path = Path(self.config["hash_db_file"])
            with open(hash_db_path, "w", encoding="utf-8") as file:
                json.dump({}, file, indent=2, ensure_ascii=False)

            self.status_bar_var.set("Hash database reset successfully.")
            self.toast_manager.show_toast("Hash database reset successfully.", "warning")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to reset hash database:\n{error}")

    def open_folder(self, folder_path: str):
        path = Path(folder_path).resolve()
        if path.exists():
            os.startfile(path)
            self.status_bar_var.set(f"Opened folder: {path}")
        else:
            messagebox.showwarning("Warning", f"Folder does not exist:\n{path}")

    def open_log_file(self):
        path = Path(self.config["log_file"]).resolve()
        if path.exists():
            os.startfile(path)
            self.status_bar_var.set("Opened log file.")
        else:
            messagebox.showwarning("Warning", f"Log file does not exist:\n{path}")

    def open_history_file(self):
        path = Path(self.config["history_file"]).resolve()
        if path.exists():
            os.startfile(path)
            self.status_bar_var.set("Opened history file.")
        else:
            messagebox.showwarning("Warning", f"History file does not exist:\n{path}")

    def open_reports_folder(self):
        path = Path("reports").resolve()
        if path.exists():
            os.startfile(path)
            self.status_bar_var.set("Opened reports folder.")
        else:
            messagebox.showwarning("Warning", f"Reports folder does not exist:\n{path}")


def launch_gui():
    root = tk.Tk()
    app = FileAutomationGUI(root)
    root.mainloop()