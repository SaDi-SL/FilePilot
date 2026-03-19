# FilePilot 🚀

> Smart desktop file automation and organization system for Windows

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Windows-blue)](https://github.com/SaDi-SL/FilePilot/releases)
[![Release](https://img.shields.io/badge/Release-v1.0.0-green)](https://github.com/SaDi-SL/FilePilot/releases)

FilePilot watches your folders and automatically organizes files using smart rules, plugins, and AI-powered classification — all from a clean dark-mode desktop app.

---

## ✨ Features

- **Smart Classification** — Organizes files by extension, filename keywords, and content analysis
- **Watch Multiple Folders** — Monitor Downloads, Desktop, and any other folder simultaneously
- **Plugin System** — Extend with custom Python plugins + built-in Plugin Marketplace
- **Live Dashboard** — Real-time activity feed, charts, and statistics
- **Rule Testing** — Test any filename against your rules before applying
- **Auto Backup** — Weekly automatic backup of config and history
- **4 Languages** — English, العربية, Français, Türkçe
- **Headless Mode** — Run silently in the background with tray icon only
- **Dark / Light Theme** — Fully themed interface
- **Portable** — Single `.exe`, no installation required

---

## 📥 Download

| Version | Type | Download |
|---------|------|----------|
| v1.0.0 | Portable EXE | [FilePilot.exe](https://github.com/SaDi-SL/FilePilot/releases/latest) |
| v1.0.0 | Installer | [FilePilot_Setup_v1.0.0.exe](https://github.com/SaDi-SL/FilePilot/releases/latest) |

---

## 🚀 Quick Start

### Option 1 — Portable (no install)
1. Download `FilePilot.exe`
2. Place it in any folder
3. Run it — done!

### Option 2 — Installer
1. Download `FilePilot_Setup_v1.0.0.exe`
2. Run and follow the wizard
3. Launch from Desktop shortcut

### Option 3 — From source
```bash
git clone https://github.com/SaDi-SL/FilePilot.git
cd FilePilot
pip install -r requirements.txt
python run.py
```

---

## 🖥️ Usage

```bash
python run.py              # Normal GUI mode
python run.py --headless   # Tray-only background mode
python run.py --help       # Show options
```

---

## 🏗️ Project Structure

```
FilePilot/
├── run.py                  # Entry point
├── build.py                # Build script (EXE + Installer)
├── FilePilot.spec          # PyInstaller spec
├── installer.iss           # Inno Setup script
├── config/
│   ├── config.json         # User settings
│   └── smart_rules.json    # Smart classification rules
├── plugins/                # Drop .py plugins here
└── app/
    ├── gui.py              # Main GUI entry
    ├── gui_dashboard.py    # Dashboard page
    ├── gui_builder.py      # UI layout
    ├── gui_monitoring.py   # Monitoring + tray
    ├── gui_actions.py      # Business logic
    ├── gui_theme.py        # Colors + styles
    ├── gui_toast.py        # Toast notifications
    ├── gui_tools.py        # Tools page
    ├── gui_wizard.py       # Setup wizard
    ├── gui_notifications.py
    ├── watcher.py          # File system watcher
    ├── multi_watcher.py    # Multi-folder monitor
    ├── mover.py            # File mover
    ├── classifier.py       # Extension classifier
    ├── smart_classifier.py # AI classifier
    ├── plugin_manager.py   # Plugin loader
    ├── plugin_marketplace.py # Marketplace engine
    ├── rule_tester.py      # Rule testing engine
    ├── auto_backup.py      # Auto backup manager
    ├── headless.py         # Headless mode
    ├── i18n.py             # Internationalization
    ├── config_loader.py
    ├── hash_manager.py
    ├── stats.py
    └── main.py
```

---

## 🔌 Plugin Development

Create a file in the `plugins/` folder:

```python
# plugins/my_plugin.py
NAME = "My Plugin"
VERSION = "1.0.0"
DESCRIPTION = "Describe what this plugin does."

def process(file_path, context):
    """
    Classify a file. Return category name or None.
    file_path: pathlib.Path object
    context: dict with 'rules' key
    """
    if "invoice" in file_path.name.lower():
        return "invoices"
    return None
```

Browse community plugins at the **Plugin Marketplace** inside the app or at:
[github.com/SaDi-SL/SaDi-SL-plugins](https://github.com/SaDi-SL/SaDi-SL-plugins)

---

## 🛠️ Build from Source

```bash
# Install dependencies
pip install pyinstaller pillow watchdog pystray

# Build portable EXE
python build.py --exe

# Build installer (requires Inno Setup 6)
python build.py

# Clean build artifacts
python build.py --clean
```

---

## 📋 Requirements

- Windows 10 / 11
- Python 3.10+ (for source)
- No Python needed for `.exe`

---

## 🤝 Contributing

1. Fork the repo
2. Create a branch: `git checkout -b feature/my-feature`
3. Commit: `git commit -m "Add my feature"`
4. Push: `git push origin feature/my-feature`
5. Open a Pull Request

To contribute a plugin, visit the [plugins repo](https://github.com/SaDi-SL/SaDi-SL-plugins).

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 👨‍💻 Author

**Sadi Al-lulu**
- GitHub: [@SaDi-SL](https://github.com/SaDi-SL)
- Email: sadii.allulu@gmail.com

---

<p align="center">Made with ❤️ — FilePilot v1.0.0</p>
