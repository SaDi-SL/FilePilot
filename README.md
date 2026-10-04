# FilePilot
Smart desktop file automation and organization system

## Windows release candidate

Install the declared build dependencies, then run from any working directory:

```powershell
python C:\path\to\FilePilot\build.py
```

The build validates release inputs, derives both display and Windows numeric
versions from `app.product_identity.PRODUCT_IDENTITY`, builds the professional
Qt application into `dist\FilePilot.exe`, and compiles an Inno Setup installer
when Inno Setup 6 is already available. Use `python build.py --dry-run` for a
static packaging review without building.

Installed binaries live under `%LOCALAPPDATA%\Programs\FilePilot`. Writable
configuration, logs, reports, backups, plugins, reminders, and the operation
journal live separately under `%LOCALAPPDATA%\FilePilot`. Upgrades and
uninstallation do not overwrite or remove that user data.

`icon.ico` is the current development icon. It is wired consistently into the
EXE and installer, but its single 16x16 image must be replaced by a reviewed
multi-resolution production icon before a public release.
