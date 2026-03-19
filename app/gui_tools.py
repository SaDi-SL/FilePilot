import os
import tkinter as tk
import tkinter.simpledialog as simpledialog
from tkinter import messagebox, ttk

from app.config_loader import get_plugins_dir
from app.main import build_monitor



def _add_tooltip(widget, text):
    """Simple tooltip shown on widget hover."""
    tip_window = [None]
    def show_tip(event):
        if tip_window[0] or not text:
            return
        x = widget.winfo_rootx() + 20
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        tw = tk.Toplevel(widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)
        tk.Label(tw, text=text, background="#1e293b", foreground="#f8fafc",
                 font=("Segoe UI", 9), relief="flat", padx=8, pady=4).pack()
        tip_window[0] = tw
    def hide_tip(event):
        tw = tip_window[0]; tip_window[0] = None
        if tw:
            try: tw.destroy()
            except: pass
    widget.bind("<Enter>", show_tip)
    widget.bind("<Leave>", hide_tip)

def build_tools_page(self):
    page = self.pages["tools"]

    page_container = tk.Frame(page, bg=self.colors["bg"])
    page_container.pack(fill="both", expand=True)

    canvas = tk.Canvas(page_container, bg=self.colors["bg"], highlightthickness=0)
    scrollbar = ttk.Scrollbar(page_container, orient="vertical", command=canvas.yview)
    scrollable_frame = tk.Frame(canvas, bg=self.colors["bg"])
    scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    self.attach_safe_mousewheel(canvas, owner=page)
    outer = scrollable_frame

    def _tb(parent, text, cmd, tip="", primary=False):
        b = tk.Button(parent, text=text,
                      bg=self.colors["accent"] if primary else self.colors["panel_2"],
                      fg="white" if primary else self.colors["muted"],
                      activebackground=self.colors["accent_2"] if primary else self.colors["border_2"],
                      activeforeground="white" if primary else self.colors["text"],
                      relief="flat", bd=0, padx=12, pady=6,
                      font=("Segoe UI", 9, "bold") if primary else ("Segoe UI", 9),
                      cursor="hand2", command=cmd)
        if tip:
            _add_tooltip(b, tip)
        return b

    # ── Management Tools ──────────────────────────────────────
    tools_panel = self.create_info_panel(outer, "Management Tools")
    tools_panel.pack(fill="x", padx=20, pady=(14, 0))
    ti = tk.Frame(tools_panel, bg=self.colors["card"])
    ti.pack(fill="x", padx=14, pady=12)

    r1 = tk.Frame(ti, bg=self.colors["card"])
    r1.pack(fill="x", pady=(0, 6))
    _tb(r1, "📄  Log File",       self.open_log_file,      "Open the automation log").pack(side="left", padx=(0,6))
    _tb(r1, "📋  History File",   self.open_history_file,   "Open history CSV").pack(side="left", padx=(0,6))
    _tb(r1, "📁  Reports Folder", self.open_reports_folder, "Open reports folder").pack(side="left", padx=(0,6))
    _tb(r1, "i  About",          self.open_about_dialog,   "App version & info", primary=True).pack(side="right")

    r2 = tk.Frame(ti, bg=self.colors["card"])
    r2.pack(fill="x")
    _tb(r2, "^  Export Config", self.export_config,     "Save backup of config.json").pack(side="left", padx=(0,6))
    _tb(r2, "v  Import Config", self.import_config,     "Load settings from file").pack(side="left", padx=(0,6))
    _tb(r2, "*  Setup Wizard",  self.open_welcome_wizard, "Re-run first-time wizard").pack(side="left", padx=(0,6))

    hint_bar = tk.Frame(ti, bg=self.colors["card_2"],
                        highlightbackground=self.colors["border"], highlightthickness=1)
    hint_bar.pack(fill="x", pady=(10, 0))

    hint_inner = tk.Frame(hint_bar, bg=self.colors["card_2"])
    hint_inner.pack(fill="x", padx=12, pady=6)

    tk.Label(hint_inner, text="i  Closing the window minimizes FilePilot to the system tray.",
             bg=self.colors["card_2"], fg=self.colors["muted"],
             font=("Segoe UI", 9), anchor="w").pack(fill="x")

    # Headless mode launch shortcut
    headless_row = tk.Frame(hint_inner, bg=self.colors["card_2"])
    headless_row.pack(fill="x", pady=(4, 0))

    tk.Label(headless_row, text="i  Headless mode (tray only):  ",
             bg=self.colors["card_2"], fg=self.colors["muted"],
             font=("Segoe UI", 9), anchor="w").pack(side="left")

    tk.Label(headless_row, text="python run.py --headless",
             bg=self.colors["card_2"], fg=self.colors["stat_blue"],
             font=("Consolas", 9), anchor="w").pack(side="left")

    # ===== Plugin Marketplace =====
    mkt_panel = self.create_info_panel(outer, "Plugin Marketplace")
    mkt_panel.pack(fill="x", padx=20, pady=(10, 0))

    mkt_inner = tk.Frame(mkt_panel, bg=self.colors["card"])
    mkt_inner.pack(fill="x", padx=14, pady=12)

    # Header row
    mkt_header = tk.Frame(mkt_inner, bg=self.colors["card"])
    mkt_header.pack(fill="x", pady=(0, 8))

    self.mkt_status_var = tk.StringVar(value="Click 'Browse' to load available plugins.")
    tk.Label(mkt_header, textvariable=self.mkt_status_var,
             bg=self.colors["card"], fg=self.colors["muted"],
             font=("Segoe UI", 9)).pack(side="left")

    def _browse_marketplace():
        self.mkt_status_var.set("Fetching registry...")
        self._load_marketplace_registry()

    tk.Button(mkt_header, text="o  Browse Plugins",
              bg=self.colors["accent"], fg="white",
              activebackground=self.colors["accent_2"], activeforeground="white",
              relief="flat", bd=0, padx=12, pady=5,
              font=("Segoe UI", 9, "bold"), cursor="hand2",
              command=_browse_marketplace).pack(side="right")

    # Plugin cards container
    self.mkt_cards_frame = tk.Frame(mkt_inner, bg=self.colors["card"])
    self.mkt_cards_frame.pack(fill="x")

    # ===== Plugin Manager =====
    plugins_panel = self.create_info_panel(outer, "Plugin Manager")
    plugins_panel.pack(fill="both", expand=True, padx=20, pady=(10, 0))

    plugins_inner = tk.Frame(plugins_panel, bg=self.colors["card"])
    plugins_inner.pack(fill="both", expand=True, padx=12, pady=12)

    top_bar = tk.Frame(plugins_inner, bg=self.colors["card"])
    top_bar.pack(fill="x", pady=(0, 10))

    tk.Label(
        top_bar,
        text="Loaded Plugins:",
        bg=self.colors["card"],
        fg=self.colors["text"],
        font=("Segoe UI", 10, "bold"),
    ).pack(side="left")

    tk.Label(
        top_bar,
        textvariable=self.plugins_loaded_count_var,
        bg=self.colors["card"],
        fg=self.colors["success"],
        font=("Segoe UI", 10, "bold"),
    ).pack(side="left", padx=(6, 20))

    tk.Label(
        top_bar,
        text="Failed Plugins:",
        bg=self.colors["card"],
        fg=self.colors["text"],
        font=("Segoe UI", 10, "bold"),
    ).pack(side="left")

    tk.Label(
        top_bar,
        textvariable=self.plugins_failed_count_var,
        bg=self.colors["card"],
        fg=self.colors["stat_red"],
        font=("Segoe UI", 10, "bold"),
    ).pack(side="left", padx=(6, 20))

    def _pb(parent, text, cmd, primary=False):
        return tk.Button(parent, text=text,
                         bg=self.colors["accent"] if primary else self.colors["panel_2"],
                         fg="white" if primary else self.colors["muted"],
                         activebackground=self.colors["accent_2"] if primary else self.colors["border_2"],
                         activeforeground="white" if primary else self.colors["text"],
                         relief="flat", bd=0, padx=12, pady=5,
                         font=("Segoe UI", 9, "bold") if primary else ("Segoe UI", 9),
                         cursor="hand2", command=cmd)

    _pb(top_bar, "Create Plugin Template", self.create_plugin_template).pack(side="right", padx=(4,0))
    _pb(top_bar, "Reload Plugins",         self.reload_plugins_from_gui, primary=True).pack(side="right", padx=4)
    _pb(top_bar, "Open Plugins Folder",    self.open_plugins_folder).pack(side="right", padx=(0,4))

    loaded_label = tk.Label(
        plugins_inner,
        text="Loaded Plugins",
        bg=self.colors["card"],
        fg=self.colors["text"],
        font=("Segoe UI", 11, "bold"),
    )
    loaded_label.pack(anchor="w", pady=(0, 6))

    loaded_columns = ("name", "version", "description", "status")
    self.plugins_tree = ttk.Treeview(
        plugins_inner,
        columns=loaded_columns,
        show="headings",
        height=6,
    )

    self.plugins_tree.heading("name", text="Name")
    self.plugins_tree.heading("version", text="Version")
    self.plugins_tree.heading("description", text="Description")
    self.plugins_tree.heading("status", text="Status")

    self.plugins_tree.column("name", width=180)
    self.plugins_tree.column("version", width=100)
    self.plugins_tree.column("description", width=420)
    self.plugins_tree.column("status", width=100)

    self.plugins_tree.pack(fill="x", pady=(0, 14))

    failed_label = tk.Label(
        plugins_inner,
        text="Failed Plugins",
        bg=self.colors["card"],
        fg=self.colors["text"],
        font=("Segoe UI", 11, "bold"),
    )
    failed_label.pack(anchor="w", pady=(0, 6))

    failed_columns = ("file", "reason")
    self.failed_plugins_tree = ttk.Treeview(
        plugins_inner,
        columns=failed_columns,
        show="headings",
        height=4,
    )

    self.failed_plugins_tree.heading("file", text="Plugin File")
    self.failed_plugins_tree.heading("reason", text="Failure Reason")

    self.failed_plugins_tree.column("file", width=220)
    self.failed_plugins_tree.column("reason", width=580)

    self.failed_plugins_tree.pack(fill="x")

    # ===== Auto Backup =====
    backup_panel = self.create_info_panel(outer, "Auto Backup")
    backup_panel.pack(fill="x", padx=20, pady=(10, 0))

    bi = tk.Frame(backup_panel, bg=self.colors["card"])
    bi.pack(fill="x", padx=14, pady=12)

    # Status row
    status_row = tk.Frame(bi, bg=self.colors["card"])
    status_row.pack(fill="x", pady=(0, 8))

    self.backup_status_var = tk.StringVar(value="Checking...")

    tk.Label(status_row, text="Next backup:",
             bg=self.colors["card"], fg=self.colors["muted"],
             font=("Segoe UI", 9), width=14, anchor="w").pack(side="left")

    tk.Label(status_row, textvariable=self.backup_status_var,
             bg=self.colors["card"], fg=self.colors["text"],
             font=("Segoe UI", 9, "bold")).pack(side="left")

    def _refresh_backup_status():
        try:
            if hasattr(self, "backup_manager"):
                days = self.backup_manager.days_until_next()
                if days == 0:
                    self.backup_status_var.set("Due now")
                elif days == 1:
                    self.backup_status_var.set("Tomorrow")
                else:
                    self.backup_status_var.set(f"In {int(days)} days")
                # Update backup list
                _refresh_backup_list()
        except Exception:
            self.backup_status_var.set("Unknown")

    # Backup list frame
    self.backup_list_frame = tk.Frame(bi, bg=self.colors["card_2"],
                                      highlightbackground=self.colors["border"],
                                      highlightthickness=1)
    self.backup_list_frame.pack(fill="x", pady=(0, 8))

    def _refresh_backup_list():
        for w in self.backup_list_frame.winfo_children():
            w.destroy()
        try:
            backups = self.backup_manager.list_backups() if hasattr(self, "backup_manager") else []
            if not backups:
                tk.Label(self.backup_list_frame,
                         text="  No backups yet.",
                         bg=self.colors["card_2"], fg=self.colors["muted"],
                         font=("Segoe UI", 9), padx=12, pady=6, anchor="w").pack(fill="x")
                return
            for entry in backups[:5]:
                row = tk.Frame(self.backup_list_frame, bg=self.colors["card_2"])
                row.pack(fill="x", padx=12, pady=2)
                tk.Label(row, text="o",
                         bg=self.colors["card_2"], fg=self.colors["stat_green"],
                         font=("Segoe UI", 8)).pack(side="left", padx=(0, 8))
                tk.Label(row, text=entry["created"].strftime("%Y-%m-%d  %H:%M"),
                         bg=self.colors["card_2"], fg=self.colors["text"],
                         font=("Consolas", 9)).pack(side="left")
        except Exception:
            pass

    _refresh_backup_list()

    # Buttons row
    btn_row2 = tk.Frame(bi, bg=self.colors["card"])
    btn_row2.pack(fill="x")

    def _run_backup_now():
        if not hasattr(self, "backup_manager"):
            return
        ok, msg = self.backup_manager.run_now()
        _refresh_backup_status()
        if ok:
            self.toast_manager.show_toast(msg, "success")
        else:
            self.toast_manager.show_toast(msg, "error")

    tk.Button(btn_row2, text="^ Backup Now",
              bg=self.colors["accent"], fg="white",
              activebackground=self.colors["accent_2"], activeforeground="white",
              relief="flat", bd=0, padx=12, pady=6,
              font=("Segoe UI", 9, "bold"), cursor="hand2",
              command=_run_backup_now).pack(side="left", padx=(0, 6))

    tk.Button(btn_row2, text="o  Refresh",
              bg=self.colors["panel_2"], fg=self.colors["muted"],
              activebackground=self.colors["border_2"], activeforeground=self.colors["text"],
              relief="flat", bd=0, padx=12, pady=6,
              font=("Segoe UI", 9), cursor="hand2",
              command=_refresh_backup_status).pack(side="left", padx=(0, 6))

    tk.Label(bi, text="i  Keeps last 5 backups. Runs automatically every 7 days.",
             bg=self.colors["card"], fg=self.colors["muted"],
             font=("Segoe UI", 8), pady=4, anchor="w").pack(fill="x")

    # Trigger status refresh
    self.root.after(500, _refresh_backup_status)

    # ===== Danger Zone =====
    danger_panel = self.create_info_panel(outer, "Danger Zone")
    danger_panel.pack(fill="x", padx=20, pady=(10, 16))

    di = tk.Frame(danger_panel, bg=self.colors["card"])
    di.pack(fill="x", padx=14, pady=12)

    # Warning bar
    warn = tk.Frame(di, bg=self.colors["danger"],
                    highlightbackground=self.colors["danger_border"],
                    highlightthickness=1)
    warn.pack(fill="x", pady=(0, 10))
    tk.Label(warn, text="!  These actions cannot be undone.",
             bg=self.colors["danger"], fg=self.colors["danger_fg"],
             font=("Segoe UI", 9, "bold"), padx=12, pady=6, anchor="w").pack(fill="x")

    def _dbtn(p, txt, cmd, tip=""):
        b = tk.Button(p, text=txt,
                      bg=self.colors["danger"], fg=self.colors["danger_fg"],
                      activebackground=self.colors["danger_2"],
                      activeforeground=self.colors["danger_fg"],
                      relief="flat", bd=0,
                      highlightthickness=0,
                      padx=14, pady=7,
                      font=("Segoe UI", 9, "bold"), cursor="hand2", command=cmd)
        if tip:
            _add_tooltip(b, tip)
        return b

    dr = tk.Frame(di, bg=self.colors["card"])
    dr.pack(fill="x")
    _dbtn(dr, "o  Reset Statistics",    self.reset_stats,     "Clear all stats counters").pack(side="left", padx=(0,6))
    _dbtn(dr, "o  Reset Hash Database", self.reset_hash_db,   "Remove duplicate history").pack(side="left", padx=(0,6))
    _dbtn(dr, "x  Exit Application",   self.exit_application, "Close FilePilot").pack(side="left")

def reload_plugins_from_gui(self):
    try:
        was_running = self.monitor.is_running
        if was_running:
            self._stop_dot_pulse()
            self._stop_auto_refresh()
            self.monitor.stop()

        self.config, self.monitor = build_monitor()

        self.refresh_plugins_view()
        self.refresh_stats()
        self.refresh_history()

        if was_running:
            self.monitor.set_file_processed_callback(self._make_live_callback())
            self.monitor.start()
            self.status_var.set("Running")
            self.header_status.config(text="Running", fg=self.colors["success"])
            self.start_button.state(["disabled"])
            self.stop_button.state(["!disabled"])
            self._start_dot_pulse()
            self._start_auto_refresh()
            self.status_bar_var.set("Plugins reloaded — monitoring restarted.")
        else:
            self.status_var.set("Stopped")
            self.header_status.config(text="Stopped", fg=self.colors["danger"])
            self.start_button.state(["!disabled"])
            self.stop_button.state(["disabled"])
            self.status_bar_var.set("Plugins reloaded successfully.")

        self.toast_manager.show_toast("Plugins reloaded successfully.", "success")
        self.add_notification("success", "Plugins Reloaded", "Plugins reloaded successfully.")

    except Exception as error:
        messagebox.showerror("Error", f"Failed to reload plugins:\n{error}")
        self.add_notification("error", "Plugins Reload Failed", str(error))


def open_plugins_folder(self):
    try:
        plugins_dir = get_plugins_dir()
        plugins_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(plugins_dir)
        self.status_bar_var.set("Opened plugins folder.")
        self.add_notification("info", "Plugins Folder", "Opened plugins folder.")
    except Exception as error:
        messagebox.showerror("Error", f"Failed to open plugins folder:\n{error}")
        self.add_notification("error", "Plugins Folder Error", str(error))


def create_plugin_template(self):
    try:
        plugin_name = simpledialog.askstring(
            "Create Plugin",
            "Enter plugin name (example: image_sorter):"
        )

        if not plugin_name:
            return

        plugin_name = plugin_name.strip().lower().replace(" ", "_")

        plugins_dir = get_plugins_dir()
        plugins_dir.mkdir(parents=True, exist_ok=True)

        plugin_file = plugins_dir / f"{plugin_name}.py"

        if plugin_file.exists():
            messagebox.showerror("Error", "Plugin already exists.")
            return

        template = f'''from pathlib import Path

PLUGIN_NAME = "{plugin_name.replace("_", " ").title()}"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "Describe what this plugin does."


def process(file_path: Path, context: dict) -> str | None:
    """
    file_path: path of the file being processed
    context: additional info from FilePilot
    return category name or None
    """

    name = file_path.name.lower()
    suffix = file_path.suffix.lower()

    # Example rule
    if suffix == ".txt" and "note" in name:
        return "notes"

    return None
'''

        plugin_file.write_text(template, encoding="utf-8")
        os.startfile(plugin_file)

        self.status_bar_var.set("Plugin template created.")
        self.toast_manager.show_toast("Plugin template created.", "success")
        self.add_notification("success", "Plugin Template", f"Created plugin template: {plugin_file.name}")

    except Exception as error:
        messagebox.showerror("Error", f"Failed to create plugin template:\n{error}")
        self.add_notification("error", "Plugin Template Error", str(error))
        
def toggle_run_at_startup(self):
    """Delegates to toggle_startup_setting which handles the actual registry/shortcut logic."""
    self.toggle_startup_setting()