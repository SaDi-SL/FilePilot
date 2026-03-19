"""
gui_theme.py — Theme configuration and color palette for FilePilot.
Mixin class: ThemeMixin
"""
import tkinter as tk
from tkinter import ttk


class ThemeMixin:
    """Handles color palette, ttk styles, and theme switching logic."""

    def configure_theme(self):
        self.style.theme_use("clam")

        if self.theme_mode == "dark":
            self.colors = {
                "bg":               "#0d1117",
                "panel":            "#161b22",
                "panel_2":          "#21262d",
                "card":             "#161b22",
                "card_2":           "#0d1117",
                "text":             "#f0f6fc",
                "muted":            "#8b949e",
                "accent":           "#2563eb",
                "accent_2":         "#3b82f6",
                "active_nav":       "#1e3a5f",
                "active_nav_text":  "#93c5fd",
                "border":           "#21262d",
                "border_2":         "#30363d",
                "danger":           "#3d1c1c",
                "danger_fg":        "#ff7b72",
                "danger_2":         "#2c1212",
                "danger_border":    "#5c2020",
                "success":          "#3fb950",
                "success_bg":       "#0d2117",
                "success_border":   "#1a4a2a",
                "warning":          "#e3b341",
                "warning_bg":       "#271f0a",
                "stat_blue":        "#60a5fa",
                "stat_green":       "#3fb950",
                "stat_amber":       "#e3b341",
                "stat_red":         "#f85149",
                "stat_purple":      "#bc8cff",
                "input_bg":         "#0d1117",
                "log_info":         "#7d8590",
                "log_warning":      "#e3b341",
                "log_error":        "#f85149",
                "log_debug":        "#58a6ff",
            }
        else:
            self.colors = {
                "bg":               "#f1f5f9",
                "panel":            "#ffffff",
                "panel_2":          "#f1f5f9",
                "card":             "#ffffff",
                "card_2":           "#f8fafc",
                "text":             "#0f172a",
                "muted":            "#64748b",
                "accent":           "#2563eb",
                "accent_2":         "#3b82f6",
                "active_nav":       "#dbeafe",
                "active_nav_text":  "#1d4ed8",
                "border":           "#e2e8f0",
                "border_2":         "#cbd5e1",
                "danger":           "#fee2e2",
                "danger_fg":        "#dc2626",
                "danger_2":         "#fecaca",
                "danger_border":    "#fca5a5",
                "success":          "#059669",
                "success_bg":       "#d1fae5",
                "success_border":   "#6ee7b7",
                "warning":          "#d97706",
                "warning_bg":       "#fef3c7",
                "stat_blue":        "#2563eb",
                "stat_green":       "#059669",
                "stat_amber":       "#d97706",
                "stat_red":         "#dc2626",
                "stat_purple":      "#7c3aed",
                "input_bg":         "#ffffff",
                "log_info":         "#475569",
                "log_warning":      "#b45309",
                "log_error":        "#dc2626",
                "log_debug":        "#2563eb",
            }

        self.root.configure(bg=self.colors["bg"], bd=0, highlightthickness=0)
        self.root["bg"] = self.colors["bg"]

        self.style.configure(
            "Primary.TButton",
            background=self.colors["accent"],
            foreground="white",
            borderwidth=0,
            padding=(14, 9),
            font=("Segoe UI", 10, "bold"),
            focuscolor="none",
        )
        self.style.map(
            "Primary.TButton",
            background=[("active", self.colors["accent_2"]), ("disabled", self.colors["border"])],
            foreground=[("active", "white"), ("disabled", self.colors["muted"])],
        )

        self.style.configure(
            "Secondary.TButton",
            background=self.colors["panel_2"],
            foreground=self.colors["muted"],
            borderwidth=1,
            padding=(12, 8),
            font=("Segoe UI", 10),
            focuscolor="none",
        )
        self.style.map(
            "Secondary.TButton",
            background=[("active", self.colors["border_2"])],
            foreground=[("active", self.colors["text"])],
        )

        self.style.configure(
            "Danger.TButton",
            background=self.colors["danger"],
            foreground=self.colors["danger_fg"],
            borderwidth=1,
            padding=(12, 8),
            font=("Segoe UI", 10, "bold"),
            focuscolor="none",
        )
        self.style.map(
            "Danger.TButton",
            background=[("active", self.colors["danger_2"]), ("disabled", self.colors["panel_2"])],
            foreground=[("active", self.colors["danger_fg"]), ("disabled", self.colors["muted"])],
        )

        self.style.configure("TEntry",
            padding=8,
            fieldbackground=self.colors["input_bg"],
            foreground=self.colors["text"],
            insertcolor=self.colors["text"],
        )
        self.style.configure("TCombobox",
            padding=7,
            fieldbackground=self.colors["input_bg"],
            foreground=self.colors["text"],
        )

        self.style.configure(
            "Treeview",
            background=self.colors["panel"],
            fieldbackground=self.colors["panel"],
            foreground=self.colors["text"],
            bordercolor=self.colors["border"],
            rowheight=28,
            font=("Segoe UI", 10),
        )
        self.style.configure(
            "Treeview.Heading",
            background=self.colors["panel_2"],
            foreground=self.colors["muted"],
            font=("Segoe UI", 9, "bold"),
            relief="flat",
        )
        self.style.map(
            "Treeview",
            background=[("selected", self.colors["accent"])],
            foreground=[("selected", "white")],
        )
