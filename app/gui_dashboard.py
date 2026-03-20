import tkinter as tk
from tkinter import ttk

def _add_tooltip(widget, text):
    """Simple tooltip shown on hover for any widget."""
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
        lbl = tk.Label(
            tw, text=text,
            background="#1e293b", foreground="#f8fafc",
            font=("Segoe UI", 9), relief="flat",
            padx=8, pady=4
        )
        lbl.pack()
        tip_window[0] = tw

    def hide_tip(event):
        tw = tip_window[0]
        tip_window[0] = None
        if tw:
            try:
                tw.destroy()
            except Exception:
                pass

    widget.bind("<Enter>", show_tip)
    widget.bind("<Leave>", hide_tip)



def build_dashboard_page(self):
    page = self.pages["dashboard"]

    page_container = tk.Frame(page, bg=self.colors["bg"])
    page_container.pack(fill="both", expand=True)

    canvas = tk.Canvas(page_container, bg=self.colors["bg"], highlightthickness=0)
    scrollbar = ttk.Scrollbar(page_container, orient="vertical", command=canvas.yview)
    scrollable_frame = tk.Frame(canvas, bg=self.colors["bg"])

    scrollable_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )

    canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    self.attach_safe_mousewheel(canvas, owner=page)

    outer = scrollable_frame
    pad = dict(padx=20, pady=(0, 10))

    # ── Stat cards — all 8 in TWO compact rows ──
    cards_row = tk.Frame(outer, bg=self.colors["bg"])
    cards_row.pack(fill="x", padx=20, pady=(14, 0))

    gap = 6
    self.create_stat_box(cards_row, "Total Files",  self.total_files_var,        "stat_blue").pack(side="left", fill="x", expand=True, padx=(0, gap))
    self.create_stat_box(cards_row, "Failed",       self.failed_files_var,       "stat_red").pack(side="left", fill="x", expand=True, padx=(0, gap))
    self.create_stat_box(cards_row, "Duplicates",   self.duplicates_var,         "stat_amber").pack(side="left", fill="x", expand=True, padx=(0, gap))
    self.create_stat_box(cards_row, "Documents",    self.documents_var,          "stat_green").pack(side="left", fill="x", expand=True, padx=(0, gap))
    self.create_stat_box(cards_row, "Rules",        self.rules_count_var,        "stat_purple").pack(side="left", fill="x", expand=True)

    cards_row_2 = tk.Frame(outer, bg=self.colors["bg"])
    cards_row_2.pack(fill="x", padx=20, pady=(6, 0))

    self.create_stat_box(cards_row_2, "Plugin",   self.plugin_classified_var,  "stat_blue").pack(side="left", fill="x", expand=True, padx=(0, gap))
    self.create_stat_box(cards_row_2, "Smart",    self.smart_classified_var,   "stat_green").pack(side="left", fill="x", expand=True, padx=(0, gap))
    self.create_stat_box(cards_row_2, "Content",  self.content_classified_var, "stat_amber").pack(side="left", fill="x", expand=True, padx=(0, gap))
    self.create_stat_box(cards_row_2, "Top Cat.", self.top_category_var,       "stat_purple").pack(side="left", fill="x", expand=True)

    # ── Quick Actions ──
    actions_panel = self.create_info_panel(outer, "Quick Actions")
    actions_panel.pack(fill="x", padx=20, pady=(10, 0))

    actions_inner = tk.Frame(actions_panel, bg=self.colors["card"])
    actions_inner.pack(fill="x", padx=14, pady=(10, 14))

    # All actions in one clean horizontal row
    btn_row = tk.Frame(actions_inner, bg=self.colors["card"])
    btn_row.pack(fill="x", pady=(4, 0))

    # Start — solid blue
    self.start_button = tk.Button(
        btn_row,
        text="▶  Start Monitoring",
        bg=self.colors["accent"],
        fg="white",
        activebackground=self.colors["accent_2"],
        activeforeground="white",
        relief="flat", bd=0,
        padx=14, pady=6,
        font=("Segoe UI", 9, "bold"),
        cursor="hand2",
        command=self.start_monitoring,
    )
    self.start_button.pack(side="left", padx=(0, 5))
    _add_tooltip(self.start_button, "Start watching the incoming folder  (Ctrl+M)")

    # Stop — danger bordered, dimmed when disabled
    self.stop_button = tk.Button(
        btn_row,
        text="■  Stop Monitoring",
        bg=self.colors["danger"],
        fg=self.colors["danger_fg"],
        activebackground=self.colors["danger_2"],
        activeforeground=self.colors["danger_fg"],
        relief="flat", bd=0,
        highlightthickness=0,
        padx=14, pady=6,
        font=("Segoe UI", 9, "bold"),
        cursor="hand2",
        state="disabled",
        command=self.stop_monitoring,
    )
    self.stop_button.pack(side="left", padx=(0, 10))
    _add_tooltip(self.stop_button, "Stop file monitoring  (Ctrl+M)")

    # Thin vertical separator
    sep = tk.Frame(btn_row, bg=self.colors["border"], width=1)
    sep.pack(side="left", fill="y", pady=4, padx=(0, 10))

    # Secondary buttons — same row, lighter style
    def _sec(text, cmd, tip):
        b = tk.Button(
            btn_row, text=text,
            bg=self.colors["panel_2"],
            fg=self.colors["muted"],
            activebackground=self.colors["border_2"],
            activeforeground=self.colors["text"],
            relief="solid", bd=1,
            highlightbackground=self.colors["border"],
            highlightthickness=1,
            padx=10, pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=cmd,
        )
        _add_tooltip(b, tip)
        b.pack(side="left", padx=(0, 4))

    _sec("↻  Refresh",    self.refresh_stats,
         "Reload statistics  (Ctrl+R / F5)")
    _sec("📂  Incoming",  lambda: self.open_folder(self.config["source_folder"]),
         "Open the incoming folder")
    _sec("📁  Organized", lambda: self.open_folder(self.config["organized_base_folder"]),
         "Open the organized folder")

    # Last file bar — inset dark strip
    last_bar = tk.Frame(actions_inner, bg=self.colors["card_2"],
                        highlightbackground=self.colors["border"],
                        highlightthickness=1)
    last_bar.pack(fill="x", pady=(10, 2))
    tk.Label(last_bar, text="Last file:",
             bg=self.colors["card_2"], fg=self.colors["muted"],
             font=("Segoe UI", 9), padx=12, pady=6).pack(side="left")
    tk.Label(last_bar, textvariable=self.last_file_var,
             bg=self.colors["card_2"], fg=self.colors["text"],
             font=("Consolas", 9), pady=6).pack(side="left")

    # -------- Analytics Row --------
    analytics_row = tk.Frame(outer, bg=self.colors["bg"])
    analytics_row.pack(fill="x", padx=20, pady=(10, 0))

    methods_panel = self.create_info_panel(analytics_row, "Classification Methods")
    methods_panel.pack(side="left", fill="both", expand=True, padx=(0, 6))

    methods_inner = tk.Frame(methods_panel, bg=self.colors["card"])
    methods_inner.pack(fill="both", expand=True, padx=0, pady=0)

    self.classification_chart_canvas = tk.Canvas(
        methods_inner,
        bg=self.colors["card"],
        height=180,
        highlightthickness=0,
        bd=0,
    )
    self.classification_chart_canvas.pack(fill="both", expand=True, padx=8, pady=(4, 8))

    self.classification_chart_canvas.bind(
        "<Configure>",
        lambda e: self.draw_classification_chart(
            int(self.plugin_classified_var.get() or 0),
            int(self.smart_classified_var.get() or 0),
            int(self.content_classified_var.get() or 0),
            int(self.extension_classified_var.get() or 0),
        )
    )

    top_categories_panel = self.create_info_panel(analytics_row, "Top Categories")
    top_categories_panel.pack(side="left", fill="both", expand=True)

    top_categories_inner = tk.Frame(top_categories_panel, bg=self.colors["card"])
    top_categories_inner.pack(fill="both", expand=True, padx=12, pady=12)

    # Category bars host frame (replaces treeview with bar chart rows)
    self.top_categories_bars_frame = tk.Frame(
        top_categories_inner,
        bg=self.colors["card"],
    )
    self.top_categories_bars_frame.pack(fill="both", expand=True)

    # -------- Recent Activity --------
    activity_panel = self.create_info_panel(outer, "Recent Activity")
    activity_panel.pack(fill="both", expand=True, padx=20, pady=(10, 0))

    activity_inner = tk.Frame(activity_panel, bg=self.colors["card"])
    activity_inner.pack(fill="both", expand=True, padx=0, pady=0)

    # Header row
    activity_top = tk.Frame(activity_inner, bg=self.colors["card"])
    activity_top.pack(fill="x", padx=14, pady=(10, 6))

    tk.Label(activity_top, text="Recent Entries:",
             bg=self.colors["card"], fg=self.colors["text"],
             font=("Segoe UI", 10, "bold")).pack(side="left")

    tk.Label(activity_top, textvariable=self.recent_activity_count_var,
             bg=self.colors["card"], fg=self.colors["accent"],
             font=("Segoe UI", 10, "bold")).pack(side="left", padx=(6, 0))

    # ── Mini live feed — last 5 files as inline cards ──────────────
    self.live_feed_frame = tk.Frame(activity_inner, bg=self.colors["card"])
    self.live_feed_frame.pack(fill="x", padx=14, pady=(0, 8))

    # Placeholder shown when no files processed yet
    self.live_feed_empty = tk.Label(
        self.live_feed_frame,
        text="No files processed yet — start monitoring to see live activity.",
        bg=self.colors["card"], fg=self.colors["muted"],
        font=("Segoe UI", 9), pady=6,
    )
    self.live_feed_empty.pack(anchor="w")

    # ── Full history treeview below ─────────────────────────────────
    tk.Frame(activity_inner, bg=self.colors["border"], height=1).pack(fill="x")

    activity_columns = ("time", "file", "category", "status", "method")
    self.recent_activity_tree = ttk.Treeview(
        activity_inner,
        columns=activity_columns,
        show="headings",
        height=7,
    )

    self.recent_activity_tree.heading("time",     text="Time")
    self.recent_activity_tree.heading("file",     text="File")
    self.recent_activity_tree.heading("category", text="Category")
    self.recent_activity_tree.heading("status",   text="Status")
    self.recent_activity_tree.heading("method",   text="Method")

    self.recent_activity_tree.column("time",     width=150)
    self.recent_activity_tree.column("file",     width=300)
    self.recent_activity_tree.column("category", width=110)
    self.recent_activity_tree.column("status",   width=130)
    self.recent_activity_tree.column("method",   width=110)

    # Status colors
    self.recent_activity_tree.tag_configure("moved",             foreground=self.colors["stat_green"])
    self.recent_activity_tree.tag_configure("duplicate_skipped", foreground=self.colors["stat_amber"])
    self.recent_activity_tree.tag_configure("failed",            foreground=self.colors["stat_red"])

    self.recent_activity_tree.pack(fill="both", expand=True)

    # -------- Classification Breakdown --------
    breakdown_panel = self.create_info_panel(outer, "How Files Were Sorted")
    breakdown_panel.pack(fill="x", padx=20, pady=(10, 16))

    self.breakdown_frame = tk.Frame(breakdown_panel, bg=self.colors["card"])
    self.breakdown_frame.pack(fill="x", padx=14, pady=12)

    # Getting Started banner (shown when no files processed yet)
    self.getting_started_frame = tk.Frame(outer, bg=self.colors["card_2"],
                                           highlightbackground=self.colors["accent"],
                                           highlightthickness=1)

    gs_inner = tk.Frame(self.getting_started_frame, bg=self.colors["card_2"])
    gs_inner.pack(fill="x", padx=20, pady=16)

    tk.Label(gs_inner, text="Getting Started with FilePilot",
             bg=self.colors["card_2"], fg=self.colors["text"],
             font=("Segoe UI", 12, "bold")).pack(anchor="w")
    tk.Label(gs_inner, text="Follow these steps to start organizing your files automatically:",
             bg=self.colors["card_2"], fg=self.colors["muted"],
             font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 10))

    for i, (step, desc) in enumerate([
        ("1. Set your folders", "Go to Settings → set Incoming Folder and Organized Folder"),
        ("2. Check your rules", "Go to Rules Editor → make sure categories match your files"),
        ("3. Start Monitoring", "Click ▶ Start Monitoring above — FilePilot will watch your folder"),
        ("4. Drop files in",    "Copy files to your Incoming Folder and watch them get organized!"),
    ], 1):
        row = tk.Frame(gs_inner, bg=self.colors["card_2"])
        row.pack(fill="x", pady=2)
        tk.Label(row, text=step,
                 bg=self.colors["card_2"], fg=self.colors["accent"],
                 font=("Segoe UI", 9, "bold"), width=20, anchor="w").pack(side="left")
        tk.Label(row, text=desc,
                 bg=self.colors["card_2"], fg=self.colors["muted"],
                 font=("Segoe UI", 9), anchor="w").pack(side="left")


def draw_classification_chart(self, plugin_count, smart_count, content_count, extension_count):
    if not hasattr(self, "classification_chart_canvas"):
        return

    canvas = self.classification_chart_canvas
    canvas.delete("all")
    canvas.update_idletasks()

    width = max(canvas.winfo_width(), 300)
    height = max(canvas.winfo_height(), 180)

    # Fill background explicitly with card color
    canvas.configure(bg=self.colors["card"])
    canvas.create_rectangle(0, 0, width, height,
                            fill=self.colors["card"], outline="")

    data = [
        ("Plugin",    plugin_count,    self.colors["stat_blue"]),
        ("Smart",     smart_count,     self.colors["stat_green"]),
        ("Content",   content_count,   self.colors["stat_amber"]),
        ("Extension", extension_count, self.colors["muted"]),
    ]

    max_value = max((v for _, v, _ in data), default=1)
    if max_value <= 0:
        max_value = 1

    margin_left = 16
    margin_right = 16
    margin_top = 24
    margin_bottom = 36

    usable_width  = width  - margin_left - margin_right
    usable_height = height - margin_top  - margin_bottom

    count = len(data)
    gap = 16
    bar_width = max((usable_width - gap * (count - 1)) / count, 30)

    total_w = bar_width * count + gap * (count - 1)
    start_x = margin_left + (usable_width - total_w) / 2

    baseline_y = height - margin_bottom

    # Baseline
    canvas.create_line(
        margin_left, baseline_y,
        width - margin_right, baseline_y,
        fill=self.colors["border_2"], width=1,
    )

    for i, (label, value, color) in enumerate(data):
        x1 = start_x + i * (bar_width + gap)
        x2 = x1 + bar_width
        cx = (x1 + x2) / 2

        bar_h = (value / max_value) * usable_height if value > 0 else 2
        y2 = baseline_y
        y1 = y2 - bar_h

        # Bar with rounded top (simulate with two rects + oval)
        canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline="")

        # Value label above bar — brighter
        canvas.create_text(
            cx, y1 - 11,
            text=str(value),
            fill=self.colors["text"],
            font=("Segoe UI", 10, "bold"),
        )

        # Category label below baseline — clearly visible
        canvas.create_text(
            cx, baseline_y + 18,
            text=label,
            fill=self.colors["muted"],
            font=("Segoe UI", 9),
        )


def refresh_top_categories_view(self, category_counter: dict):
    if not hasattr(self, "top_categories_bars_frame"):
        return

    # Clear previous bars
    for w in self.top_categories_bars_frame.winfo_children():
        w.destroy()

    sorted_items = sorted(
        category_counter.items(),
        key=lambda x: x[1],
        reverse=True
    )[:6]

    if not sorted_items:
        tk.Label(
            self.top_categories_bars_frame,
            text="No data yet",
            bg=self.colors["card"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=4, pady=8)
        return

    max_val = max(v for _, v in sorted_items) or 1

    for category, count in sorted_items:
        row = tk.Frame(self.top_categories_bars_frame, bg=self.colors["card"])
        row.pack(fill="x", pady=3)

        # Category name
        tk.Label(
            row,
            text=category,
            bg=self.colors["card"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            width=10,
            anchor="w",
        ).pack(side="left", padx=(0, 8))

        # Bar track
        track = tk.Frame(row, bg=self.colors["panel_2"], height=4)
        track.pack(side="left", fill="x", expand=True)
        track.pack_propagate(False)

        fill_pct = count / max_val

        def _draw_fill(t=track, p=fill_pct):
            t.update_idletasks()
            w = int(t.winfo_width() * p)
            if w < 2:
                w = 2
            fill = tk.Frame(t, bg=self.colors["stat_blue"], height=4)
            fill.place(x=0, y=0, width=w, height=4)

        track.after(50, _draw_fill)

        # Count
        tk.Label(
            row,
            text=str(count),
            bg=self.colors["card"],
            fg=self.colors["muted"],
            font=("Segoe UI", 9),
            width=3,
            anchor="e",
        ).pack(side="left", padx=(6, 0))


def refresh_recent_activity_view(self):
    """Refresh recent activity tree + live feed from in-memory cache."""
    if not hasattr(self, "recent_activity_tree"):
        return

    for item in self.recent_activity_tree.get_children():
        self.recent_activity_tree.delete(item)

    all_rows = list(getattr(self, "history_rows_cache", []))
    self.recent_activity_count_var.set(str(len(all_rows)))

    display_rows = all_rows[-100:]
    display_rows_rev = list(reversed(display_rows))

    status_colors = {
        "moved":             self.colors["stat_green"],
        "duplicate_skipped": self.colors["stat_amber"],
        "failed":            self.colors["stat_red"],
    }

    for row in display_rows_rev:
        status = row.get("status", "")
        tag = status if status in ("moved", "duplicate_skipped", "failed") else ""
        self.recent_activity_tree.insert(
            "", tk.END,
            values=(
                row.get("timestamp", ""),
                row.get("filename", ""),
                row.get("category", ""),
                status,
                row.get("classification_method", ""),
            ),
            tags=(tag,) if tag else (),
        )

    # ── Populate live feed (last 5) ───────────────────────────────
    if not hasattr(self, "live_feed_frame"):
        return

    for w in self.live_feed_frame.winfo_children():
        w.destroy()

    recent5 = all_rows[-5:] if all_rows else []

    if not recent5:
        tk.Label(
            self.live_feed_frame,
            text="No files processed yet — start monitoring to see live activity.",
            bg=self.colors["card"], fg=self.colors["muted"],
            font=("Segoe UI", 9), pady=6,
        ).pack(anchor="w")
        return

    for row in reversed(recent5):
        status = row.get("status", "")
        dot_color = status_colors.get(status, self.colors["muted"])
        ts = row.get("timestamp", "")
        ts_short = ts[-8:] if ts else ""
        fname = row.get("filename", "")
        short = fname if len(fname) <= 40 else fname[:37] + "..."
        cat = row.get("category", "")

        card = tk.Frame(self.live_feed_frame, bg=self.colors["card_2"],
                        highlightbackground=self.colors["border"],
                        highlightthickness=1)
        card.pack(fill="x", pady=(0, 3))

        dot = tk.Canvas(card, width=8, height=8,
                        bg=self.colors["card_2"], highlightthickness=0)
        dot.pack(side="left", padx=(10, 6), pady=8)
        dot.create_oval(1, 1, 7, 7, fill=dot_color, outline="")

        tk.Label(card, text=ts_short, bg=self.colors["card_2"],
                 fg=self.colors["muted"], font=("Consolas", 8)).pack(side="left", padx=(0, 10))
        tk.Label(card, text=short, bg=self.colors["card_2"],
                 fg=self.colors["text"], font=("Segoe UI", 9)).pack(side="left")
        tk.Label(card, text="->", bg=self.colors["card_2"],
                 fg=self.colors["muted"], font=("Segoe UI", 9)).pack(side="left", padx=(6, 4))
        tk.Label(card, text=cat, bg=self.colors["card_2"],
                 fg=dot_color, font=("Segoe UI", 9, "bold")).pack(side="left")