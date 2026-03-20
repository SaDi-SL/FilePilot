"""
gui_actions.py — Business logic actions for FilePilot.
Covers: rules, settings, stats, history, logs, plugins, config, filesystem.
Mixin class: ActionsMixin
"""
import csv
import sys
import json
import os
import shutil
import tkinter as tk
import tkinter.simpledialog as simpledialog
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app.branding import APP_NAME, APP_COPYRIGHT, APP_DEVELOPER, APP_EMAIL, APP_VERSION, APP_WEBSITE, APP_TAGLINE
from app.config_loader import get_config_path, resolve_runtime_path
from app.i18n import t, get_language
from app.main import build_monitor
from app.smart_classifier import load_smart_rules, save_smart_rules
from app.rule_tester import test_filename, RuleTestResult
from app.startup_manager import enable_startup, disable_startup


class ActionsMixin:
    """All business-logic actions: saving, loading, refreshing, validating, opening files."""

    def run_rule_test(self) -> None:
        """Run the Rule Tester and display results inline in the Rules page."""
        filename = getattr(self, "rule_test_var", None)
        if filename is None:
            return
        filename = filename.get().strip()

        result_frame = getattr(self, "rule_test_result_frame", None)
        if result_frame is None:
            return

        # Clear previous result
        for w in result_frame.winfo_children():
            w.destroy()

        if not filename:
            return

        # Run test
        result = test_filename(filename, self.config, self.monitor.handler.plugin_manager
                               if hasattr(self.monitor, "handler") else None)

        # Show result frame
        result_frame.pack(fill="x")

        # ── Verdict banner ────────────────────────────────────────
        verdict_color = self.colors.get(result.verdict_color, self.colors["stat_blue"])
        banner = tk.Frame(result_frame, bg=verdict_color)
        banner.pack(fill="x")

        method_labels = {
            "plugin":        "Plugin",
            "smart_name":    "Smart (name)",
            "smart_content": "Smart (content)",
            "extension":     "Extension rule",
            "default":       "No rule matched",
        }
        method_str = method_labels.get(result.final_method, result.final_method)

        tk.Label(banner,
                 text=f"  {result.final_category.upper()}",
                 bg=verdict_color, fg="white",
                 font=("Segoe UI", 13, "bold"),
                 padx=14, pady=10, anchor="w").pack(side="left")

        tk.Label(banner,
                 text=f"via {method_str}  ",
                 bg=verdict_color, fg="white",
                 font=("Segoe UI", 9),
                 padx=0, pady=10, anchor="e").pack(side="right")

        # ── Decision steps ────────────────────────────────────────
        steps_frame = tk.Frame(result_frame, bg=self.colors["card_2"])
        steps_frame.pack(fill="x", padx=0, pady=0)

        method_icons = {
            "plugin":        "1.",
            "smart_name":    "2.",
            "smart_content": "3.",
            "extension":     "4.",
            "default":       "5.",
        }

        for step in result.steps:
            row = tk.Frame(steps_frame, bg=self.colors["card_2"])
            row.pack(fill="x", padx=12, pady=3)

            if step.matched:
                icon_col = self.colors["stat_green"]
                icon_txt = "+"
            else:
                icon_col = self.colors["muted"]
                icon_txt = "-"

            num = method_icons.get(step.method, "")
            tk.Label(row, text=f"{num} [{icon_txt}]",
                     bg=self.colors["card_2"], fg=icon_col,
                     font=("Consolas", 9, "bold"), width=8, anchor="w").pack(side="left")

            tk.Label(row, text=step.reason,
                     bg=self.colors["card_2"],
                     fg=self.colors["text"] if step.matched else self.colors["muted"],
                     font=("Segoe UI", 9), anchor="w").pack(side="left", fill="x", expand=True)

            if step.detail and step.matched:
                detail_row = tk.Frame(steps_frame, bg=self.colors["card_2"])
                detail_row.pack(fill="x", padx=12, pady=(0, 2))
                tk.Label(detail_row, text="         " + step.detail,
                         bg=self.colors["card_2"], fg=self.colors["stat_blue"],
                         font=("Consolas", 8), anchor="w").pack(side="left")

        # ── Warnings ──────────────────────────────────────────────
        if result.warnings:
            warn_frame = tk.Frame(result_frame, bg=self.colors["warning_bg"],
                                  highlightbackground=self.colors["border"],
                                  highlightthickness=1)
            warn_frame.pack(fill="x", padx=0, pady=(4, 0))
            for w in result.warnings:
                tk.Label(warn_frame, text=f"  !  {w}",
                         bg=self.colors["warning_bg"], fg=self.colors["warning"],
                         font=("Segoe UI", 8), padx=8, pady=5, anchor="w").pack(fill="x")

    def open_about_dialog(self):
        about_window = tk.Toplevel(self.root)
        about_window.title(f"About {APP_NAME}")
        about_window.geometry("460x320")
        about_window.resizable(False, False)
        about_window.configure(bg=self.colors["bg"])
        about_window.transient(self.root)
        about_window.grab_set()
        # Force theme colors on Toplevel (needed on some Windows versions)
        about_window.tk_setPalette(
            background=self.colors["bg"],
            foreground=self.colors["text"],
            activeBackground=self.colors["panel_2"],
            activeForeground=self.colors["text"],
        )

        icon_path = self.get_icon_path()
        if icon_path is not None:
            try:
                about_window.iconbitmap(str(icon_path))
            except Exception:
                pass

        outer = tk.Frame(about_window, bg=self.colors["bg"])
        outer.pack(fill="both", expand=True, padx=28, pady=24)

        # App name + version
        tk.Label(outer, text=APP_NAME,
                 bg=self.colors["bg"], fg=self.colors["text"],
                 font=("Segoe UI", 20, "bold")).pack(anchor="w")

        tk.Label(outer, text=f"v{APP_VERSION}",
                 bg=self.colors["bg"], fg=self.colors["stat_blue"],
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(2, 10))

        tk.Label(outer, text=APP_TAGLINE,
                 bg=self.colors["bg"], fg=self.colors["muted"],
                 font=("Segoe UI", 9), wraplength=380, justify="left",
                 ).pack(anchor="w", pady=(0, 14))

        # Divider
        tk.Frame(outer, bg=self.colors["border_2"], height=1).pack(fill="x", pady=(0, 12))

        # Info rows
        def _row(label, value, color=None):
            row = tk.Frame(outer, bg=self.colors["bg"])
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=self.colors["bg"],
                     fg=self.colors["muted"], font=("Segoe UI", 9),
                     width=12, anchor="w").pack(side="left")
            tk.Label(row, text=value, bg=self.colors["bg"],
                     fg=color or self.colors["text"],
                     font=("Segoe UI", 9, "bold")).pack(side="left")

        _row("Developer:", APP_DEVELOPER)
        if APP_EMAIL:
            _row("Email:", APP_EMAIL, self.colors["stat_blue"])
        _row("Copyright:", APP_COPYRIGHT)

        tk.Frame(outer, bg=self.colors["border_2"], height=1).pack(fill="x", pady=(14, 12))

        tk.Button(outer, text="Close",
                  bg=self.colors["accent"], fg="white",
                  activebackground=self.colors["accent_2"],
                  activeforeground="white",
                  relief="flat", bd=0, padx=20, pady=6,
                  font=("Segoe UI", 9, "bold"), cursor="hand2",
                  command=about_window.destroy).pack(anchor="e")

    def update_rules_count(self):
        self.rules_count_var.set(str(len(self.config.get("rules", {}))))

    def _update_notif_badge(self):
        """Update the Notifications nav button with live unread count."""
        try:
            count = self.notification_center.count()
            if count > 0:
                self.nav_buttons["notifications"].config(
                    text=f"🔔  Notifications  ({count})"
                )
            else:
                self.nav_buttons["notifications"].config(
                    text="🔔  Notifications"
                )
        except Exception:
            pass

    def browse_source_folder(self):
        folder = filedialog.askdirectory(title="Select Incoming Folder")
        if folder:
            self.source_folder_var.set(folder)

    def browse_organized_folder(self):
        folder = filedialog.askdirectory(title="Select Organized Base Folder")
        if folder:
            self.organized_base_var.set(folder)

    def refresh_stats(self):
        history_path = Path(self.config["history_file"])
        stats_path = Path(self.config["stats_file"])

        try:
            duplicate_count = 0
            plugin_count = 0
            smart_count = 0
            content_count = 0
            total_processed_count = 0
            failed_count = 0
            documents_count = 0
            category_counter = {}
            extension_count = 0
            
            # Read stats.json only if needed for extra details
            stats = {}
            if stats_path.exists():
                try:
                    with open(stats_path, "r", encoding="utf-8") as file:
                        stats = json.load(file)
                except Exception:
                    stats = {}

            if history_path.exists():
                with open(history_path, "r", encoding="utf-8", errors="ignore") as file:
                    reader = csv.DictReader(file)

                    for row in reader:
                        status = row.get("status", "").strip().lower()
                        category = row.get("category", "").strip().lower()
                        method = row.get("classification_method", "").strip().lower()
                        smart_source = row.get("smart_source", "").strip().lower()

                        # Total files processed by the system
                        if status in ("moved", "success", "duplicate_skipped"):
                            total_processed_count += 1

                        # Failed files
                        if status in ("failed", "hash_check_failed", "disappeared"):
                            failed_count += 1

                        # Duplicates
                        if status == "duplicate_skipped":
                            duplicate_count += 1

                        # Analytics counters
                        if status in ("moved", "success", "duplicate_skipped"):
                            if category:
                                category_counter[category] = category_counter.get(category, 0) + 1

                            if method == "plugin":
                                plugin_count += 1
                            elif method == "smart":
                                smart_count += 1
                            elif method == "extension":
                                extension_count += 1

                            if smart_source:
                                content_count += 1

                            # Approximate document count from common categories
                            if category in ("documents", "pdfs", "reports", "contracts", "resumes", "presentations", "notes", "invoices"):
                                documents_count += 1

            # Populate stat variables
            self.total_files_var.set(str(total_processed_count))
            self.failed_files_var.set(str(failed_count))
            self.duplicates_var.set(str(duplicate_count))
            self.documents_var.set(str(documents_count))
            self.plugin_classified_var.set(str(plugin_count))
            self.smart_classified_var.set(str(smart_count))
            self.content_classified_var.set(str(content_count))
            self.extension_classified_var.set(str(extension_count))
            
            if category_counter:
                top_category = max(category_counter, key=category_counter.get)
                self.top_category_var.set(top_category)
            else:
                self.top_category_var.set("-")

            self.draw_classification_chart(
                plugin_count=plugin_count,
                smart_count=smart_count,
                content_count=content_count,
                extension_count=extension_count
            )

            self.refresh_top_categories_view(category_counter)
            self.refresh_recent_activity_view()

            # Update breakdown cards
            self._refresh_breakdown_cards(
                total_processed_count, plugin_count, smart_count,
                content_count, extension_count, duplicate_count
            )

            # Show/hide Getting Started banner
            if hasattr(self, "getting_started_frame"):
                if total_processed_count == 0:
                    self.getting_started_frame.pack(fill="x", padx=20, pady=(0, 16))
                else:
                    self.getting_started_frame.pack_forget()

            self.status_bar_var.set("Statistics refreshed.")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to load stats:\n{error}")


    def _refresh_breakdown_cards(self, total, plugin, smart, content, extension, duplicates):
        """Rebuild the classification breakdown cards."""
        if not hasattr(self, "breakdown_frame"):
            return

        for w in self.breakdown_frame.winfo_children():
            w.destroy()

        if total == 0:
            tk.Label(self.breakdown_frame,
                     text="No files processed yet.",
                     bg=self.colors["card"], fg=self.colors["muted"],
                     font=("Segoe UI", 9), pady=6, anchor="w").pack(fill="x")
            return

        items = [
            ("By Extension",  extension,  self.colors["muted"],       "Files sorted by their file type (.pdf, .jpg...)"),
            ("By Keywords",   smart,      self.colors["stat_green"],  "Files sorted by keywords in filename"),
            ("By Content",    content,    self.colors["stat_blue"],   "Files sorted after reading content"),
            ("By Plugin",     plugin,     self.colors["stat_purple"], "Files sorted by installed plugins"),
            ("Duplicates",    duplicates, self.colors["stat_amber"],  "Files skipped — already organized"),
        ]

        for label, count, color, desc in items:
            if count == 0:
                continue

            row = tk.Frame(self.breakdown_frame, bg=self.colors["card"])
            row.pack(fill="x", pady=2)

            # Color dot
            dot = tk.Canvas(row, width=8, height=8, bg=self.colors["card"], highlightthickness=0)
            dot.pack(side="left", padx=(0, 8), pady=6)
            dot.create_oval(1, 1, 7, 7, fill=color, outline="")

            # Label
            tk.Label(row, text=label,
                     bg=self.colors["card"], fg=self.colors["text"],
                     font=("Segoe UI", 9), width=14, anchor="w").pack(side="left")

            # Count
            tk.Label(row, text=str(count),
                     bg=self.colors["card"], fg=color,
                     font=("Segoe UI", 10, "bold"), width=5, anchor="w").pack(side="left")

            # Progress bar
            pct = count / max(total, 1)
            bar_outer = tk.Frame(row, bg=self.colors["border"], width=200, height=6)
            bar_outer.pack(side="left", padx=(8, 12))
            bar_outer.pack_propagate(False)
            bar_fill = tk.Frame(bar_outer, bg=color, height=6)
            bar_fill.place(relx=0, rely=0, relwidth=pct, relheight=1)

            # Description
            tk.Label(row, text=desc,
                     bg=self.colors["card"], fg=self.colors["muted"],
                     font=("Segoe UI", 8), anchor="w").pack(side="left")

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
                status = row.get("status", "")
                tag = status if status in ("moved", "duplicate_skipped", "failed", "disappeared") else ""
                self.history_tree.insert(
                    "",
                    tk.END,
                    values=(
                        row.get("filename", ""),
                        row.get("category", ""),
                        status,
                        row.get("timestamp", ""),
                    ),
                    tags=(tag,) if tag else (),
                )
        else:
            self.history_empty_label.pack(expand=True)

    def clear_history_filters(self):
        self.history_search_var.set("")
        self.history_category_var.set("All")
        self.history_status_var.set("All")
        self.apply_history_filters()
        self.toast_manager.show_toast("History filters cleared.", "info")

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
        self.rules_inner_frame.grid_columnconfigure(1, weight=1)

        for index, (category, extensions) in enumerate(self.config.get("rules", {}).items()):
            tk.Label(self.rules_inner_frame, text=category,
                     bg=self.colors["card"], fg=self.colors["text"],
                     font=("Segoe UI", 9), width=16, anchor="w").grid(
                     row=index, column=0, padx=(14, 8), pady=5, sticky="w")

            entry_var = tk.StringVar(value=", ".join(extensions))
            ttk.Entry(self.rules_inner_frame, textvariable=entry_var).grid(
                row=index, column=1, padx=8, pady=5, sticky="ew")

            self.rule_entries[category] = entry_var

            tk.Button(self.rules_inner_frame, text="Delete",
                      bg=self.colors["panel_2"], fg=self.colors["muted"],
                      activebackground=self.colors["border_2"],
                      relief="flat", bd=0, padx=10, pady=4,
                      font=("Segoe UI", 8), cursor="hand2",
                      command=lambda c=category: self.delete_rule(c)).grid(
                      row=index, column=2, padx=(8, 14), pady=5, sticky="e")

    def normalize_smart_keywords(self, keywords_text: str):
        keywords = [kw.strip().lower() for kw in keywords_text.split(",") if kw.strip()]
        normalized = []
        seen = set()

        for keyword in keywords:
            if keyword not in seen:
                normalized.append(keyword)
                seen.add(keyword)

        return normalized

    def render_smart_rule_entries(self):
        if not hasattr(self, "smart_rules_frame"):
            return

        for widget in self.smart_rules_frame.winfo_children():
            widget.destroy()

        self.smart_rule_entries = {}
        self.smart_rules_frame.grid_columnconfigure(1, weight=1)
        smart_rules = load_smart_rules()

        for index, (category, keywords) in enumerate(smart_rules.items()):
            tk.Label(self.smart_rules_frame, text=category,
                     bg=self.colors["card"], fg=self.colors["text"],
                     font=("Segoe UI", 9), width=16, anchor="w").grid(
                     row=index, column=0, padx=(14, 8), pady=5, sticky="w")

            kw_text = ", ".join(keywords) if isinstance(keywords, list) else str(keywords)
            entry_var = tk.StringVar(value=kw_text)
            ttk.Entry(self.smart_rules_frame, textvariable=entry_var).grid(
                row=index, column=1, padx=8, pady=5, sticky="ew")

            self.smart_rule_entries[category] = entry_var

            tk.Button(self.smart_rules_frame, text="Delete",
                      bg=self.colors["panel_2"], fg=self.colors["muted"],
                      activebackground=self.colors["border_2"],
                      relief="flat", bd=0, padx=10, pady=4,
                      font=("Segoe UI", 8), cursor="hand2",
                      command=lambda c=category: self.delete_smart_rule(c)).grid(
                      row=index, column=2, padx=(8, 14), pady=5, sticky="e")


    def add_new_smart_rule(self):
        category = self.new_smart_category_var.get().strip().lower()
        keywords_text = self.new_smart_keywords_var.get().strip()

        if not category:
            messagebox.showerror("Error", "Smart category name cannot be empty.")
            return

        smart_rules = load_smart_rules()

        if category in smart_rules:
            messagebox.showerror("Error", "This smart category already exists.")
            return

        normalized_keywords = self.normalize_smart_keywords(keywords_text)

        if not normalized_keywords:
            messagebox.showerror("Error", "Please enter at least one keyword.")
            return

        smart_rules[category] = normalized_keywords
        save_smart_rules(smart_rules)

        self.new_smart_category_var.set("")
        self.new_smart_keywords_var.set("")
        self.render_smart_rule_entries()
        self.toast_manager.show_toast(f"Added smart rule '{category}'.", "success")
        self.status_bar_var.set(f"Added smart rule: {category}")

    def delete_smart_rule(self, category):
        confirm = messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete smart rule '{category}'?"
        )
        if not confirm:
            return

        smart_rules = load_smart_rules()

        if category in smart_rules:
            del smart_rules[category]
            save_smart_rules(smart_rules)

        self.render_smart_rule_entries()
        self.toast_manager.show_toast(f"Deleted smart rule '{category}'.", "warning")
        self.status_bar_var.set(f"Deleted smart rule: {category}")

    def save_smart_rules_from_gui(self):
        try:
            updated_rules = {}

            for category, entry_var in self.smart_rule_entries.items():
                keywords = self.normalize_smart_keywords(entry_var.get().strip())

                if keywords:
                    updated_rules[category] = keywords

            save_smart_rules(updated_rules)
            self.render_smart_rule_entries()
            self.toast_manager.show_toast("Smart rules saved successfully.", "success")
            self.status_bar_var.set("Smart rules saved successfully.")
            self.add_notification("success", "Smart Rules", "Smart rules saved successfully.")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to save smart rules:\n{error}")
            self.add_notification("error", "Smart Rules Error", str(error))

    def reload_smart_rules_from_gui(self):
        try:
            self.render_smart_rule_entries()
            self.toast_manager.show_toast("Smart rules reloaded.", "info")
            self.status_bar_var.set("Smart rules reloaded.")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to reload smart rules:\n{error}")

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
            self.add_notification("success", "Rules Saved", "Rules saved successfully.")
            self.reload_rules()

        except Exception as error:
            messagebox.showerror("Error", f"Failed to save rules:\n{error}")
            self.add_notification("error", "Rules Error", str(error))

    def reload_rules(self):
        try:
            config_path = get_config_path()

            with open(config_path, "r", encoding="utf-8") as file:
                config_data = json.load(file)

            self.config["rules"] = config_data.get("rules", {})
            self.render_rule_entries()
            self.update_rules_count()
            self.status_bar_var.set("Rules reloaded successfully.")
            self.toast_manager.show_toast("Rules reloaded.", "info")
            self.add_notification("success", "Rules Saved", "Rules saved successfully.")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to reload rules:\n{error}")
            self.add_notification("error", "Reload Rules Error", str(error))

    def save_settings(self):
        try:
            # --- Validation ---
            source_folder = self.source_folder_var.get().strip()
            organized_folder = self.organized_base_var.get().strip()

            if not source_folder:
                messagebox.showerror("Validation Error", "Incoming folder path cannot be empty.")
                return
            if not organized_folder:
                messagebox.showerror("Validation Error", "Organized base folder path cannot be empty.")
                return
            if source_folder == organized_folder:
                messagebox.showerror("Validation Error", "Incoming and organized folders cannot be the same path.")
                return

            try:
                wait_seconds = int(self.processing_wait_var.get())
                if wait_seconds < 1 or wait_seconds > 300:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Validation Error", "Processing wait must be a number between 1 and 300.")
                return

            try:
                dup_window = int(self.duplicate_window_var.get())
                if dup_window < 1 or dup_window > 60:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Validation Error", "Duplicate event window must be a number between 1 and 60.")
                return

            # --- Save ---
            config_path = get_config_path()

            with open(config_path, "r", encoding="utf-8") as file:
                config_data = json.load(file)

            config_data["source_folder"] = source_folder
            config_data["organized_base_folder"] = organized_folder
            config_data["processing_wait_seconds"] = wait_seconds
            config_data["duplicate_event_window_seconds"] = dup_window
            config_data["archive_by_date"] = bool(self.archive_by_date_var.get())
            config_data["run_at_startup"] = bool(self.run_at_startup_var.get())
            config_data["language"] = get_language()

            with open(config_path, "w", encoding="utf-8") as file:
                json.dump(config_data, file, indent=2, ensure_ascii=False)

            self.status_bar_var.set(t("msg_settings_saved"))
            self.toast_manager.show_toast("Settings saved successfully.", "success")
            self.add_notification("success", "Settings Saved", "Settings saved successfully.")
            self.reload_settings()

        except Exception as error:
            messagebox.showerror("Error", f"Failed to save settings:\n{error}")
            self.add_notification("error", "Settings Error", str(error))

    def reload_settings(self):
        try:
            was_running = self.monitor.is_running
            if was_running:
                self._stop_dot_pulse()
                self._stop_auto_refresh()
                self.monitor.stop()

            self.config, self.monitor = build_monitor()

            self.source_folder_var.set(self.config.get("source_folder", "incoming"))
            self.organized_base_var.set(self.config.get("organized_base_folder", "organized"))
            self.processing_wait_var.set(str(self.config.get("processing_wait_seconds", 5)))
            self.duplicate_window_var.set(str(self.config.get("duplicate_event_window_seconds", 3)))
            self.archive_by_date_var.set(self.config.get("archive_by_date", False))
            from app.startup_manager import is_startup_enabled
            self.run_at_startup_var.set(is_startup_enabled())

            self.refresh_stats()
            self.refresh_history()
            self.render_rule_entries()
            self.render_smart_rule_entries()
            self.update_rules_count()
            self.refresh_plugins_view()

            if was_running:
                self.monitor.set_file_processed_callback(self._make_live_callback())
                self.monitor.start()
                self.status_var.set(t("status_running"))
                try:
                    self.header_status.config(text="Running", bg=self.colors["success_bg"], fg=self.colors["success"])
                    self._status_badge.config(bg=self.colors["success_bg"], highlightbackground=self.colors["success_border"])
                    self._status_dot.config(bg=self.colors["success_bg"])
                except Exception:
                    pass
                self.start_button.config(state="disabled")
                self.stop_button.config(state="normal")
                self._start_dot_pulse()
                self._start_auto_refresh()
                self.status_bar_var.set("Settings reloaded — monitoring restarted.")
            else:
                self.status_var.set(t("status_stopped"))
                try:
                    self.header_status.config(text="Stopped", bg=self.colors["danger"], fg=self.colors["danger_fg"])
                    self._status_badge.config(bg=self.colors["danger"], highlightbackground=self.colors["danger_border"])
                    self._status_dot.config(bg=self.colors["danger"])
                except Exception:
                    pass
                self.start_button.config(state="normal")
                self.stop_button.config(state="disabled")
                self.status_bar_var.set("Settings reloaded successfully.")

            self.add_notification("info", "Settings Reloaded", "Settings reloaded successfully.")
            self.toast_manager.show_toast("Settings reloaded.", "info")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to reload settings:\n{error}")
            self.add_notification("error", "Reload Settings Error", str(error))

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
            source_path = get_config_path()
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
            self.add_notification("success", "Config Exported", "Configuration exported successfully.")
            
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
            self.add_notification("success", "Config Imported", "Configuration imported successfully.")

        except Exception as error:
            messagebox.showerror("Error", f"Failed to import config:\n{error}")
            self.add_notification("error", "Config Import Error", str(error))

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
            self.add_notification("warning", "Statistics Reset", "Statistics reset successfully.")

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
            self.add_notification("warning", "Hash Database Reset", "Hash database reset successfully.")

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
        from app.config_loader import resolve_runtime_path
        path = resolve_runtime_path("reports")
        if path.exists():
            os.startfile(path)
            self.status_bar_var.set("Opened reports folder.")
        else:
            messagebox.showwarning("Warning", f"Reports folder does not exist:\n{path}")

    def refresh_plugins_view(self):
        if not hasattr(self, "plugins_tree") or not hasattr(self, "failed_plugins_tree"):
            return

        for item in self.plugins_tree.get_children():
            self.plugins_tree.delete(item)

        for item in self.failed_plugins_tree.get_children():
            self.failed_plugins_tree.delete(item)

        loaded_plugins = self.config.get("loaded_plugins", [])
        failed_plugins = self.config.get("failed_plugins", [])

        self.plugins_loaded_count_var.set(str(len(loaded_plugins)))
        self.plugins_failed_count_var.set(str(len(failed_plugins)))

        for plugin in loaded_plugins:
            self.plugins_tree.insert(
                "",
                tk.END,
                values=(
                    plugin.get("name", ""),
                    plugin.get("version", ""),
                    plugin.get("description", ""),
                    plugin.get("status", ""),
                ),
            )

        for plugin in failed_plugins:
            self.failed_plugins_tree.insert(
                "",
                tk.END,
                values=(
                    plugin.get("file", ""),
                    plugin.get("reason", ""),
                ),
            )


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

    # ── Watch Folders ─────────────────────────────────────────────────────────

    def refresh_watch_folders_list(self) -> None:
        """Rebuild the folder rows in the Watch Folders page."""
        if not hasattr(self, "watch_folders_list_frame"):
            return

        frame = self.watch_folders_list_frame
        for w in frame.winfo_children():
            w.destroy()

        folders = self.config.get("watch_folders", [])

        if not folders:
            tk.Label(frame, text="  No watch folders configured.",
                     bg=self.colors["card"], fg=self.colors["muted"],
                     font=("Segoe UI", 9), pady=10, anchor="w").pack(fill="x", padx=12)
            return

        for i, folder in enumerate(folders):
            path = folder.get("path", "")
            label = folder.get("label", "") or Path(path).name
            status = self.monitor.folder_status(path) if hasattr(self, "monitor") else "stopped"

            row_bg = self.colors["card"] if i % 2 == 0 else self.colors["card_2"]
            row = tk.Frame(frame, bg=row_bg)
            row.pack(fill="x")

            # Status dot
            dot_color = self.colors["stat_green"] if status == "running" else self.colors["muted"]
            dot_c = tk.Canvas(row, width=8, height=8, bg=row_bg, highlightthickness=0)
            dot_c.pack(side="left", padx=(12, 6), pady=10)
            dot_c.create_oval(1, 1, 7, 7, fill=dot_color, outline="")

            status_txt = "Running" if status == "running" else "Stopped"
            tk.Label(row, text=status_txt, bg=row_bg, fg=dot_color,
                     font=("Segoe UI", 8, "bold"), width=7, anchor="w").pack(side="left", padx=(0, 8))

            tk.Label(row, text=label, bg=row_bg, fg=self.colors["text"],
                     font=("Segoe UI", 9, "bold"), width=16, anchor="w").pack(side="left")

            short_path = path if len(path) <= 50 else "..." + path[-47:]
            tk.Label(row, text=short_path, bg=row_bg, fg=self.colors["muted"],
                     font=("Segoe UI", 9), anchor="w").pack(side="left", fill="x", expand=True)

            btn_f = tk.Frame(row, bg=row_bg)
            btn_f.pack(side="right", padx=8)

            def _start(p=path):
                self.monitor.start_folder(p)
                self.refresh_watch_folders_list()
                self._update_status_badge_running()

            def _stop(p=path):
                self.monitor.stop_folder(p)
                self.refresh_watch_folders_list()
                if not self.monitor.is_running:
                    self._update_status_badge_stopped()

            def _remove(p=path):
                name = Path(p).name
                msg = f"Remove '{name}' from watch list?\nFiles will NOT be deleted."
                if messagebox.askyesno("Remove Folder", msg):
                    self.monitor.remove_watch_folder(p)
                    self._save_watch_folders_to_config()
                    self.refresh_watch_folders_list()

            if status == "running":
                tk.Button(btn_f, text="Stop",
                          bg=self.colors["danger"], fg=self.colors["danger_fg"],
                          relief="flat", bd=0, padx=8, pady=3,
                          font=("Segoe UI", 8, "bold"), cursor="hand2",
                          command=_stop).pack(side="left", padx=(0, 4))
            else:
                tk.Button(btn_f, text="Start",
                          bg=self.colors["accent"], fg="white",
                          relief="flat", bd=0, padx=8, pady=3,
                          font=("Segoe UI", 8, "bold"), cursor="hand2",
                          command=_start).pack(side="left", padx=(0, 4))

            tk.Button(btn_f, text="Remove",
                      bg=self.colors["panel_2"], fg=self.colors["muted"],
                      relief="flat", bd=0, padx=8, pady=3,
                      font=("Segoe UI", 8), cursor="hand2",
                      command=_remove).pack(side="left")

            tk.Frame(frame, bg=self.colors["border"], height=1).pack(fill="x")

    def add_watch_folder_from_gui(self) -> None:
        """Add new folder from Watch Folders page inputs."""
        path_var  = getattr(self, "new_watch_path_var", None)
        label_var = getattr(self, "new_watch_label_var", None)
        if not path_var:
            return

        path_str  = path_var.get().strip()
        label_str = label_var.get().strip() if label_var else ""

        if not path_str:
            self.toast_manager.show_toast("Please enter a folder path.", "error")
            return

        if not Path(path_str).exists():
            self.toast_manager.show_toast(f"Folder does not exist: {path_str}", "error")
            return

        added = self.monitor.add_watch_folder(path_str, label=label_str, active=True)
        if not added:
            self.toast_manager.show_toast("Folder already in watch list.", "error")
            return

        self._save_watch_folders_to_config()
        self.refresh_watch_folders_list()
        path_var.set("")
        if label_var:
            label_var.set("")
        self.toast_manager.show_toast(f"Added: {Path(path_str).name}", "success")

    def _save_watch_folders_to_config(self) -> None:
        """Persist watch_folders list to config.json."""
        try:
            cfg_path = get_config_path()
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["watch_folders"] = self.config.get("watch_folders", [])
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.toast_manager.show_toast(f"Could not save: {e}", "error")

    def _update_status_badge_running(self) -> None:
        try:
            self.header_status.config(text=t("status_running"),
                                      bg=self.colors["success_bg"],
                                      fg=self.colors["success"])
            self._status_badge.config(bg=self.colors["success_bg"],
                                      highlightbackground=self.colors["success_border"])
            self._status_dot.config(bg=self.colors["success_bg"])
        except Exception:
            pass

    def _update_status_badge_stopped(self) -> None:
        try:
            self.header_status.config(text=t("status_stopped"),
                                      bg=self.colors["danger"],
                                      fg=self.colors["danger_fg"])
            self._status_badge.config(bg=self.colors["danger"],
                                      highlightbackground=self.colors["danger_border"])
            self._status_dot.config(bg=self.colors["danger"])
        except Exception:
            pass

    # ── Plugin Marketplace ────────────────────────────────────────────────────

    def _get_marketplace(self):
        """Return (or create) the shared marketplace instance."""
        if not hasattr(self, "_marketplace_instance"):
            from app.config_loader import get_plugins_dir
            from app.plugin_marketplace import PluginMarketplace
            self._marketplace_instance = PluginMarketplace(get_plugins_dir())
        return self._marketplace_instance

    def _load_marketplace_registry(self) -> None:
        """Fetch registry in background, then build plugin cards."""
        mkt = self._get_marketplace()

        def _on_done(plugins, error):
            self.root.after(0, lambda: self._render_marketplace_cards(plugins, error))

        mkt.fetch_registry(_on_done)

    def _render_marketplace_cards(self, plugins, error) -> None:
        """Render plugin cards in the marketplace frame."""
        if not hasattr(self, "mkt_cards_frame"):
            return
        for w in self.mkt_cards_frame.winfo_children():
            w.destroy()

        if error:
            self.mkt_status_var.set(f"Error loading registry")
            err_msg = f"Could not load registry: {error}"
            tk.Label(self.mkt_cards_frame, text=err_msg,
                     bg=self.colors["card"], fg=self.colors["stat_red"],
                     font=("Segoe UI", 9), pady=8, justify="left").pack(anchor="w")
            return

        from app.plugin_marketplace import STATUS_NOT_INSTALLED, STATUS_INSTALLED, STATUS_UPDATABLE
        mkt = self._get_marketplace()
        self.mkt_status_var.set(f"{len(plugins)} plugins available")

        if not plugins:
            tk.Label(self.mkt_cards_frame, text="No plugins found.",
                     bg=self.colors["card"], fg=self.colors["muted"],
                     font=("Segoe UI", 9), pady=8).pack(anchor="w")
            return

        for plugin in plugins:
            self._build_plugin_card(plugin, mkt)

    def _build_plugin_card(self, plugin, mkt) -> None:
        """Build one plugin card row."""
        from app.plugin_marketplace import STATUS_NOT_INSTALLED, STATUS_INSTALLED, STATUS_UPDATABLE
        status = mkt.get_plugin_status(plugin)

        status_colors = {
            STATUS_INSTALLED:     self.colors["stat_green"],
            STATUS_UPDATABLE:     self.colors["stat_amber"],
            STATUS_NOT_INSTALLED: self.colors["muted"],
        }
        sc = status_colors.get(status, self.colors["muted"])

        card = tk.Frame(self.mkt_cards_frame, bg=self.colors["card_2"],
                        highlightbackground=self.colors["border"], highlightthickness=1)
        card.pack(fill="x", pady=(0, 6))

        info = tk.Frame(card, bg=self.colors["card_2"])
        info.pack(side="left", fill="both", expand=True, padx=14, pady=10)

        name_row = tk.Frame(info, bg=self.colors["card_2"])
        name_row.pack(fill="x")
        tk.Label(name_row, text=plugin.get("name", plugin["id"]),
                 bg=self.colors["card_2"], fg=self.colors["text"],
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        tk.Label(name_row, text=f"  v{plugin.get('version', '?')}",
                 bg=self.colors["card_2"], fg=self.colors["muted"],
                 font=("Segoe UI", 9)).pack(side="left")
        tk.Label(name_row, text=f"  {status}",
                 bg=self.colors["card_2"], fg=sc,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(8, 0))

        tk.Label(info, text=f"by {plugin.get('author', 'Unknown')}",
                 bg=self.colors["card_2"], fg=self.colors["muted"],
                 font=("Segoe UI", 8)).pack(anchor="w")

        desc = plugin.get("description", "")
        if len(desc) > 90:
            desc = desc[:87] + "..."
        tk.Label(info, text=desc, bg=self.colors["card_2"], fg=self.colors["muted"],
                 font=("Segoe UI", 9), wraplength=480, justify="left").pack(anchor="w", pady=(2, 0))

        if plugin.get("tags"):
            tags_row = tk.Frame(info, bg=self.colors["card_2"])
            tags_row.pack(fill="x", pady=(4, 0))
            for tag in plugin["tags"][:4]:
                tag_f = tk.Frame(tags_row, bg=self.colors["panel_2"],
                                 highlightbackground=self.colors["border"], highlightthickness=1)
                tag_f.pack(side="left", padx=(0, 4))
                tk.Label(tag_f, text=tag, bg=self.colors["panel_2"],
                         fg=self.colors["muted"], font=("Segoe UI", 7),
                         padx=6, pady=2).pack()

        btn_f = tk.Frame(card, bg=self.colors["card_2"])
        btn_f.pack(side="right", padx=14, pady=10)

        def _install(p=plugin): self._marketplace_action(p, is_update=False)
        def _update(p=plugin):  self._marketplace_action(p, is_update=True)
        def _remove(p=plugin):  self._marketplace_remove(p)

        if status == STATUS_NOT_INSTALLED:
            tk.Button(btn_f, text="Install",
                      bg=self.colors["accent"], fg="white",
                      activebackground=self.colors["accent_2"], activeforeground="white",
                      relief="flat", bd=0, padx=14, pady=6,
                      font=("Segoe UI", 9, "bold"), cursor="hand2",
                      command=_install).pack()
        elif status == STATUS_UPDATABLE:
            tk.Button(btn_f, text="Update",
                      bg=self.colors["stat_amber"], fg="#1a1a1a",
                      relief="flat", bd=0, padx=14, pady=6,
                      font=("Segoe UI", 9, "bold"), cursor="hand2",
                      command=_update).pack(pady=(0, 4))
            tk.Button(btn_f, text="Remove",
                      bg=self.colors["panel_2"], fg=self.colors["muted"],
                      relief="flat", bd=0, padx=14, pady=4,
                      font=("Segoe UI", 8), cursor="hand2",
                      command=_remove).pack()
        else:
            tk.Button(btn_f, text="Installed",
                      bg=self.colors["success_bg"], fg=self.colors["stat_green"],
                      relief="flat", bd=0, padx=14, pady=6,
                      font=("Segoe UI", 9, "bold"), state="disabled").pack(pady=(0, 4))
            tk.Button(btn_f, text="Remove",
                      bg=self.colors["panel_2"], fg=self.colors["muted"],
                      relief="flat", bd=0, padx=14, pady=4,
                      font=("Segoe UI", 8), cursor="hand2",
                      command=_remove).pack()

    def _marketplace_action(self, plugin, is_update=False) -> None:
        """Install or update a plugin."""
        action = "Updating" if is_update else "Installing"
        name = plugin.get("name", plugin["id"])
        self.mkt_status_var.set(f"{action} {name}...")
        mkt = self._get_marketplace()

        def _on_done(result):
            def _ui():
                if result.ok:
                    self.mkt_status_var.set(result.message)
                    self.toast_manager.show_toast(result.message, "success")
                    self.reload_plugins_from_gui()
                    self._render_marketplace_cards(mkt.get_cached_registry(), None)
                else:
                    self.mkt_status_var.set("Install failed")
                    self.toast_manager.show_toast(result.message, "error")
            self.root.after(0, _ui)

        mkt.install(plugin, _on_done)

    def _marketplace_remove(self, plugin) -> None:
        """Remove a plugin after confirmation."""
        name = plugin.get("name", plugin["id"])
        if not messagebox.askyesno("Remove Plugin",
                                   f"Remove '{name}'?\nYou can reinstall it anytime."):
            return
        mkt = self._get_marketplace()
        result = mkt.remove(plugin["id"])
        if result.ok:
            self.toast_manager.show_toast(result.message, "success")
            self.reload_plugins_from_gui()
            self._render_marketplace_cards(mkt.get_cached_registry(), None)
        else:
            self.toast_manager.show_toast(result.message, "error")

    # ── AI Classifier ─────────────────────────────────────────────────────────

    def check_ai_status(self) -> None:
        """Check AI provider availability and update status label."""
        if not hasattr(self, "ai_status_var"):
            return
        try:
            from app.ai_classifier import AIClassifier
            provider = getattr(self, "ai_provider_var", None)
            provider_name = provider.get() if provider else "ollama"

            api_key = ""
            if provider_name == "claude" and hasattr(self, "claude_api_key_var"):
                api_key = self.claude_api_key_var.get()

            ai = AIClassifier(provider=provider_name, claude_api_key=api_key)
            active = ai.get_active_provider()

            if active == "none":
                if provider_name == "ollama":
                    self.ai_status_var.set("! Ollama not running — start Ollama app")
                else:
                    self.ai_status_var.set("! Invalid API key")
            else:
                self.ai_status_var.set(f"+ Connected ({active})")
        except Exception as e:
            self.ai_status_var.set(f"! Error: {e}")

    def test_ai_connection(self) -> None:
        """Test AI connection with a sample classification."""
        if not hasattr(self, "ai_status_var"):
            return

        self.ai_status_var.set("Testing...")

        def _test():
            try:
                from app.ai_classifier import AIClassifier
                provider = getattr(self, "ai_provider_var", None)
                provider_name = provider.get() if provider else "ollama"
                api_key = ""
                if hasattr(self, "claude_api_key_var"):
                    api_key = self.claude_api_key_var.get()

                ai = AIClassifier(provider=provider_name, claude_api_key=api_key)
                result = ai.classify("invoice_march_2024.pdf",
                                     ["invoices", "documents", "finance"])
                if result.ok:
                    msg = f"+ OK: '{result.category}' — {result.reason}"
                else:
                    msg = f"! Failed: {result.error}"
                self.root.after(0, lambda: self.ai_status_var.set(msg))
            except Exception as e:
                self.root.after(0, lambda: self.ai_status_var.set(f"! {e}"))

        import threading
        threading.Thread(target=_test, daemon=True).start()

    def suggest_ai_rules(self) -> None:
        """Ask AI to analyze history and suggest new rules."""
        if not hasattr(self, "history_rows_cache"):
            self.toast_manager.show_toast("No history yet to analyze.", "error")
            return

        self.toast_manager.show_toast("Analyzing history with AI...", "info")

        def _on_done(suggestions, error):
            def _ui():
                if error:
                    self.toast_manager.show_toast(f"AI error: {error}", "error")
                    return
                if not suggestions:
                    self.toast_manager.show_toast("No new rule suggestions.", "info")
                    return
                self._show_ai_suggestions(suggestions)
            self.root.after(0, _ui)

        try:
            from app.ai_classifier import get_ai_classifier
            ai = get_ai_classifier(self.config)
            ai.suggest_rules(self.history_rows_cache, _on_done)
        except Exception as e:
            self.toast_manager.show_toast(f"AI error: {e}", "error")

    def _show_ai_suggestions(self, suggestions) -> None:
        """Show AI rule suggestions in a popup."""
        import tkinter as tk
        from tkinter import ttk

        win = tk.Toplevel(self.root)
        win.title("AI Rule Suggestions")
        win.geometry("600x400")
        win.configure(bg=self.colors["bg"])
        win.transient(self.root)
        win.grab_set()

        tk.Label(win, text="AI Rule Suggestions",
                 bg=self.colors["bg"], fg=self.colors["text"],
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=20, pady=(16, 4))

        tk.Label(win, text="Based on your file history. Click Apply to add a rule.",
                 bg=self.colors["bg"], fg=self.colors["muted"],
                 font=("Segoe UI", 9)).pack(anchor="w", padx=20, pady=(0, 12))

        tk.Frame(win, bg=self.colors["border_2"], height=1).pack(fill="x")

        scroll_frame = tk.Frame(win, bg=self.colors["bg"])
        scroll_frame.pack(fill="both", expand=True, padx=20, pady=12)

        for s in suggestions:
            card = tk.Frame(scroll_frame, bg=self.colors["card_2"],
                            highlightbackground=self.colors["border"],
                            highlightthickness=1)
            card.pack(fill="x", pady=(0, 8))

            info = tk.Frame(card, bg=self.colors["card_2"])
            info.pack(side="left", fill="both", expand=True, padx=12, pady=8)

            tk.Label(info, text=s.category.upper(),
                     bg=self.colors["card_2"], fg=self.colors["stat_blue"],
                     font=("Segoe UI", 10, "bold")).pack(anchor="w")

            tk.Label(info, text=f"Keywords: {', '.join(s.keywords)}",
                     bg=self.colors["card_2"], fg=self.colors["muted"],
                     font=("Segoe UI", 8)).pack(anchor="w")

            tk.Label(info, text=s.reason,
                     bg=self.colors["card_2"], fg=self.colors["text"],
                     font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))

            conf_color = self.colors["stat_green"] if s.confidence >= 0.8 else self.colors["stat_amber"]
            tk.Label(info, text=f"Confidence: {int(s.confidence*100)}%",
                     bg=self.colors["card_2"], fg=conf_color,
                     font=("Segoe UI", 8)).pack(anchor="w")

            def _apply(sug=s):
                from app.smart_classifier import load_smart_rules, save_smart_rules
                rules = load_smart_rules()
                if sug.category not in rules:
                    rules[sug.category] = sug.keywords
                    save_smart_rules(rules)
                    self.toast_manager.show_toast(
                        f"Rule added: {sug.category}", "success"
                    )
                    self.render_smart_rule_entries()
                else:
                    self.toast_manager.show_toast(
                        f"Category '{sug.category}' already exists.", "info"
                    )

            tk.Button(card, text="Apply",
                      bg=self.colors["accent"], fg="white",
                      relief="flat", bd=0, padx=12, pady=6,
                      font=("Segoe UI", 9, "bold"), cursor="hand2",
                      command=_apply).pack(side="right", padx=12, pady=8)

        tk.Button(win, text="Close",
                  bg=self.colors["panel_2"], fg=self.colors["muted"],
                  relief="flat", bd=0, padx=16, pady=6,
                  font=("Segoe UI", 9), cursor="hand2",
                  command=win.destroy).pack(anchor="e", padx=20, pady=(0, 16))

    def save_ai_settings(self) -> None:
        """Save AI settings to config.json."""
        try:
            cfg_path = get_config_path()
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            cfg["ai"] = {
                "enabled":        getattr(self, "ai_enabled_var", tk.BooleanVar()).get()
                                  if hasattr(self, "ai_enabled_var") else False,
                "provider":       getattr(self, "ai_provider_var", tk.StringVar(value="ollama")).get()
                                  if hasattr(self, "ai_provider_var") else "ollama",
                "claude_api_key": getattr(self, "claude_api_key_var", tk.StringVar()).get()
                                  if hasattr(self, "claude_api_key_var") else "",
                "ollama_model":   "mistral",
            }

            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)

            from app.ai_classifier import reset_ai_classifier
            reset_ai_classifier()

        except Exception as e:
            logger.error(f"Failed to save AI settings: {e}")

    # ── AI Document Analysis ──────────────────────────────────────────────────

    def analyze_file_with_ai(self, file_path: str | None = None) -> None:
        """Open file picker and analyze selected file with AI."""
        if not file_path:
            from tkinter import filedialog
            file_path = filedialog.askopenfilename(
                title="Select file to analyze",
                filetypes=[
                    ("All supported", "*.pdf *.docx *.doc *.txt *.xlsx *.xls *.jpg *.jpeg *.png"),
                    ("PDF files", "*.pdf"),
                    ("Word files", "*.docx *.doc"),
                    ("Images", "*.jpg *.jpeg *.png"),
                    ("All files", "*.*"),
                ]
            )
        if not file_path:
            return

        self._show_ai_analysis_window(Path(file_path))

    def _show_ai_analysis_window(self, file_path: Path) -> None:
        """Show the AI document analysis window."""
        win = tk.Toplevel(self.root)
        win.title(f"AI Analysis — {file_path.name}")
        win.geometry("680x580")
        win.configure(bg=self.colors["bg"])
        win.transient(self.root)

        # Header
        header = tk.Frame(win, bg=self.colors["panel"])
        header.pack(fill="x")

        tk.Label(header, text="AI Document Analysis",
                 bg=self.colors["panel"], fg=self.colors["text"],
                 font=("Segoe UI", 13, "bold"),
                 padx=20, pady=12).pack(side="left")

        tk.Label(header, text=file_path.name,
                 bg=self.colors["panel"], fg=self.colors["muted"],
                 font=("Segoe UI", 9),
                 padx=20, pady=12).pack(side="left")

        tk.Frame(win, bg=self.colors["border_2"], height=1).pack(fill="x")

        # Loading state
        content_frame = tk.Frame(win, bg=self.colors["bg"])
        content_frame.pack(fill="both", expand=True, padx=20, pady=16)

        loading_lbl = tk.Label(content_frame,
                               text="Analyzing document with AI...",
                               bg=self.colors["bg"], fg=self.colors["muted"],
                               font=("Segoe UI", 11))
        loading_lbl.pack(expand=True)

        def _on_analysis_done(analysis):
            self.root.after(0, lambda: self._populate_analysis_window(
                win, content_frame, loading_lbl, analysis, file_path
            ))

        # Run analysis
        try:
            from app.ai_document_analyzer import AIDocumentAnalyzer
            from app.ai_classifier import get_ai_classifier
            ai = get_ai_classifier(self.config)
            analyzer = AIDocumentAnalyzer(ai_classifier=ai)
            categories = list(self.config.get("rules", {}).keys())
            analyzer.analyze_async(file_path, categories, _on_analysis_done)
        except Exception as e:
            loading_lbl.config(text=f"Error: {e}", fg=self.colors["stat_red"])

    def _populate_analysis_window(self, win, frame, loading_lbl, analysis, file_path) -> None:
        """Fill the analysis window with results."""
        loading_lbl.destroy()
        for w in frame.winfo_children():
            w.destroy()

        # Scrollable content
        canvas = tk.Canvas(frame, bg=self.colors["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=self.colors["bg"])
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.attach_safe_mousewheel(canvas, owner=win)

        c = scroll_frame

        # ── Type + Category ───────────────────────────────────────
        type_row = tk.Frame(c, bg=self.colors["card_2"],
                            highlightbackground=self.colors["border"],
                            highlightthickness=1)
        type_row.pack(fill="x", pady=(0, 10))

        tk.Label(type_row, text=analysis.doc_type.upper(),
                 bg=self.colors["stat_blue"], fg="white",
                 font=("Segoe UI", 9, "bold"),
                 padx=10, pady=5).pack(side="left")

        tk.Label(type_row, text=f"  Category: {analysis.category}",
                 bg=self.colors["card_2"], fg=self.colors["text"],
                 font=("Segoe UI", 9, "bold"),
                 padx=8, pady=5).pack(side="left")

        tk.Label(type_row, text=f"  Folder: {analysis.smart_folder}",
                 bg=self.colors["card_2"], fg=self.colors["muted"],
                 font=("Segoe UI", 9),
                 padx=8, pady=5).pack(side="left")

        # ── Summary ───────────────────────────────────────────────
        if analysis.summary:
            tk.Label(c, text=analysis.summary,
                     bg=self.colors["bg"], fg=self.colors["text"],
                     font=("Segoe UI", 10), wraplength=580,
                     justify="left").pack(anchor="w", pady=(0, 12))

        # ── Key dates ─────────────────────────────────────────────
        if analysis.key_dates:
            tk.Label(c, text="IMPORTANT DATES",
                     bg=self.colors["bg"], fg=self.colors["muted"],
                     font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 6))

            for date_info in analysis.key_dates:
                date_card = tk.Frame(c, bg=self.colors["card_2"],
                                     highlightbackground=self.colors["stat_amber"],
                                     highlightthickness=1)
                date_card.pack(fill="x", pady=(0, 6))

                info = tk.Frame(date_card, bg=self.colors["card_2"])
                info.pack(side="left", fill="both", expand=True, padx=12, pady=8)

                tk.Label(info, text=f"  {date_info.label}",
                         bg=self.colors["card_2"], fg=self.colors["stat_amber"],
                         font=("Segoe UI", 9, "bold")).pack(anchor="w")

                tk.Label(info, text=f"Date: {date_info.date}  |  {date_info.description}",
                         bg=self.colors["card_2"], fg=self.colors["muted"],
                         font=("Segoe UI", 8)).pack(anchor="w")

                def _add_reminder(di=date_info, fp=file_path):
                    self._add_calendar_reminder(di, fp.name)

                tk.Button(date_card, text="+ Add Reminder",
                          bg=self.colors["accent"], fg="white",
                          relief="flat", bd=0, padx=10, pady=5,
                          font=("Segoe UI", 8, "bold"), cursor="hand2",
                          command=_add_reminder).pack(side="right", padx=10, pady=8)

        # ── Entities ──────────────────────────────────────────────
        if analysis.entities:
            tk.Label(c, text="KEY INFORMATION",
                     bg=self.colors["bg"], fg=self.colors["muted"],
                     font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(12, 6))

            ent_frame = tk.Frame(c, bg=self.colors["card_2"],
                                 highlightbackground=self.colors["border"],
                                 highlightthickness=1)
            ent_frame.pack(fill="x", pady=(0, 12))

            for key, val in analysis.entities.items():
                row = tk.Frame(ent_frame, bg=self.colors["card_2"])
                row.pack(fill="x", padx=12, pady=3)
                tk.Label(row, text=f"{key.title()}:",
                         bg=self.colors["card_2"], fg=self.colors["muted"],
                         font=("Segoe UI", 9), width=12, anchor="w").pack(side="left")
                tk.Label(row, text=val,
                         bg=self.colors["card_2"], fg=self.colors["text"],
                         font=("Segoe UI", 9, "bold")).pack(side="left")

        # ── Tips ──────────────────────────────────────────────────
        if analysis.tips:
            tk.Label(c, text="AI TIPS",
                     bg=self.colors["bg"], fg=self.colors["muted"],
                     font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 6))

            for tip in analysis.tips:
                tip_row = tk.Frame(c, bg=self.colors["success_bg"],
                                   highlightbackground=self.colors["success_border"],
                                   highlightthickness=1)
                tip_row.pack(fill="x", pady=(0, 4))
                tk.Label(tip_row, text=f"  +  {tip}",
                         bg=self.colors["success_bg"], fg=self.colors["stat_green"],
                         font=("Segoe UI", 9), padx=8, pady=6,
                         wraplength=560, justify="left", anchor="w").pack(fill="x")

        # ── Action buttons ────────────────────────────────────────
        tk.Frame(win, bg=self.colors["border_2"], height=1).pack(fill="x", pady=(8, 0))
        btn_row = tk.Frame(win, bg=self.colors["bg"])
        btn_row.pack(fill="x", padx=20, pady=12)

        def _add_all_reminders():
            try:
                from app.calendar_integration import CalendarManager
                cal = CalendarManager(
                    provider=self.config.get("calendar_provider", "windows")
                )
                results = cal.add_reminders_from_analysis(analysis, auto_open=True)
                added = sum(1 for ok, _ in results if ok)
                self.toast_manager.show_toast(
                    f"Added {added} reminder(s) to calendar!", "success"
                )
            except Exception as e:
                self.toast_manager.show_toast(f"Calendar error: {e}", "error")

        if analysis.key_dates:
            tk.Button(btn_row, text="+ Add All to Calendar",
                      bg=self.colors["accent"], fg="white",
                      activebackground=self.colors["accent_2"],
                      relief="flat", bd=0, padx=14, pady=7,
                      font=("Segoe UI", 9, "bold"), cursor="hand2",
                      command=_add_all_reminders).pack(side="left", padx=(0, 8))

        tk.Button(btn_row, text="Close",
                  bg=self.colors["panel_2"], fg=self.colors["muted"],
                  relief="flat", bd=0, padx=14, pady=7,
                  font=("Segoe UI", 9), cursor="hand2",
                  command=win.destroy).pack(side="right")

    def _add_calendar_reminder(self, date_info, filename: str) -> None:
        """Add a single date as calendar reminder."""
        try:
            from app.calendar_integration import CalendarManager
            cal = CalendarManager(
                provider=self.config.get("calendar_provider", "windows")
            )
            ok, msg = cal.add_reminder(
                title=date_info.label,
                date_str=date_info.date,
                description=date_info.description,
                remind_days_before=date_info.remind_days_before,
                filename=filename,
                auto_open=True,
            )
            if ok:
                self.toast_manager.show_toast(msg, "success")
            else:
                self.toast_manager.show_toast(msg, "error")
        except Exception as e:
            self.toast_manager.show_toast(f"Error: {e}", "error")