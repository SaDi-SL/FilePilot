# Document library

Open **Overview → Document library** or **My Files → Document library**.

## Add documents without moving them

The configured organized destination remains included in indexing. Read-only library folders can be indexed even before automatic-organization setup is complete; this never starts monitoring. **Add folder** adds another persistent, read-only search location. It does not add a watch folder, enable automatic organization, move files or alter rules. **Remove from library** removes that location only; the next index update removes records no longer covered by any configured library root. Source files are retained. Overlapping locations are deduplicated during indexing.

## Index health and controls

**Update / retry index** scans configured locations, skips unchanged successful extraction, retries extraction problems, and reuses matching chunk embeddings before making model requests. Each completed file is saved; a stopped job can be resumed with another update. **Pause** and **Stop** take effect at the next checkpoint after the current file or model request. They do not interrupt a SQLite transaction or kill a running OCR/model subprocess.

The optional two-minute refresh runs only while this library widget is visible and no library document/index job is active. It is off by default. It is a periodic reconciliation, not a filesystem watcher. Index jobs have their own worker lane so the lifecycle thread can continue handling monitoring controls.

The table shows stored extraction and vector states, indexing times, stale/inaccessible-file notices and actionable extraction/embedding errors. An unavailable or incompletely scanned root keeps its previous index entries; it is never interpreted as an empty folder. A cancelled scan does not prune unvisited entries. Catalog totals describe stored entries; freshness checks cover the displayed rows. The table displays up to 500 cataloged files plus up to 100 failures without records. Full-index Search files remains available for larger libraries.

Filename/path, type and status filters apply to the displayed catalog. Search files combines lexical and semantic evidence, falls back to exact local search when semantic search is unavailable, and has a result type filter. Activate a search result to open its original file.

## Summaries, comparisons and suggestions

Select one file for **Summarize selected** or **Suggest organization**. Select exactly two with Ctrl-click for **Compare two files**. Choose English, Arabic or Swedish. **Browse files → Summarize file** opens the same selected-file workflow.

These tasks use indexed text from the selected paths only and local Ollama; they do not require embedding generation. Missing, changed or unreadable selected files are rejected with a request to update or inspect their index status. Source IDs are validated, and the exact cited excerpts remain visible below the answer. Citation ID validation does not prove every generated factual claim; users should inspect the supporting text.

Summaries and comparisons are **excerpt-based**. Extraction is bounded to 100,000 characters and, for PDFs, the first 20 pages. Up to 16,000 context characters are evenly sampled across the indexed portion of the selected documents. The view does not claim a complete review of an arbitrarily long original. Excerpt/chunk numbers are not PDF page numbers.

Organization suggestions must use an existing configured category (or `others`) and a validated Windows-safe filename preserving the extension. They are proposals only: the feature never renames, moves files or edits classification rules. Actual organization continues through the existing preview/revalidation flow.

## Privacy and support

Settings → About & privacy contains **Check for updates** and **Prepare diagnostics**. Release checks contact the repository's GitHub releases endpoint only on request. They open a validated release page and never download or execute an installer automatically. Diagnostic reports are previewed and saved locally; they exclude document text, file names, folder paths, credentials and raw logs.

## Local AI without a terminal

Summaries and answers automatically try to start an installed Ollama on Windows when needed. The library's **Prepare local AI** button also starts it and checks the saved answer model, reporting missing models separately from a connection failure. No model downloads or cloud fallback occur. This preparation runs in the background.

If a generated answer omits valid excerpt references, FilePilot makes one fresh generation attempt with the same evidence and an explicit citation reminder. Both generations share the original time budget; a second invalid response remains an error. Citation-format validation does not prove factual accuracy: inspect the displayed excerpts. Requested Arabic/Swedish/English output takes precedence over the internal English task prompt.
