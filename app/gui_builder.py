"""
gui_builder.py — UI layout and widget construction for FilePilot.
Mixin class: BuilderMixin
"""
import tkinter as tk
from tkinter import filedialog, ttk

from app.branding import APP_NAME, APP_TAGLINE, APP_VERSION, APP_DEVELOPER
from app.i18n import t


class BuilderMixin:
    """Builds all UI layout: sidebar, header, pages, stat boxes, panels."""

    def create_layout(self):
        self.main_frame = tk.Frame(
            self.root,
            bg=self.colors["bg"],
            bd=0,
            highlightthickness=0
        )
        self.main_frame.pack(fill="both", expand=True, padx=0, pady=0)

        self.sidebar = tk.Frame(
            self.main_frame,
            bg=self.colors["panel"],
            width=210,
            bd=0,
            highlightbackground=self.colors["border_2"],
            highlightthickness=1,
        )
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.content_area = tk.Frame(
            self.main_frame,
            bg=self.colors["bg"],
            bd=0,
            highlightthickness=0
        )
        self.content_area.pack(side="right", fill="both", expand=True)

        self.build_sidebar()
        self.build_header()
        self.build_pages()
        self.build_status_bar()

        self.show_page("dashboard")

        # Keyboard shortcuts
        self.root.bind("<Control-r>", lambda e: self._kb_refresh())
        self.root.bind("<Control-R>", lambda e: self._kb_refresh())
        self.root.bind("<F5>",        lambda e: self._kb_refresh())
        self.root.bind("<Control-m>", lambda e: self._kb_toggle_monitor())
        self.root.bind("<Control-M>", lambda e: self._kb_toggle_monitor())
        self.root.bind("<Control-s>", lambda e: self._kb_save())
        self.root.bind("<Control-S>", lambda e: self._kb_save())

    def build_sidebar(self):
        # ── Logo area ──
        logo_frame = tk.Frame(self.sidebar, bg=self.colors["panel"])
        logo_frame.pack(fill="x", padx=18, pady=(18, 12))

        tk.Label(
            logo_frame,
            text=APP_NAME,
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor="w")

        tk.Label(
            logo_frame,
            text="Smart file automation system",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            wraplength=180,
            justify="left",
        ).pack(anchor="w", pady=(3, 0))

        tk.Frame(self.sidebar, bg=self.colors["border"], height=1).pack(fill="x")

        # ── Nav items ──
        nav_frame = tk.Frame(self.sidebar, bg=self.colors["panel"])
        nav_frame.pack(fill="x", padx=8, pady=8)

        nav_items = [
            ("dashboard",     "▦  Dashboard"),
            ("settings",      "⚙  Settings"),
            ("rules",         "◈  Rules Editor"),
            ("watch",         "=  Watch Folders"),
            ("history",       "◷  History"),
            ("notifications", "◎  Notifications"),
            ("tools",         "⚒  Tools"),
        ]

        for key, label in nav_items:
            btn = tk.Button(
                nav_frame,
                text=label,
                bg=self.colors["panel"],
                fg=self.colors["muted"],
                activebackground=self.colors["panel_2"],
                activeforeground=self.colors["text"],
                relief="flat",
                bd=0,
                padx=12,
                pady=8,
                anchor="w",
                font=("Segoe UI", 10),
                command=lambda k=key: self.show_page(k),
                cursor="hand2",
            )

            def on_enter(e, b=btn, item_key=key):
                if self.current_page != item_key:
                    b.configure(bg=self.colors["panel_2"], fg=self.colors["text"])

            def on_leave(e, b=btn, item_key=key):
                if self.current_page != item_key:
                    b.configure(bg=self.colors["panel"], fg=self.colors["muted"])

            btn.bind("<Enter>", on_enter)
            btn.bind("<Leave>", on_leave)
            btn.pack(fill="x", pady=0)
            self.nav_buttons[key] = btn

        # ── Bottom: theme toggle ──
        tk.Frame(self.sidebar, bg=self.colors["border"], height=1).pack(side="bottom", fill="x")

        bottom_frame = tk.Frame(self.sidebar, bg=self.colors["panel"])
        bottom_frame.pack(side="bottom", fill="x", padx=8, pady=8)

        moon = "◑" if self.theme_mode == "dark" else "◐"
        self.theme_button = tk.Button(
            bottom_frame,
            text=f"{moon}  Theme: {self.theme_mode.title()}",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            activebackground=self.colors["panel_2"],
            activeforeground=self.colors["text"],
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            anchor="w",
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self.toggle_theme,
        )
        self.theme_button.pack(fill="x")

    def build_header(self):
        # Header wrapper — bottom border via bg color + 1px padding
        header_wrap = tk.Frame(
            self.content_area,
            bg=self.colors["border_2"],
            bd=0,
            highlightthickness=0,
        )
        header_wrap.pack(fill="x")

        self.header = tk.Frame(
            header_wrap,
            bg=self.colors["panel"],
            bd=0,
            highlightthickness=0,
        )
        # No pack_propagate(False) — let content determine height naturally
        self.header.pack(fill="x", pady=(0, 1))

        inner = tk.Frame(self.header, bg=self.colors["panel"])
        inner.pack(fill="x", padx=24, pady=12)

        # Left: page title + subtitle
        left = tk.Frame(inner, bg=self.colors["panel"])
        left.pack(side="left", fill="y")

        self.header_title = tk.Label(
            left,
            text="Dashboard",
            bg=self.colors["panel"],
            fg=self.colors["text"],
            font=("Segoe UI", 17, "bold"),
        )
        self.header_title.pack(anchor="w")

        self.header_subtitle = tk.Label(
            left,
            text="Overview of the automation system",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Segoe UI", 10),
        )
        self.header_subtitle.pack(anchor="w", pady=(3, 0))

        # Right: status pill + exit pill
        right = tk.Frame(inner, bg=self.colors["panel"])
        right.pack(side="right", fill="y", anchor="center")

        right_inner = tk.Frame(right, bg=self.colors["panel"])
        right_inner.pack(side="right", anchor="center")

        # Exit pill — bordered
        self._exit_btn = tk.Button(
            right_inner,
            text="Exit",
            bg=self.colors["danger"],
            fg=self.colors["danger_fg"],
            activebackground=self.colors["danger_2"],
            activeforeground=self.colors["danger_fg"],
            relief="solid",
            bd=1,
            highlightbackground=self.colors["danger_border"],
            highlightcolor=self.colors["danger_border"],
            highlightthickness=1,
            padx=14,
            pady=4,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            command=self.exit_application,
        )
        self._exit_btn.pack(side="right", padx=(8, 0))

        # Status pill — bordered frame
        self._status_badge = tk.Frame(
            right_inner,
            bg=self.colors["danger"],
            highlightbackground=self.colors["danger_border"],
            highlightthickness=1,
        )
        self._status_badge.pack(side="right", padx=(0, 8))

        self._status_dot = tk.Canvas(
            self._status_badge,
            width=8, height=8,
            bg=self.colors["danger"],
            highlightthickness=0,
        )
        self._status_dot.pack(side="left", padx=(10, 3), pady=8)
        self._dot_oval = self._status_dot.create_oval(1, 1, 7, 7,
                                                       fill=self.colors["danger_fg"],
                                                       outline="")

        self.header_status = tk.Label(
            self._status_badge,
            text="Stopped",
            bg=self.colors["danger"],
            fg=self.colors["danger_fg"],
            font=("Segoe UI", 9, "bold"),
        )
        self.header_status.pack(side="left", padx=(0, 10), pady=6)

    def build_pages(self):
        self.pages_container = tk.Frame(self.content_area, bg=self.colors["bg"])
        self.pages_container.pack(fill="both", expand=True)
        
        self.pages = {
            "dashboard":     tk.Frame(self.pages_container, bg=self.colors["bg"]),
            "settings":      tk.Frame(self.pages_container, bg=self.colors["bg"]),
            "rules":         tk.Frame(self.pages_container, bg=self.colors["bg"]),
            "watch":         tk.Frame(self.pages_container, bg=self.colors["bg"]),
            "history":       tk.Frame(self.pages_container, bg=self.colors["bg"]),
            "notifications": tk.Frame(self.pages_container, bg=self.colors["bg"]),
            "tools":         tk.Frame(self.pages_container, bg=self.colors["bg"]),
        }

        for page in self.pages.values():
            page.place(relx=0, rely=0, relwidth=1, relheight=1)

        self.build_dashboard_page()
        self.build_settings_page()
        self.build_rules_page()
        self.build_watch_folders_page()
        self.build_history_page()
        self.build_notifications_page()
        self.build_tools_page()

    def build_status_bar(self):
        footer_frame = tk.Frame(self.root, bg=self.colors["panel"])
        footer_frame.pack(fill="x", side="bottom")

        tk.Frame(footer_frame, bg=self.colors["border"], height=1).pack(fill="x")

        bar_inner = tk.Frame(footer_frame, bg=self.colors["panel"])
        bar_inner.pack(fill="x")

        self.status_bar = tk.Label(
            bar_inner,
            textvariable=self.status_bar_var,
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            anchor="w",
            padx=16,
            pady=6,
            font=("Segoe UI", 9),
        )
        self.status_bar.pack(side="left")

        footer_text = f"{APP_NAME} v{APP_VERSION}  ·  {APP_DEVELOPER}"
        self.footer_bar = tk.Label(
            bar_inner,
            text=footer_text,
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            anchor="e",
            padx=16,
            pady=6,
            font=("Segoe UI", 9),
        )
        self.footer_bar.pack(side="right")

    def highlight_active_nav(self, active_key):
        for key, btn in self.nav_buttons.items():
            if key == active_key:
                btn.configure(
                    bg=self.colors["active_nav"],
                    fg=self.colors["active_nav_text"],
                    font=("Segoe UI", 10, "bold"),
                )
            else:
                btn.configure(
                    bg=self.colors["panel"],
                    fg=self.colors["muted"],
                    font=("Segoe UI", 10),
                )

    def show_page(self, page_name: str):
        self.current_page = page_name
        self.pages[page_name].tkraise()
        self.highlight_active_nav(page_name)

        titles = {
            "dashboard":     (t("nav_dashboard"),     t("sub_dashboard")),
            "settings":      (t("nav_settings"),      t("sub_settings")),
            "rules":         (t("nav_rules"),          t("sub_rules")),
            "watch":         ("Watch Folders",         "Monitor multiple folders simultaneously"),
            "history":       (t("nav_history"),        t("sub_history")),
            "notifications": (t("nav_notifications"),  t("sub_notifications")),
            "tools":         (t("nav_tools"),          t("sub_tools")),
        }

        title, subtitle = titles.get(page_name, ("Page", ""))
        self.header_title.config(text=title)
        self.header_subtitle.config(text=subtitle)

    def create_info_panel(self, parent, title):
        outer = tk.Frame(
            parent,
            bg=self.colors["border_2"],
            highlightthickness=0,
        )
        inner = tk.Frame(outer, bg=self.colors["card"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        # Panel header — slightly different bg to distinguish it
        header = tk.Frame(inner, bg=self.colors["panel_2"])
        header.pack(fill="x")

        tk.Label(
            header,
            text=title.upper(),
            bg=self.colors["panel_2"],
            fg=self.colors["muted"],
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", padx=14, pady=(9, 8))

        tk.Frame(inner, bg=self.colors["border_2"], height=1).pack(fill="x")
        return outer

    def create_stat_box(self, parent, title, value_var, color_key="stat_blue"):
        # Outer wrapper for the colored left accent border
        color = self.colors.get(color_key, self.colors["stat_blue"])
        wrapper = tk.Frame(
            parent,
            bg=color,                          # left accent strip color
            bd=0,
            highlightbackground=self.colors["border"],
            highlightthickness=1,
        )

        # Inner card (slightly inset to reveal the left strip)
        box = tk.Frame(
            wrapper,
            bg=self.colors["card"],
            padx=12,
            pady=10,
        )
        box.pack(fill="both", expand=True, padx=(3, 0))  # 3px left accent

        tk.Label(
            box,
            textvariable=value_var,
            bg=self.colors["card"],
            fg=color,
            font=("Segoe UI", 20, "bold"),
        ).pack(anchor="w")

        tk.Label(
            box,
            text=title.upper(),
            bg=self.colors["card"],
            fg=self.colors["muted"],
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(5, 0))

        return wrapper

    def attach_safe_mousewheel(self, canvas, owner=None):
        def _on_mousewheel(event):
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except tk.TclError:
                pass

        def _bind_mousewheel(_event):
            try:
                if canvas.winfo_exists():
                    canvas.bind_all("<MouseWheel>", _on_mousewheel)
            except tk.TclError:
                pass

        def _unbind_mousewheel(_event):
            try:
                canvas.unbind_all("<MouseWheel>")
            except tk.TclError:
                pass

        canvas.bind("<Enter>", _bind_mousewheel)
        canvas.bind("<Leave>", _unbind_mousewheel)

        if owner is not None:
            owner.bind(
                "<Destroy>",
                lambda _event: canvas.unbind_all("<MouseWheel>")
            )

    def _round_button(self, btn, radius=8):
        """Simulate rounded pill look by binding paint on the button canvas."""
        # Tkinter buttons can't have true border-radius,
        # but we can closely simulate with overrelief=flat + high padx.
        # For a true pill: use a Canvas-based button approach on demand.
        btn.config(overrelief="flat", cursor="hand2")

    def _round_frame(self, frame):
        """No-op placeholder — tkinter frames are square. 
        True rounding requires Canvas-based drawing or OS compositor tricks."""
        pass

    def build_settings_page(self):
        page = self.pages["settings"]

        outer = tk.Frame(page, bg=self.colors["bg"])
        outer.pack(fill="both", expand=True, padx=20, pady=16)

        def _lbl(parent, text):
            return tk.Label(parent, text=text,
                            bg=self.colors["card"], fg=self.colors["muted"],
                            font=("Segoe UI", 9), width=22, anchor="w")

        def _hint(parent, text):
            return tk.Label(parent, text=text,
                            bg=self.colors["card"], fg=self.colors["muted"],
                            font=("Segoe UI", 8), anchor="w")

        # ── Folders ──────────────────────────────────────────────
        folders_panel = self.create_info_panel(outer, "Folders")
        folders_panel.pack(fill="x", pady=(0, 10))
        fi = tk.Frame(folders_panel, bg=self.colors["card"])
        fi.pack(fill="x", padx=16, pady=14)

        for row, (label, var, cmd, hint) in enumerate([
            ("Incoming Folder",       self.source_folder_var,    self.browse_source_folder,    "Files dropped here get auto-organized"),
            ("Organized Base Folder", self.organized_base_var,   self.browse_organized_folder, "Sorted subfolders are created inside here"),
        ]):
            _lbl(fi, label + ":").grid(row=row*2,   column=0, sticky="w", padx=(0, 12), pady=(10 if row==0 else 6, 0))
            ttk.Entry(fi, textvariable=var, width=72).grid(row=row*2, column=1, sticky="ew", pady=(10 if row==0 else 6, 0))
            tk.Button(fi, text="Browse",
                      bg=self.colors["panel_2"], fg=self.colors["muted"],
                      activebackground=self.colors["border_2"], activeforeground=self.colors["text"],
                      relief="flat", bd=0, padx=12, pady=4,
                      font=("Segoe UI", 9), cursor="hand2",
                      command=cmd).grid(row=row*2, column=2, padx=(8, 0), pady=(10 if row==0 else 6, 0))
            _hint(fi, hint).grid(row=row*2+1, column=1, sticky="w", pady=(0, 4))

        fi.columnconfigure(1, weight=1)

        # ── Configuration ─────────────────────────────────────────
        config_panel = self.create_info_panel(outer, "Configuration")
        config_panel.pack(fill="x", pady=(0, 10))
        ci = tk.Frame(config_panel, bg=self.colors["card"])
        ci.pack(fill="x", padx=16, pady=14)

        for row, (label, var, hint) in enumerate([
            ("Processing wait (seconds)",        self.processing_wait_var,   "Delay before moving a new file  (1–300)"),
            ("Duplicate event window (seconds)", self.duplicate_window_var,  "Ignore duplicate filesystem events within this window  (1–60)"),
        ]):
            _lbl(ci, label + ":").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=8)
            ttk.Entry(ci, textvariable=var, width=10).grid(row=row, column=1, sticky="w", pady=8)
            _hint(ci, hint).grid(row=row, column=2, sticky="w", padx=(16, 0), pady=8)

        # Archive toggle
        toggle_row = tk.Frame(ci, bg=self.colors["card"])
        toggle_row.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 8))
        ttk.Checkbutton(toggle_row, text="Archive files by date", variable=self.archive_by_date_var).pack(side="left")
        _hint(toggle_row, "   Creates YYYY-MM subfolders inside each category").pack(side="left")

        # Startup toggle
        startup_row = tk.Frame(ci, bg=self.colors["card"])
        startup_row.grid(row=3, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Checkbutton(startup_row, text="Run at Windows startup", variable=self.run_at_startup_var,
                        command=self.toggle_startup_setting).pack(side="left")
        _hint(startup_row, "   Minimizes to tray on launch").pack(side="left")

        # ── Action buttons ────────────────────────────────────────
        # ── Language selector ─────────────────────────────────────
        lang_panel = self.create_info_panel(outer, "Language")
        lang_panel.pack(fill="x", pady=(0, 10))
        li = tk.Frame(lang_panel, bg=self.colors["card"])
        li.pack(fill="x", padx=16, pady=12)

        from app.i18n import available_languages, get_language, language_display_name
        langs = available_languages()
        lang_names = [name for _, name in langs]
        lang_codes  = [code for code, _ in langs]

        self.lang_var = tk.StringVar(value=language_display_name(get_language()))
        lang_combo = ttk.Combobox(li, textvariable=self.lang_var,
                                  values=lang_names, state="readonly", width=22)
        lang_combo.pack(side="left", padx=(0, 8))

        def _apply_lang():
            sel = self.lang_var.get()
            code = next((c for c, n in langs if n == sel), "en")
            if code != get_language():
                self.change_language(code)

        tk.Button(li, text="Apply",
                  bg=self.colors["accent"], fg="white",
                  activebackground=self.colors["accent_2"], activeforeground="white",
                  relief="flat", bd=0, padx=12, pady=5,
                  font=("Segoe UI", 9, "bold"), cursor="hand2",
                  command=_apply_lang).pack(side="left")

        tk.Label(li, text="  Rebuilds UI instantly.",
                 bg=self.colors["card"], fg=self.colors["muted"],
                 font=("Segoe UI", 8)).pack(side="left", padx=(8, 0))

        # ── Action buttons ────────────────────────────────────────
        btn_row = tk.Frame(outer, bg=self.colors["bg"])
        btn_row.pack(fill="x", pady=(4, 0))

        tk.Button(btn_row, text=t("btn_save_settings"),
                  bg=self.colors["accent"], fg="white",
                  activebackground=self.colors["accent_2"], activeforeground="white",
                  relief="flat", bd=0, padx=16, pady=7,
                  font=("Segoe UI", 9, "bold"), cursor="hand2",
                  command=self.save_settings).pack(side="left", padx=(0, 6))

        tk.Button(btn_row, text=t("btn_reload_settings"),
                  bg=self.colors["panel_2"], fg=self.colors["muted"],
                  activebackground=self.colors["border_2"], activeforeground=self.colors["text"],
                  relief="flat", bd=0, padx=14, pady=6,
                  font=("Segoe UI", 9), cursor="hand2",
                  command=self.reload_settings).pack(side="left")

    def build_rules_page(self):
        page = self.pages["rules"]

        # Scrollable page container
        page_container = tk.Frame(page, bg=self.colors["bg"])
        page_container.pack(fill="both", expand=True, padx=20, pady=20)

        canvas = tk.Canvas(
            page_container,
            bg=self.colors["bg"],
            highlightthickness=0
        )
        scrollbar = ttk.Scrollbar(
            page_container,
            orient="vertical",
            command=canvas.yview
        )

        scrollable_frame = tk.Frame(canvas, bg=self.colors["bg"])

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Mouse wheel support
        self.attach_safe_mousewheel(canvas, owner=page)

        outer = scrollable_frame

        tk.Label(
            outer,
            text="Edit file extension rules. Separate extensions with commas, for example: .jpg, .jpeg, .png",
            bg=self.colors["bg"],
            fg=self.colors["muted"],
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(0, 10))

        # -------- Current Rules --------
        rules_panel = self.create_info_panel(outer, "Current Rules")
        rules_panel.pack(fill="x", pady=10)

        rules_host = tk.Frame(rules_panel, bg=self.colors["card"])
        rules_host.pack(fill="both", expand=True, padx=12, pady=12)

        rules_canvas = tk.Canvas(rules_host, highlightthickness=0, bg=self.colors["card"], height=260)
        rules_scrollbar = ttk.Scrollbar(rules_host, orient="vertical", command=rules_canvas.yview)
        self.rules_inner_frame = tk.Frame(rules_canvas, bg=self.colors["card"])

        self.rules_inner_frame.bind(
            "<Configure>",
            lambda e: rules_canvas.configure(scrollregion=rules_canvas.bbox("all")),
        )

        rules_canvas.create_window((0, 0), window=self.rules_inner_frame, anchor="nw")
        rules_canvas.configure(yscrollcommand=rules_scrollbar.set)

        rules_canvas.pack(side="left", fill="both", expand=True)
        rules_scrollbar.pack(side="right", fill="y")

        self.render_rule_entries()

        # -------- Add New Rule --------
        add_panel = self.create_info_panel(outer, "Add New Rule Category")
        add_panel.pack(fill="x", pady=10)

        add_inner = tk.Frame(add_panel, bg=self.colors["card"])
        add_inner.pack(fill="x", padx=12, pady=12)

        tk.Label(add_inner, text="Category Name:", bg=self.colors["card"], fg=self.colors["text"], font=("Segoe UI", 10)).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        ttk.Entry(add_inner, textvariable=self.new_rule_name_var, width=28).grid(row=0, column=1, padx=8, pady=8, sticky="w")

        tk.Label(add_inner, text="Extensions:", bg=self.colors["card"], fg=self.colors["text"], font=("Segoe UI", 10)).grid(row=1, column=0, padx=8, pady=8, sticky="w")
        ttk.Entry(add_inner, textvariable=self.new_rule_extensions_var, width=78).grid(row=1, column=1, padx=8, pady=8, sticky="w")

        ttk.Button(
            add_inner,
            text="Add Rule",
            style="Primary.TButton",
            command=self.add_new_rule
        ).grid(row=0, column=2, rowspan=2, padx=10, pady=8)

        buttons = tk.Frame(outer, bg=self.colors["bg"])
        buttons.pack(fill="x", pady=10)

        ttk.Button(buttons, text="Save Rules", style="Primary.TButton", command=self.save_rules).pack(side="left", padx=6)
        ttk.Button(buttons, text="Reload Rules", style="Secondary.TButton", command=self.reload_rules).pack(side="left", padx=6)
        ttk.Button(buttons, text="Validate Rules", style="Secondary.TButton", command=self.validate_rules_preview).pack(side="left", padx=6)

        # -------- Smart Rules Editor --------
        smart_rules_panel = self.create_info_panel(outer, "Smart Rules Editor")
        smart_rules_panel.pack(fill="x", pady=10)

        smart_rules_inner = tk.Frame(smart_rules_panel, bg=self.colors["card"])
        smart_rules_inner.pack(fill="both", expand=True, padx=12, pady=12)

        smart_canvas = tk.Canvas(
            smart_rules_inner,
            highlightthickness=0,
            bg=self.colors["card"],
            height=240
        )
        smart_scrollbar = ttk.Scrollbar(smart_rules_inner, orient="vertical", command=smart_canvas.yview)
        self.smart_rules_frame = tk.Frame(smart_canvas, bg=self.colors["card"])

        self.smart_rules_frame.bind(
            "<Configure>",
            lambda e: smart_canvas.configure(scrollregion=smart_canvas.bbox("all")),
        )

        smart_canvas.create_window((0, 0), window=self.smart_rules_frame, anchor="nw")
        smart_canvas.configure(yscrollcommand=smart_scrollbar.set)

        smart_canvas.pack(side="left", fill="both", expand=True)
        smart_scrollbar.pack(side="right", fill="y")

        self.render_smart_rule_entries()

        # -------- Add New Smart Rule --------
        smart_add_panel = self.create_info_panel(outer, "Add New Smart Rule")
        smart_add_panel.pack(fill="x", pady=10)

        smart_add_inner = tk.Frame(smart_add_panel, bg=self.colors["card"])
        smart_add_inner.pack(fill="x", padx=12, pady=12)

        tk.Label(
            smart_add_inner,
            text="Smart Category:",
            bg=self.colors["card"],
            fg=self.colors["text"],
            font=("Segoe UI", 10)
        ).grid(row=0, column=0, padx=8, pady=8, sticky="w")

        ttk.Entry(
            smart_add_inner,
            textvariable=self.new_smart_category_var,
            width=28
        ).grid(row=0, column=1, padx=8, pady=8, sticky="w")

        tk.Label(
            smart_add_inner,
            text="Keywords:",
            bg=self.colors["card"],
            fg=self.colors["text"],
            font=("Segoe UI", 10)
        ).grid(row=1, column=0, padx=8, pady=8, sticky="w")

        ttk.Entry(
            smart_add_inner,
            textvariable=self.new_smart_keywords_var,
            width=78
        ).grid(row=1, column=1, padx=8, pady=8, sticky="w")

        ttk.Button(
            smart_add_inner,
            text="Add Smart Rule",
            style="Primary.TButton",
            command=self.add_new_smart_rule
        ).grid(row=0, column=2, rowspan=2, padx=10, pady=8)

        smart_buttons = tk.Frame(outer, bg=self.colors["bg"])
        smart_buttons.pack(fill="x", pady=10)

        ttk.Button(
            smart_buttons,
            text="Save Smart Rules",
            style="Primary.TButton",
            command=self.save_smart_rules_from_gui
        ).pack(side="left", padx=6)

        ttk.Button(
            smart_buttons,
            text="Reload Smart Rules",
            style="Secondary.TButton",
            command=self.reload_smart_rules_from_gui
        ).pack(side="left", padx=6)

        # ── Rule Tester ───────────────────────────────────────────
        tester_panel = self.create_info_panel(outer, "Rule Tester")
        tester_panel.pack(fill="x", pady=(10, 16))

        ti = tk.Frame(tester_panel, bg=self.colors["card"])
        ti.pack(fill="x", padx=16, pady=14)

        # Input row
        input_row = tk.Frame(ti, bg=self.colors["card"])
        input_row.pack(fill="x", pady=(0, 10))

        tk.Label(input_row, text="Filename:",
                 bg=self.colors["card"], fg=self.colors["muted"],
                 font=("Segoe UI", 9), width=10, anchor="w").pack(side="left", padx=(0, 8))

        self.rule_test_var = tk.StringVar()
        test_entry = ttk.Entry(input_row, textvariable=self.rule_test_var, width=48)
        test_entry.pack(side="left", padx=(0, 8))
        test_entry.bind("<Return>", lambda e: self.run_rule_test())

        tk.Label(input_row, text="e.g.  invoice_march.pdf  or  photo_2024.jpg",
                 bg=self.colors["card"], fg=self.colors["muted"],
                 font=("Segoe UI", 8)).pack(side="left", padx=(4, 0))

        tk.Button(input_row, text="Test Rule",
                  bg=self.colors["accent"], fg="white",
                  activebackground=self.colors["accent_2"], activeforeground="white",
                  relief="flat", bd=0, padx=14, pady=6,
                  font=("Segoe UI", 9, "bold"), cursor="hand2",
                  command=self.run_rule_test).pack(side="right")

        # Result box
        self.rule_test_result_frame = tk.Frame(
            ti,
            bg=self.colors["card_2"],
            highlightbackground=self.colors["border"],
            highlightthickness=1,
        )
        self.rule_test_result_frame.pack(fill="x")
        self.rule_test_result_frame.pack_forget()   # hidden until first test

    def build_watch_folders_page(self):
        page = self.pages["watch"]

        outer = tk.Frame(page, bg=self.colors["bg"])
        outer.pack(fill="both", expand=True, padx=20, pady=16)

        # ── Folders list panel ────────────────────────────────────
        list_panel = self.create_info_panel(outer, "Watch Folders")
        list_panel.pack(fill="x", pady=(0, 10))

        list_host = tk.Frame(list_panel, bg=self.colors["card"])
        list_host.pack(fill="x", padx=0, pady=0)

        # Column headers
        header_row = tk.Frame(list_host, bg=self.colors["panel_2"])
        header_row.pack(fill="x")

        for text, w in [("Status", 7), ("Label", 16), ("Path", 0), ("", 10)]:
            tk.Label(header_row, text=text.upper(),
                     bg=self.colors["panel_2"], fg=self.colors["muted"],
                     font=("Segoe UI", 8, "bold"), width=w, anchor="w",
                     padx=12, pady=6).pack(side="left",
                     fill="x" if text == "Path" else None,
                     expand=text == "Path")

        tk.Frame(list_host, bg=self.colors["border_2"], height=1).pack(fill="x")

        # Scrollable folder rows
        self.watch_folders_list_frame = tk.Frame(list_host, bg=self.colors["card"])
        self.watch_folders_list_frame.pack(fill="x")

        self.refresh_watch_folders_list()

        # ── Add new folder panel ──────────────────────────────────
        add_panel = self.create_info_panel(outer, "Add Folder")
        add_panel.pack(fill="x", pady=(0, 10))

        add_inner = tk.Frame(add_panel, bg=self.colors["card"])
        add_inner.pack(fill="x", padx=14, pady=12)

        row1 = tk.Frame(add_inner, bg=self.colors["card"])
        row1.pack(fill="x", pady=(0, 6))

        tk.Label(row1, text="Path:", bg=self.colors["card"],
                 fg=self.colors["muted"], font=("Segoe UI", 9),
                 width=8, anchor="w").pack(side="left", padx=(0, 8))

        self.new_watch_path_var = tk.StringVar()
        path_entry = ttk.Entry(row1, textvariable=self.new_watch_path_var, width=60)
        path_entry.pack(side="left", padx=(0, 8))

        def _browse_new():
            from tkinter import filedialog
            d = filedialog.askdirectory(title="Select folder to watch")
            if d:
                self.new_watch_path_var.set(d)
                if not self.new_watch_label_var.get():
                    from pathlib import Path
                    self.new_watch_label_var.set(Path(d).name)

        tk.Button(row1, text="Browse",
                  bg=self.colors["panel_2"], fg=self.colors["muted"],
                  activebackground=self.colors["border_2"],
                  activeforeground=self.colors["text"],
                  relief="flat", bd=0, padx=10, pady=4,
                  font=("Segoe UI", 9), cursor="hand2",
                  command=_browse_new).pack(side="left")

        row2 = tk.Frame(add_inner, bg=self.colors["card"])
        row2.pack(fill="x")

        tk.Label(row2, text="Label:", bg=self.colors["card"],
                 fg=self.colors["muted"], font=("Segoe UI", 9),
                 width=8, anchor="w").pack(side="left", padx=(0, 8))

        self.new_watch_label_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.new_watch_label_var, width=28).pack(side="left", padx=(0, 16))

        tk.Button(row2, text="+ Add Folder",
                  bg=self.colors["accent"], fg="white",
                  activebackground=self.colors["accent_2"], activeforeground="white",
                  relief="flat", bd=0, padx=14, pady=6,
                  font=("Segoe UI", 9, "bold"), cursor="hand2",
                  command=self.add_watch_folder_from_gui).pack(side="left")

        # ── Info bar ──────────────────────────────────────────────
        info_bar = tk.Frame(outer, bg=self.colors["card_2"],
                            highlightbackground=self.colors["border"],
                            highlightthickness=1)
        info_bar.pack(fill="x", pady=(0, 0))
        tk.Label(info_bar,
                 text="i  All folders share the same rules and organized base folder.  "
                      "Changes take effect after Save + Restart Monitoring.",
                 bg=self.colors["card_2"], fg=self.colors["muted"],
                 font=("Segoe UI", 8), padx=12, pady=7, anchor="w").pack(fill="x")

    def build_history_page(self):
        page = self.pages["history"]

        outer = tk.Frame(page, bg=self.colors["bg"])
        outer.pack(fill="both", expand=True, padx=20, pady=16)

        # ── Filter bar ────────────────────────────────────────────
        filter_bar = tk.Frame(outer, bg=self.colors["card"],
                              highlightbackground=self.colors["border_2"],
                              highlightthickness=1)
        filter_bar.pack(fill="x", pady=(0, 10))

        fi = tk.Frame(filter_bar, bg=self.colors["card"])
        fi.pack(fill="x", padx=14, pady=10)

        # Search icon + entry
        tk.Label(fi, text="🔍", bg=self.colors["card"],
                 fg=self.colors["muted"], font=("Segoe UI", 11)).pack(side="left", padx=(0, 6))
        search_entry = ttk.Entry(fi, textvariable=self.history_search_var, width=32)
        search_entry.pack(side="left", padx=(0, 16))
        search_entry.bind("<KeyRelease>", lambda e: self.apply_history_filters())

        tk.Frame(fi, bg=self.colors["border_2"], width=1).pack(side="left", fill="y", pady=2, padx=(0, 16))

        tk.Label(fi, text="Category:", bg=self.colors["card"],
                 fg=self.colors["muted"], font=("Segoe UI", 9)).pack(side="left", padx=(0, 6))
        self.history_category_combo = ttk.Combobox(fi, textvariable=self.history_category_var,
                                                   width=18, state="readonly")
        self.history_category_combo.pack(side="left", padx=(0, 14))
        self.history_category_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_history_filters())

        tk.Label(fi, text="Status:", bg=self.colors["card"],
                 fg=self.colors["muted"], font=("Segoe UI", 9)).pack(side="left", padx=(0, 6))
        self.history_status_combo = ttk.Combobox(fi, textvariable=self.history_status_var,
                                                 width=18, state="readonly")
        self.history_status_combo.pack(side="left", padx=(0, 14))
        self.history_status_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_history_filters())

        tk.Button(fi, text="x  Clear",
                  bg=self.colors["panel_2"], fg=self.colors["muted"],
                  activebackground=self.colors["border_2"], activeforeground=self.colors["text"],
                  relief="flat", bd=0, padx=10, pady=4,
                  font=("Segoe UI", 9), cursor="hand2",
                  command=self.clear_history_filters).pack(side="left")

        # ── History table ─────────────────────────────────────────
        history_panel = self.create_info_panel(outer, "Recent History")
        history_panel.pack(fill="both", expand=True)

        history_host = tk.Frame(history_panel, bg=self.colors["card"])
        history_host.pack(fill="both", expand=True, padx=0, pady=0)

        columns = ("filename", "category", "status", "timestamp")
        self.history_tree = ttk.Treeview(history_host, columns=columns,
                                         show="headings", height=22)

        self.history_tree.heading("filename",  text="Filename")
        self.history_tree.heading("category",  text="Category")
        self.history_tree.heading("status",    text="Status")
        self.history_tree.heading("timestamp", text="Timestamp")

        self.history_tree.column("filename",  width=400, minwidth=200)
        self.history_tree.column("category",  width=140, minwidth=80)
        self.history_tree.column("status",    width=160, minwidth=80)
        self.history_tree.column("timestamp", width=160, minwidth=100)

        # Status tag colors
        self.history_tree.tag_configure("moved",            foreground=self.colors["stat_green"])
        self.history_tree.tag_configure("duplicate_skipped", foreground=self.colors["stat_amber"])
        self.history_tree.tag_configure("failed",           foreground=self.colors["stat_red"])
        self.history_tree.tag_configure("disappeared",      foreground=self.colors["muted"])

        scrollbar = ttk.Scrollbar(history_host, orient="vertical",
                                  command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=scrollbar.set)
        self.history_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.history_empty_label = tk.Label(
            history_host,
            text="No history yet.\nStart monitoring to see processed files here.",
            bg=self.colors["card"],
            fg=self.colors["muted"],
            font=("Segoe UI", 11),
            justify="center",
        )
