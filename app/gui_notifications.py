import tkinter as tk
from tkinter import ttk


_IMPORTANT_TITLES = {
    "error", "warning", "failed", "plugin", "backup",
    "rules", "settings", "config", "hash", "statistics",
    "file processed", "ai", "marketplace",
}

_SKIP_DUPLICATES = {
    "Application Started",
    "Plugins Reloaded",
    "Notifications refreshed.",
    "Settings Reloaded",
}


def _is_important(notif):
    level = notif.get("level", "").lower()
    title = notif.get("title", "").lower()
    if level in ("error", "warning"):
        return True
    for kw in _IMPORTANT_TITLES:
        if kw in title:
            return True
    return False


def _deduplicate(notifications):
    seen = {}
    result = []
    for n in reversed(notifications):
        title = n.get("title", "")
        if title in _SKIP_DUPLICATES:
            if title not in seen:
                seen[title] = True
                result.append(n)
        else:
            result.append(n)
    return list(reversed(result))


def build_notifications_page(self):
    page = self.pages["notifications"]

    outer = tk.Frame(page, bg=self.colors["bg"])
    outer.pack(fill="both", expand=True)

    # Filter bar
    filter_bar = tk.Frame(outer, bg=self.colors["panel"],
                          highlightbackground=self.colors["border_2"],
                          highlightthickness=1)
    filter_bar.pack(fill="x")

    self._notif_filter = tk.StringVar(value="important")
    filter_btns = {}

    def _set_filter(val):
        self._notif_filter.set(val)
        _refresh_filter_btns()
        _build_cards()

    for key, label in [("important", "Important"), ("all", "All"), ("errors", "Errors Only")]:
        btn = tk.Button(filter_bar, text=label,
                        bg=self.colors["panel"], fg=self.colors["muted"],
                        activebackground=self.colors["panel_2"],
                        activeforeground=self.colors["text"],
                        relief="flat", bd=0, padx=18, pady=10,
                        font=("Segoe UI", 9), cursor="hand2",
                        command=lambda k=key: _set_filter(k))
        btn.pack(side="left")
        filter_btns[key] = btn

    def _refresh_filter_btns():
        active = self._notif_filter.get()
        for k, b in filter_btns.items():
            if k == active:
                b.config(fg=self.colors["text"], font=("Segoe UI", 9, "bold"))
            else:
                b.config(fg=self.colors["muted"], font=("Segoe UI", 9))

    _refresh_filter_btns()

    action_frame = tk.Frame(filter_bar, bg=self.colors["panel"])
    action_frame.pack(side="right", padx=12)

    self.notif_count_label = tk.Label(action_frame, text="",
                                       bg=self.colors["panel"], fg=self.colors["muted"],
                                       font=("Segoe UI", 9))
    self.notif_count_label.pack(side="left", padx=(0, 12))

    def _clear_all():
        self.notification_center.clear()
        self.notifications_count_var.set("0")
        self._update_notif_badge()
        _build_cards()
        self.status_bar_var.set("Notifications cleared.")

    tk.Button(action_frame, text="Clear All",
              bg=self.colors["danger"], fg=self.colors["danger_fg"],
              activebackground=self.colors["danger"],
              relief="flat", bd=0, padx=12, pady=6,
              font=("Segoe UI", 9), cursor="hand2",
              command=_clear_all).pack(side="left")

    # Scrollable area
    canvas = tk.Canvas(outer, bg=self.colors["bg"], highlightthickness=0)
    sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    sf = tk.Frame(canvas, bg=self.colors["bg"])
    sf.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    win_id = canvas.create_window((0, 0), window=sf, anchor="nw")
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=e.width))
    canvas.configure(yscrollcommand=sb.set)
    canvas.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    self.attach_safe_mousewheel(canvas, owner=page)
    self._notif_scroll_frame = sf

    def _build_cards():
        for w in sf.winfo_children():
            w.destroy()

        self.notification_center.load()
        all_notifs = self.notification_center.get_all()
        f = self._notif_filter.get()

        if f == "important":
            notifs = _deduplicate([n for n in all_notifs if _is_important(n)])
        elif f == "errors":
            notifs = [n for n in all_notifs if n.get("level","").lower() in ("error","warning")]
        else:
            notifs = _deduplicate(all_notifs)

        total = len(all_notifs)
        showing = len(notifs)
        self.notifications_count_var.set(str(total))
        self._update_notif_badge()

        if hasattr(self, "notif_count_label"):
            self.notif_count_label.config(text=f"Showing {showing} of {total}")

        if not notifs:
            empty = tk.Frame(sf, bg=self.colors["bg"])
            empty.pack(fill="both", expand=True, pady=80)
            msg = "All good — no important notifications." if f == "important" else "No notifications."
            tk.Label(empty, text=msg, bg=self.colors["bg"], fg=self.colors["muted"],
                     font=("Segoe UI", 12)).pack()
            return

        for notif in reversed(notifs):
            _build_card(notif)

    def _build_card(notif):
        level   = notif.get("level", "info").lower()
        title   = notif.get("title", "")
        message = notif.get("message", "")
        time_str = notif.get("time", "")

        level_cfg = {
            "error":   (self.colors["danger"],     self.colors["danger_fg"],  self.colors["danger_border"], self.colors["danger_fg"]),
            "warning": ("#2a2200",                  self.colors["stat_amber"], self.colors["stat_amber"],   self.colors["stat_amber"]),
            "success": (self.colors["success_bg"],  self.colors["stat_green"], self.colors["success_border"], self.colors["stat_green"]),
            "info":    (self.colors["card"],        self.colors["text"],       self.colors["border"],       self.colors["muted"]),
        }
        card_bg, title_fg, border_col, strip_col = level_cfg.get(level, level_cfg["info"])

        card = tk.Frame(sf, bg=card_bg,
                        highlightbackground=border_col,
                        highlightthickness=1)
        card.pack(fill="x", padx=10, pady=(0, 3))

        tk.Frame(card, bg=strip_col, width=3).pack(side="left", fill="y")

        body = tk.Frame(card, bg=card_bg)
        body.pack(side="left", fill="both", expand=True, padx=12, pady=8)

        icons = {"error": "●", "warning": "▲", "success": "✓", "info": "i"}
        icon = icons.get(level, "·")

        hdr = tk.Frame(body, bg=card_bg)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"{icon}  {title}",
                 bg=card_bg, fg=title_fg,
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(hdr, text=time_str,
                 bg=card_bg, fg=self.colors["muted"],
                 font=("Segoe UI", 8)).pack(side="right")

        if message:
            tk.Label(body, text=message,
                     bg=card_bg, fg=self.colors["muted"],
                     font=("Segoe UI", 8),
                     wraplength=900, justify="left", anchor="w").pack(fill="x", pady=(2, 0))

    self._build_notification_cards = _build_cards
    _build_cards()


def add_notification(self, level, title, message):
    if title == "Application Started":
        if getattr(self, "_app_started_notif_added", False):
            return
        self._app_started_notif_added = True

    self.notification_center.add(level, title, message)
    count = self.notification_center.count()
    self.notifications_count_var.set(str(count))
    self._update_notif_badge()

    if hasattr(self, "_build_notification_cards"):
        try:
            self._build_notification_cards()
        except Exception:
            pass


def refresh_notifications_view(self):
    if hasattr(self, "_build_notification_cards"):
        try:
            self._build_notification_cards()
        except Exception:
            pass
    if hasattr(self, "status_bar_var"):
        self.status_bar_var.set("Notifications refreshed.")


def clear_notifications(self):
    self.notification_center.clear()
    self.notifications_count_var.set("0")
    self._update_notif_badge()
    if hasattr(self, "_build_notification_cards"):
        self._build_notification_cards()
    self.status_bar_var.set("Notifications cleared.")