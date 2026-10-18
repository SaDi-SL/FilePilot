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
.\start-filepilot.cmd
```

For development, close the installed FilePilot and double-click
**start-filepilot.cmd** in the project folder. The first run requires Python 3.12
(recommended; 3.13 is also supported) and internet access to create a private
`.venv-dev` environment. Following runs check local imports and reuse it; libraries
are installed again only when the requirements change or `--repair` is requested.
No EXE build, Setup download or reinstallation is required for each code change.

The preview uses the current user's `%LOCALAPPDATA%\FilePilot` configuration and
search index, with code/resources from this checkout. It reuses a complete installed
English/Arabic/Swedish OCR bundle read-only, including custom per-user Inno install
locations. The launcher prints the profile and OCR location. Without that bundle,
normal source OCR discovery is used; OCR may be unavailable. Ollama and the saved
models remain separate. Close the preview before starting the installed version,
updating the project, or changing branches. Settings/index updates made in the
preview are real updates to this shared profile. Use an explicit absolute
`FILEPILOT_DEV_PROFILE` environment variable for a separate test profile if needed.
Ordinary `python -m app.ui.qt` keeps the existing repository profile behavior.

To update, close FilePilot, run `git pull --ff-only` on the development branch,
then open **start-filepilot.cmd** again. Changes appear after restart; there is no
live reload. `start-filepilot.cmd --check` prepares/checks without opening the UI;
`start-filepilot.cmd --repair` checks dependency installation again. The launcher
blocks a running installed EXE or another preview using the same profile; it never
terminates either process automatically. Avoid running another source entry point
against this shared profile at the same time.

Start with **Overview → Guided setup**, then **Document library**. Add a folder; preparation starts automatically. Use **Prepare documents** to refresh or resume later. **More options** contains local-AI checks and indexing details. Adding a library folder never moves its files. Basic text search works without a local AI model; semantic search uses `bge-m3`, and summaries/answers use the Ollama model saved in Settings.

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

The current code remains a **development build**, not a newly published stable release. Earlier Windows build/install/uninstall tests succeeded on the user's PC; the latest candidate now passes automated installation, visible-window launch, shutdown and uninstall-preservation checks. Live AI and full interactive use in that candidate still need verification before public release.

## Download a tested Windows candidate

The **Windows installer candidate** Actions workflow builds the executable and installer, then checks installation, a visible Qt window, normal shutdown and uninstallation with user-data preservation on a disposable Windows runner. Its successful run provides a `FilePilot-Windows-<commit>` artifact ZIP: extract it and run the Setup executable inside `installer`. This is an unsigned development candidate; Ollama/models remain separate and OCR-enabled Setup builds bundle local English, Arabic and Swedish OCR. No GitHub Release is published automatically. Artifacts are retained for 14 days. The workflow also supports manual runs once available on the default branch; on this development branch, application or packaging changes trigger the candidate build. Superseded builds are cancelled automatically.
