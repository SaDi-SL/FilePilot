import tkinter as tk
from tkinter import ttk


def build_notifications_page(self):
    page = self.pages["notifications"]

    outer = tk.Frame(page, bg=self.colors["bg"])
    outer.pack(fill="both", expand=True, padx=20, pady=20)

    header_row = tk.Frame(outer, bg=self.colors["bg"])
    header_row.pack(fill="x", pady=(0, 10))

    tk.Label(
        header_row,
        text="Notification Center",
        bg=self.colors["bg"],
        fg=self.colors["text"],
        font=("Segoe UI", 20, "bold"),
    ).pack(side="left")

    tk.Label(
        header_row,
        textvariable=self.notifications_count_var,
        bg=self.colors["bg"],
        fg=self.colors["accent"],
        font=("Segoe UI", 12, "bold"),
    ).pack(side="left", padx=(10, 0))

    buttons_row = tk.Frame(outer, bg=self.colors["bg"])
    buttons_row.pack(fill="x", pady=(0, 10))

    ttk.Button(
        buttons_row,
        text="Refresh Notifications",
        style="Secondary.TButton",
        command=self.refresh_notifications_view
    ).pack(side="left", padx=6)

    ttk.Button(
        buttons_row,
        text="Clear Notifications",
        style="Danger.TButton",
        command=self.clear_notifications
    ).pack(side="left", padx=6)

    panel = self.create_info_panel(outer, "All Notifications")
    panel.pack(fill="both", expand=True)

    inner = tk.Frame(panel, bg=self.colors["card"])
    inner.pack(fill="both", expand=True, padx=12, pady=12)

    columns = ("time", "level", "title", "message")
    self.notifications_tree = ttk.Treeview(
        inner,
        columns=columns,
        show="headings",
        height=16
    )

    self.notifications_tree.heading("time", text="Time")
    self.notifications_tree.heading("level", text="Level")
    self.notifications_tree.heading("title", text="Title")
    self.notifications_tree.heading("message", text="Message")

    self.notifications_tree.column("time", width=160)
    self.notifications_tree.column("level", width=90)
    self.notifications_tree.column("title", width=180)
    self.notifications_tree.column("message", width=600)

    scrollbar = ttk.Scrollbar(inner, orient="vertical", command=self.notifications_tree.yview)
    self.notifications_tree.configure(yscrollcommand=scrollbar.set)

    self.notifications_tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")


def add_notification(self, level: str, title: str, message: str):
    self.notification_center.add(level, title, message)
    count = self.notification_center.count()
    self.notifications_count_var.set(str(count))
    self._update_notif_badge()

    if hasattr(self, "notifications_tree"):
        self.refresh_notifications_view()


def refresh_notifications_view(self):
    if not hasattr(self, "notifications_tree"):
        return

    self.notification_center.load()

    for item in self.notifications_tree.get_children():
        self.notifications_tree.delete(item)

    notifications = self.notification_center.get_all()
    self.notifications_count_var.set(str(len(notifications)))

    for item in notifications:
        self.notifications_tree.insert(
            "",
            tk.END,
            values=(
                item["time"],
                item["level"].upper(),
                item["title"],
                item["message"],
            )
        )

    if hasattr(self, "status_bar_var"):
        self.status_bar_var.set("Notifications refreshed.")

    if hasattr(self, "toast_manager"):
        self.toast_manager.show_toast("Notifications refreshed.", "info")

def clear_notifications(self):
    self.notification_center.clear()
    self.notifications_count_var.set("0")
    self._update_notif_badge()

    if hasattr(self, "notifications_tree"):
        for item in self.notifications_tree.get_children():
            self.notifications_tree.delete(item)

    self.status_bar_var.set("Notifications cleared.")
    if hasattr(self, "toast_manager"):
        self.toast_manager.show_toast("Notifications cleared.", "info")