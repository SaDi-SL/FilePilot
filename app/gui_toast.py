"""
gui_toast.py — Toast notification manager for FilePilot.
"""
import tkinter as tk


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