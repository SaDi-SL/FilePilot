import os
import tkinter as tk
import tkinter.simpledialog as simpledialog
from tkinter import messagebox, ttk

from app.config_loader import get_plugins_dir
from app.main import build_monitor


def _tooltip(widget, text):
    tip = [None]
    def show(e):
        if tip[0]: return
        x = widget.winfo_rootx() + 20
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        tw = tk.Toplevel(widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)
        tk.Label(tw, text=text, bg="#1e293b", fg="#f8fafc",
                 font=("Segoe UI", 9), relief="flat", padx=8, pady=4).pack()
        tip[0] = tw
    def hide(e):
        tw = tip[0]; tip[0] = None
        if tw:
            try: tw.destroy()
            except: pass
    widget.bind("<Enter>", show)
    widget.bind("<Leave>", hide)


def build_tools_page(self):
    page = self.pages["tools"]

    # Style notebook tabs
    style = ttk.Style()
    style.configure("FP.TNotebook",
                    background=self.colors["panel"],
                    tabmargins=[0, 0, 0, 0],
                    borderwidth=0)
    style.configure("FP.TNotebook.Tab",
                    background=self.colors["panel"],
                    foreground=self.colors["muted"],
                    padding=[24, 10],
                    font=("Segoe UI", 10),
                    borderwidth=0)
    style.map("FP.TNotebook.Tab",
              background=[("selected", self.colors["bg"]),
                          ("active",   self.colors["panel_2"])],
              foreground=[("selected", self.colors["text"]),
                          ("active",   self.colors["text"])])

    nb = ttk.Notebook(page, style="FP.TNotebook")
    nb.pack(fill="both", expand=True, padx=0, pady=0)

    # Create 3 scrollable tab frames
    def _scrollable(parent):
        c = tk.Canvas(parent, bg=self.colors["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(parent, orient="vertical", command=c.yview)
        sf = tk.Frame(c, bg=self.colors["bg"])

        # Key: when canvas resizes, resize inner frame to match
        def _on_canvas_resize(event):
            c.itemconfig(win_id, width=event.width)

        sf.bind("<Configure>", lambda e: c.configure(scrollregion=c.bbox("all")))
        win_id = c.create_window((0, 0), window=sf, anchor="nw")
        c.bind("<Configure>", _on_canvas_resize)
        c.configure(yscrollcommand=sb.set)
        c.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.attach_safe_mousewheel(c, owner=parent)
        return sf

    ai_tab  = tk.Frame(nb, bg=self.colors["bg"])
    sys_tab = tk.Frame(nb, bg=self.colors["bg"])
    plg_tab = tk.Frame(nb, bg=self.colors["bg"])

    nb.add(ai_tab,  text="  AI Tools  ")
    nb.add(sys_tab, text="   System   ")
    nb.add(plg_tab, text="  Plugins   ")

    _build_ai_tab(self,      _scrollable(ai_tab))
    _build_system_tab(self,  _scrollable(sys_tab))
    _build_plugins_tab(self, _scrollable(plg_tab))


# ─────────────────────────────────────────────────────────────────────────────
def _section(parent, colors, title):
    """Create a titled section panel that fills full width."""
    wrapper = tk.Frame(parent, bg=colors["bg"])
    wrapper.pack(fill="x", padx=0, pady=(0, 2))

    header = tk.Frame(wrapper, bg=colors["panel_2"])
    header.pack(fill="x")
    tk.Label(header, text=title.upper(),
             bg=colors["panel_2"], fg=colors["muted"],
             font=("Segoe UI", 8, "bold"),
             padx=16, pady=7, anchor="w").pack(fill="x")

    body = tk.Frame(wrapper, bg=colors["card"],
                    highlightbackground=colors["border"],
                    highlightthickness=1)
    body.pack(fill="x")
    return body


def _btn(parent, colors, text, cmd, primary=False, danger=False, small=False):
    if danger:
        bg, fg, abg = colors["danger"], colors["danger_fg"], colors["danger"]
    elif primary:
        bg, fg, abg = colors["accent"], "white", colors["accent_2"]
    else:
        bg, fg, abg = colors["panel_2"], colors["muted"], colors["border_2"]
    font = ("Segoe UI", 8 if small else 9, "bold" if primary or danger else "normal")
    return tk.Button(parent, text=text, bg=bg, fg=fg,
                     activebackground=abg, activeforeground="white" if primary or danger else colors["text"],
                     relief="flat", bd=0, padx=10 if small else 14,
                     pady=4 if small else 6, font=font, cursor="hand2", command=cmd)


# ─────────────────────────────────────────────────────────────────────────────
def _build_ai_tab(self, outer):
    # Description bar
    desc = tk.Frame(outer, bg=self.colors["card_2"],
                    highlightbackground=self.colors["border"], highlightthickness=1)
    desc.pack(fill="x", padx=0, pady=(0, 2))
    tk.Label(desc,
             text="Use AI to analyze documents, extract important dates, and get smart suggestions.",
             bg=self.colors["card_2"], fg=self.colors["muted"],
             font=("Segoe UI", 9), padx=14, pady=8, anchor="w",
             wraplength=800, justify="left").pack(fill="x")

    # AI Status
    status_body = _section(outer, self.colors, "AI Status")
    sr = tk.Frame(status_body, bg=self.colors["card"])
    sr.pack(fill="x", padx=14, pady=10)
    dot_c = tk.Canvas(sr, width=10, height=10, bg=self.colors["card"], highlightthickness=0)
    dot_c.pack(side="left", padx=(0, 8))
    dot_oval = dot_c.create_oval(1, 1, 9, 9, fill=self.colors["muted"], outline="")
    self.ai_tab_status_var = tk.StringVar(value="Checking AI connection...")
    tk.Label(sr, textvariable=self.ai_tab_status_var,
             bg=self.colors["card"], fg=self.colors["text"],
             font=("Segoe UI", 9)).pack(side="left")

    def _check_ai():
        try:
            from app.ai_classifier import AIClassifier
            ai_cfg = self.config.get("ai", {})
            ai = AIClassifier(provider=ai_cfg.get("provider","ollama"),
                              claude_api_key=ai_cfg.get("claude_api_key",""))
            active = ai.get_active_provider()
            if active == "none":
                dot_c.itemconfig(dot_oval, fill=self.colors["stat_red"])
                self.ai_tab_status_var.set("Not connected — Enable in Settings > AI Classification")
            else:
                dot_c.itemconfig(dot_oval, fill=self.colors["stat_green"])
                model = ai_cfg.get("ollama_model","mistral")
                self.ai_tab_status_var.set(f"Connected via {active}" + (f" ({model})" if active=="ollama" else ""))
        except Exception as e:
            dot_c.itemconfig(dot_oval, fill=self.colors["stat_red"])
            self.ai_tab_status_var.set(f"Error: {e}")
    self.root.after(600, _check_ai)

    # Action cards
    actions_body = _section(outer, self.colors, "What can AI do for you?")
    for title, desc_txt, btn_txt, cmd, color in [
        ("Analyze a Document",
         "Open any file — AI reads the content, identifies dates, extracts key info, and suggests where to save it.",
         "Choose File...", self.analyze_file_with_ai, "accent"),
        ("Suggest Smart Rules",
         "AI analyzes your file history and recommends new rules based on your patterns.",
         "Analyze History", self.suggest_ai_rules, "stat_purple"),
    ]:
        card = tk.Frame(actions_body, bg=self.colors["card_2"],
                        highlightbackground=self.colors["border"], highlightthickness=1)
        card.pack(fill="x", padx=16, pady=(8, 0))
        info = tk.Frame(card, bg=self.colors["card_2"])
        info.pack(side="left", fill="both", expand=True, padx=14, pady=10)
        tk.Label(info, text=title, bg=self.colors["card_2"], fg=self.colors["text"],
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tk.Label(info, text=desc_txt, bg=self.colors["card_2"], fg=self.colors["muted"],
                 font=("Segoe UI", 8), wraplength=600, justify="left").pack(anchor="w", pady=(2,0))
        tk.Button(card, text=btn_txt, bg=self.colors[color], fg="white",
                  activebackground=self.colors[color], relief="flat", bd=0,
                  padx=14, pady=8, font=("Segoe UI", 9, "bold"),
                  cursor="hand2", command=cmd).pack(side="right", padx=14, pady=10)

    # Bottom padding card
    tk.Frame(actions_body, bg=self.colors["card"], height=8).pack(fill="x")

    hint = tk.Frame(outer, bg=self.colors["card_2"],
                    highlightbackground=self.colors["border"], highlightthickness=1)
    hint.pack(fill="x", padx=0, pady=(2, 0))
    tk.Label(hint, text="i  Configure AI provider (Ollama / Claude API) in Settings > AI Classification",
             bg=self.colors["card_2"], fg=self.colors["muted"],
             font=("Segoe UI", 8), padx=14, pady=8, anchor="w").pack(fill="x")


# ─────────────────────────────────────────────────────────────────────────────
def _build_system_tab(self, outer):
    # Files & Reports
    fb = _section(outer, self.colors, "Files & Reports")
    fr = tk.Frame(fb, bg=self.colors["card"])
    fr.pack(fill="x", padx=14, pady=10)
    for txt, cmd, tip in [
        ("Log File",       self.open_log_file,      "Open automation log"),
        ("History File",   self.open_history_file,   "Open history CSV"),
        ("Reports Folder", self.open_reports_folder, "Open reports folder"),
    ]:
        b = _btn(fr, self.colors, txt, cmd)
        b.pack(side="left", padx=(0, 6))
        _tooltip(b, tip)
    ab = _btn(fr, self.colors, "About", self.open_about_dialog, primary=True)
    ab.pack(side="right")
    _tooltip(ab, "App version & info")

    # Configuration
    cb = _section(outer, self.colors, "Configuration")
    cr = tk.Frame(cb, bg=self.colors["card"])
    cr.pack(fill="x", padx=14, pady=10)
    for txt, cmd, tip in [
        ("Export Config", self.export_config,       "Save backup of settings"),
        ("Import Config", self.import_config,       "Load settings from file"),
        ("Setup Wizard",  self.open_welcome_wizard, "Re-run first-time wizard"),
    ]:
        b = _btn(cr, self.colors, txt, cmd)
        b.pack(side="left", padx=(0, 6))
        _tooltip(b, tip)

    # Startup & Background
    sb2 = _section(outer, self.colors, "Startup & Background")
    for txt in [
        "i  Closing the window minimizes FilePilot to the system tray.",
    ]:
        tk.Label(sb2, text=txt, bg=self.colors["card"], fg=self.colors["muted"],
                 font=("Segoe UI", 9), padx=14, pady=6, anchor="w").pack(fill="x")
    hl = tk.Frame(sb2, bg=self.colors["card"])
    hl.pack(fill="x", padx=14, pady=(0, 8))
    tk.Label(hl, text="Headless mode:", bg=self.colors["card"],
             fg=self.colors["muted"], font=("Segoe UI", 9)).pack(side="left", padx=(0, 8))
    tk.Label(hl, text="python run.py --headless", bg=self.colors["card"],
             fg=self.colors["stat_blue"], font=("Consolas", 9)).pack(side="left")

    # Auto Backup
    bb = _section(outer, self.colors, "Auto Backup")
    br = tk.Frame(bb, bg=self.colors["card"])
    br.pack(fill="x", padx=14, pady=(8, 0))
    tk.Label(br, text="Next backup:", bg=self.colors["card"],
             fg=self.colors["muted"], font=("Segoe UI", 9), width=14, anchor="w").pack(side="left")
    self.backup_status_var = tk.StringVar(value="Checking...")
    tk.Label(br, textvariable=self.backup_status_var, bg=self.colors["card"],
             fg=self.colors["text"], font=("Segoe UI", 9, "bold")).pack(side="left")

    self.backup_list_frame = tk.Frame(bb, bg=self.colors["card"])
    self.backup_list_frame.pack(fill="x", padx=14, pady=(6, 0))

    def _refresh():
        for w in self.backup_list_frame.winfo_children(): w.destroy()
        try:
            backups = self.backup_manager.list_backups() if hasattr(self, "backup_manager") else []
            if not backups:
                tk.Label(self.backup_list_frame, text="No backups yet.",
                         bg=self.colors["card"], fg=self.colors["muted"],
                         font=("Segoe UI", 8), anchor="w").pack(fill="x")
                return
            for b in backups[:5]:
                row = tk.Frame(self.backup_list_frame, bg=self.colors["card"])
                row.pack(fill="x", pady=1)
                tk.Label(row, text="●", bg=self.colors["card"],
                         fg=self.colors["stat_green"], font=("Segoe UI", 7)).pack(side="left", padx=(0,6))
                tk.Label(row, text=b["created"].strftime("%Y-%m-%d  %H:%M"),
                         bg=self.colors["card"], fg=self.colors["text"],
                         font=("Consolas", 9)).pack(side="left")
        except Exception: pass

    def _refresh_status():
        try:
            if hasattr(self, "backup_manager"):
                days = self.backup_manager.days_until_next()
                self.backup_status_var.set("Due now" if days==0 else "Tomorrow" if days==1 else f"In {int(days)} days")
                _refresh()
        except Exception:
            self.backup_status_var.set("Unknown")

    def _backup_now():
        if not hasattr(self, "backup_manager"): return
        ok, msg = self.backup_manager.run_now()
        _refresh_status()
        self.toast_manager.show_toast(msg, "success" if ok else "error")

    _refresh()
    bbtns = tk.Frame(bb, bg=self.colors["card"])
    bbtns.pack(fill="x", padx=14, pady=8)
    _btn(bbtns, self.colors, "Backup Now", _backup_now, primary=True).pack(side="left", padx=(0,6))
    _btn(bbtns, self.colors, "Refresh", _refresh_status).pack(side="left")
    tk.Label(bb, text="i  Keeps last 5 backups. Runs automatically every 7 days.",
             bg=self.colors["card"], fg=self.colors["muted"],
             font=("Segoe UI", 8), padx=14, pady=6, anchor="w").pack(fill="x", pady=(0, 4))
    self.root.after(500, _refresh_status)

    # Danger Zone
    db = _section(outer, self.colors, "Danger Zone")
    warn = tk.Frame(db, bg=self.colors["danger"],
                    highlightbackground=self.colors["danger_border"], highlightthickness=1)
    warn.pack(fill="x", padx=14, pady=(8, 6))
    tk.Label(warn, text="!  These actions cannot be undone.",
             bg=self.colors["danger"], fg=self.colors["danger_fg"],
             font=("Segoe UI", 9, "bold"), padx=10, pady=6, anchor="w").pack(fill="x")
    dr = tk.Frame(db, bg=self.colors["card"])
    dr.pack(fill="x", padx=14, pady=(0, 10))
    _btn(dr, self.colors, "Reset Statistics",    self.reset_stats,       danger=True).pack(side="left", padx=(0,6))
    _btn(dr, self.colors, "Reset Hash Database", self.reset_hash_db,     danger=True).pack(side="left", padx=(0,6))
    _btn(dr, self.colors, "Exit Application",    self.exit_application,  danger=True).pack(side="left")

    tk.Frame(outer, bg=self.colors["bg"], height=12).pack()


# ─────────────────────────────────────────────────────────────────────────────
def _build_plugins_tab(self, outer):
    # Marketplace
    mb = _section(outer, self.colors, "Plugin Marketplace")
    mh = tk.Frame(mb, bg=self.colors["card"])
    mh.pack(fill="x", padx=14, pady=10)
    self.mkt_status_var = tk.StringVar(value="Browse available plugins from the community.")
    tk.Label(mh, textvariable=self.mkt_status_var, bg=self.colors["card"],
             fg=self.colors["muted"], font=("Segoe UI", 9)).pack(side="left")
    def _browse():
        self.mkt_status_var.set("Fetching registry...")
        self._load_marketplace_registry()
    _btn(mh, self.colors, "Browse Plugins", _browse, primary=True).pack(side="right")
    self.mkt_cards_frame = tk.Frame(mb, bg=self.colors["card"])
    self.mkt_cards_frame.pack(fill="x", padx=14, pady=(0, 8))

    # Installed Plugins
    ib = _section(outer, self.colors, "Installed Plugins")
    ih = tk.Frame(ib, bg=self.colors["card"])
    ih.pack(fill="x", padx=14, pady=8)

    # Stats
    stats_row = tk.Frame(ih, bg=self.colors["card"])
    stats_row.pack(fill="x", pady=(0, 8))
    for lbl, var, color in [
        ("Loaded:", "plugins_loaded_count_var", "stat_green"),
        ("Failed:", "plugins_failed_count_var", "stat_red"),
    ]:
        tk.Label(stats_row, text=lbl, bg=self.colors["card"],
                 fg=self.colors["muted"], font=("Segoe UI", 9)).pack(side="left")
        tk.Label(stats_row, textvariable=getattr(self, var),
                 bg=self.colors["card"], fg=self.colors[color],
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=(4, 16))

    # Buttons
    btn_row = tk.Frame(ih, bg=self.colors["card"])
    btn_row.pack(fill="x")
    _btn(btn_row, self.colors, "Reload Plugins",   self.reload_plugins_from_gui, primary=True).pack(side="left", padx=(0,6))
    _btn(btn_row, self.colors, "Open Folder",      self.open_plugins_folder).pack(side="left", padx=(0,6))
    _btn(btn_row, self.colors, "Create Template",  self.create_plugin_template).pack(side="left")

    # Loaded table
    tk.Frame(ib, bg=self.colors["border"], height=1).pack(fill="x", padx=14)
    tk.Label(ib, text="Loaded", bg=self.colors["card"], fg=self.colors["stat_green"],
             font=("Segoe UI", 9, "bold"), padx=14, anchor="w").pack(fill="x", pady=(8,0))
    cols = ("name","version","description","status")
    self.plugins_tree = ttk.Treeview(ib, columns=cols, show="headings", height=5)
    for col, w in [("name",140),("version",70),("description",400),("status",80)]:
        self.plugins_tree.heading(col, text=col.title())
        self.plugins_tree.column(col, width=w, stretch=(col=="description"))
    self.plugins_tree.pack(fill="x", padx=14, pady=(4, 0))

    # Failed table
    tk.Label(ib, text="Failed", bg=self.colors["card"], fg=self.colors["stat_red"],
             font=("Segoe UI", 9, "bold"), padx=14, anchor="w").pack(fill="x", pady=(8,0))
    fcols = ("file","reason")
    self.failed_plugins_tree = ttk.Treeview(ib, columns=fcols, show="headings", height=3)
    self.failed_plugins_tree.heading("file",   text="Plugin File")
    self.failed_plugins_tree.heading("reason", text="Failure Reason")
    self.failed_plugins_tree.column("file",   width=200)
    self.failed_plugins_tree.column("reason", width=490, stretch=True)
    self.failed_plugins_tree.pack(fill="x", padx=14, pady=(4, 14))

    tk.Frame(outer, bg=self.colors["bg"], height=12).pack()


# ─────────────────────────────────────────────────────────────────────────────
def reload_plugins_from_gui(self):
    try:
        was_running = self.monitor.is_running
        if was_running:
            try: self._stop_dot_pulse()
            except Exception: pass
            try: self._stop_auto_refresh()
            except Exception: pass
            self.monitor.stop_all()

        self.config, self.monitor = build_monitor()
        self.monitor.set_file_processed_callback(self._make_live_callback())

        if was_running:
            self.monitor.start_all()
            try:
                self.header_status.config(text="Running",
                                          bg=self.colors["success_bg"],
                                          fg=self.colors["success"])
                self._status_badge.config(bg=self.colors["success_bg"],
                                          highlightbackground=self.colors["success_border"])
                self._status_dot.config(bg=self.colors["success_bg"])
            except Exception: pass
            try: self.start_button.config(state="disabled")
            except Exception: pass
            try: self.stop_button.config(state="normal")
            except Exception: pass
            try: self._start_dot_pulse()
            except Exception: pass
            try: self._start_auto_refresh()
            except Exception: pass

        self.refresh_plugins_view()
        self.add_notification("info", "Plugins Reloaded", "Plugins reloaded successfully.")
        self.toast_manager.show_toast("Plugins reloaded.", "success")

    except Exception as error:
        self.add_notification("error", "Plugins Reload Failed", str(error))
        messagebox.showerror("Error", f"Failed to reload plugins:\n{error}")


def open_plugins_folder(self):
    d = get_plugins_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.startfile(str(d))


def create_plugin_template(self):
    d = get_plugins_dir()
    d.mkdir(parents=True, exist_ok=True)
    name = simpledialog.askstring("Plugin Name", "Enter plugin name (no spaces):")
    if not name: return
    name = name.strip().replace(" ","_").lower()
    path = d / f"{name}.py"
    if path.exists():
        messagebox.showwarning("Exists", f"Plugin '\'{name}.py\'' already exists.")
        return
    path.write_text(
        f'''NAME = "{name}"
VERSION = "1.0.0"
DESCRIPTION = "Describe what this plugin does."

def process(file_path, context):
    """Return a category string or None."""
    return None
''', encoding="utf-8")
    self.toast_manager.show_toast(f"Created: {name}.py", "success")
    os.startfile(str(d))


def toggle_run_at_startup(self):
    pass