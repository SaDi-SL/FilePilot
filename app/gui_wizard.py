import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.config_loader import get_config_path
from app.main import build_monitor

def check_first_run_wizard(self):
    try:
        config_path = get_config_path()

        with open(config_path, "r", encoding="utf-8") as file:
            config_data = json.load(file)

        self.first_run_completed = config_data.get("first_run_completed", False)

    except Exception:
        self.first_run_completed = self.config.get("first_run_completed", False)

    if not self.first_run_completed:
        self.open_welcome_wizard()


def _wizard_browse_folder(self, variable, title):
    folder = filedialog.askdirectory(title=title)
    if folder:
        variable.set(folder)


def save_first_run_setup(self, source_folder: str, organized_folder: str, start_now: bool):
    try:
        config_path = get_config_path()

        with open(config_path, "r", encoding="utf-8") as file:
            config_data = json.load(file)

        config_data["source_folder"] = source_folder
        config_data["organized_base_folder"] = organized_folder
        config_data["first_run_completed"] = True

        with open(config_path, "w", encoding="utf-8") as file:
            json.dump(config_data, file, indent=2, ensure_ascii=False)

        self.first_run_completed = True
        self.config, self.monitor = build_monitor()

        self.source_folder_var.set(self.config.get("source_folder", "incoming"))
        self.organized_base_var.set(self.config.get("organized_base_folder", "organized"))

        self.refresh_stats()
        self.refresh_history()
        self.render_rule_entries()
        self.render_smart_rule_entries()
        self.refresh_plugins_view()
        self.refresh_notifications_view()

        if start_now:
            self.start_monitoring()

        self.toast_manager.show_toast("Welcome setup completed successfully.", "success")
        self.status_bar_var.set("Welcome setup completed.")
        self.add_notification("success", "Welcome Setup", "Initial setup completed successfully.")

    except Exception as error:
        messagebox.showerror("Error", f"Failed to save first run setup:\n{error}")
        self.add_notification("error", "Welcome Setup Error", str(error))


def open_welcome_wizard(self):
    wizard = tk.Toplevel(self.root)
    wizard.title("Welcome to FilePilot")
    wizard.geometry("860x720")
    wizard.minsize(760, 620)
    wizard.configure(bg=self.colors["bg"])
    wizard.transient(self.root)
    wizard.grab_set()

    icon_path = self.get_icon_path()
    if icon_path is not None:
        try:
            wizard.iconbitmap(str(icon_path))
        except Exception:
            pass

    source_var = tk.StringVar(value=self.config.get("source_folder", "incoming"))
    organized_var = tk.StringVar(value=self.config.get("organized_base_folder", "organized"))
    start_now_var = tk.BooleanVar(value=True)

    outer = tk.Frame(wizard, bg=self.colors["bg"])
    outer.pack(fill="both", expand=True)

    content_container = tk.Frame(outer, bg=self.colors["bg"])
    content_container.pack(fill="both", expand=True, padx=24, pady=(24, 12))

    canvas = tk.Canvas(
        content_container,
        bg=self.colors["bg"],
        highlightthickness=0
    )
    scrollbar = ttk.Scrollbar(
        content_container,
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

    self.attach_safe_mousewheel(canvas, owner=wizard)

    body = scrollable_frame

    header = tk.Frame(body, bg=self.colors["bg"])
    header.pack(fill="x", pady=(0, 16))

    tk.Label(
        header,
        text="Welcome to FilePilot",
        bg=self.colors["bg"],
        fg=self.colors["text"],
        font=("Segoe UI", 22, "bold"),
    ).pack(anchor="w")

    tk.Label(
        header,
        text="Let's configure your automation system in a few quick steps.",
        bg=self.colors["bg"],
        fg=self.colors["muted"],
        font=("Segoe UI", 11),
    ).pack(anchor="w", pady=(6, 0))

    intro_panel = self.create_info_panel(body, "What FilePilot Does")
    intro_panel.pack(fill="x", pady=10)

    intro_inner = tk.Frame(intro_panel, bg=self.colors["card"])
    intro_inner.pack(fill="x", padx=14, pady=14)

    intro_text = (
        "FilePilot automatically watches a folder and organizes files into categories.\n\n"
        "It supports:\n"
        "• Extension-based sorting\n"
        "• Smart classification\n"
        "• Plugins\n"
        "• Duplicate detection\n"
        "• History and analytics\n"
    )

    tk.Label(
        intro_inner,
        text=intro_text,
        bg=self.colors["card"],
        fg=self.colors["text"],
        justify="left",
        anchor="w",
        font=("Segoe UI", 10),
    ).pack(anchor="w")

    setup_panel = self.create_info_panel(body, "Initial Setup")
    setup_panel.pack(fill="x", pady=10)

    setup_inner = tk.Frame(setup_panel, bg=self.colors["card"])
    setup_inner.pack(fill="x", padx=14, pady=14)

    tk.Label(
        setup_inner,
        text="Incoming Folder:",
        bg=self.colors["card"],
        fg=self.colors["text"],
        font=("Segoe UI", 10, "bold"),
    ).grid(row=0, column=0, sticky="w", padx=8, pady=8)

    ttk.Entry(setup_inner, textvariable=source_var, width=64).grid(
        row=0, column=1, sticky="w", padx=8, pady=8
    )

    ttk.Button(
        setup_inner,
        text="Browse",
        style="Secondary.TButton",
        command=lambda: self._wizard_browse_folder(source_var, "Select Incoming Folder")
    ).grid(row=0, column=2, padx=8, pady=8)

    tk.Label(
        setup_inner,
        text="Organized Folder:",
        bg=self.colors["card"],
        fg=self.colors["text"],
        font=("Segoe UI", 10, "bold"),
    ).grid(row=1, column=0, sticky="w", padx=8, pady=8)

    ttk.Entry(setup_inner, textvariable=organized_var, width=64).grid(
        row=1, column=1, sticky="w", padx=8, pady=8
    )

    ttk.Button(
        setup_inner,
        text="Browse",
        style="Secondary.TButton",
        command=lambda: self._wizard_browse_folder(organized_var, "Select Organized Folder")
    ).grid(row=1, column=2, padx=8, pady=8)

    ttk.Checkbutton(
        setup_inner,
        text="Start monitoring immediately after setup",
        variable=start_now_var,
    ).grid(row=2, column=0, columnspan=3, sticky="w", padx=8, pady=(12, 4))

    tips_panel = self.create_info_panel(body, "Quick Tips")
    tips_panel.pack(fill="x", pady=10)

    tips_inner = tk.Frame(tips_panel, bg=self.colors["card"])
    tips_inner.pack(fill="x", padx=14, pady=14)

    tips_text = (
        "• Put new files into the Incoming folder\n"
        "• FilePilot will move them automatically\n"
        "• You can edit categories and Smart Rules later\n"
        "• You can add Plugins anytime from the plugins folder"
    )

    tk.Label(
        tips_inner,
        text=tips_text,
        bg=self.colors["card"],
        fg=self.colors["muted"],
        justify="left",
        anchor="w",
        font=("Segoe UI", 10),
    ).pack(anchor="w")

    footer = tk.Frame(outer, bg=self.colors["bg"])
    footer.pack(fill="x", padx=24, pady=(0, 18))

    def complete_setup():
        source_folder = source_var.get().strip()
        organized_folder = organized_var.get().strip()

        if not source_folder:
            messagebox.showerror("Error", "Incoming folder cannot be empty.", parent=wizard)
            return

        if not organized_folder:
            messagebox.showerror("Error", "Organized folder cannot be empty.", parent=wizard)
            return

        wizard.destroy()
        self.save_first_run_setup(
            source_folder=source_folder,
            organized_folder=organized_folder,
            start_now=bool(start_now_var.get())
        )

    ttk.Button(
        footer,
        text="Skip for Now",
        style="Secondary.TButton",
        command=wizard.destroy
    ).pack(side="right", padx=6)

    ttk.Button(
        footer,
        text="Complete Setup",
        style="Primary.TButton",
        command=complete_setup
    ).pack(side="right", padx=6)