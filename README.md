# FilePilot

A local Windows workspace for organizing files, finding documents and understanding their contents.

## What you can do

- Preview organization before moving a file; review activity, duplicates and recovery evidence.
- Browse organized folders and preview PDF pages, images and text.
- Search filenames and extracted text, with local semantic ranking when Ollama is available.
- Add **read-only document folders**, inspect indexing problems, pause/stop indexing and resume completed work later.
- Summarize a selected document, compare two documents or request an organization suggestion with exact source excerpts. These tasks stay local through Ollama.
- Check published releases explicitly and prepare a local diagnostic report without document contents, file names, paths or credentials.

The charcoal, purple and orange UI includes guided setup and a multi-resolution Windows icon.

## Start on Windows

```powershell
python -m pip install -r requirements-qt.txt
python -m app.ui.qt
```

Start with **Overview → Guided setup**, then **Document library**. Add a folder and choose **Update / retry index**. Adding a library folder never moves its files. Basic text search works without a local AI model; semantic search uses `bge-m3`, and summaries/answers use the Ollama model saved in Settings.

See [Document library](docs/document-library.md), [Guided setup](docs/guided-setup.md), [Browse files](docs/file-browser.md) and [Ask your files](docs/local-answers.md).

## Validate

```powershell
python scripts/check_tests.py --focus
python scripts/check_tests.py
```

Each module runs in a fresh process. Failures, import errors, crashes and timeouts are reported; logs and a machine-readable summary are written to `build/test-results`. The Windows quality workflow runs the complete test set. See [validation and release limitations](docs/release-validation.md).

## Build a Windows release candidate

```powershell
.\build-windows.cmd
```

Requires Python 3.12 (Windows launcher). The command creates an isolated build environment, installs build dependencies and runs the existing validated build. Install Inno Setup 6 to produce the installer; otherwise the executable is produced.

The build derives display and numeric versions from `app.product_identity.PRODUCT_IDENTITY`, builds the Qt application, and compiles an Inno Setup installer when Inno Setup 6 is available. A completed installer build also writes `dist/release-manifest.json` with SHA-256 checksums. [Optional digital signing](docs/windows-release.md) requires an existing publisher certificate.

Binaries install under `%LOCALAPPDATA%\Programs\FilePilot`; writable configuration, logs, reports, backups, plugins, reminders and the operation journal live under `%LOCALAPPDATA%\FilePilot`. Upgrades and uninstallation preserve that user-data directory.

The current code remains a **development build**, not a newly published stable release. Earlier Windows build/install/uninstall tests succeeded on the user's PC; the newly added features require validation in a newly built package before public release.
