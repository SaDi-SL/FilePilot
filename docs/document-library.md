# Document library

Open **Overview → Document library** or **My Files → Document library**.

## Add documents without moving them

The configured organized destination remains included in indexing. Read-only library folders can be indexed even before automatic-organization setup is complete; this never starts monitoring. **Add folder** adds another persistent, read-only search location and starts preparation after that addition succeeds. It does not add a watch folder, enable automatic organization, move files or alter rules. **Remove from library** removes that location only; the next index update removes records no longer covered by any configured library root. Source files are retained. Overlapping locations are deduplicated during indexing.

## Index health and controls

**Prepare documents** scans configured locations, skips unchanged successful extraction, retries extraction problems, and reuses matching chunk embeddings before making model requests. Each completed file is saved; a stopped job can be resumed with another update. **Pause** and **Stop** take effect at the next checkpoint after the current file or model request. They do not interrupt a SQLite transaction or kill a running OCR/model subprocess.

The optional two-minute refresh runs only while this library widget is visible and no library document/index job is active. It is off by default. It is a periodic reconciliation, not a filesystem watcher. Index jobs have their own worker lane so the lifecycle thread can continue handling monitoring controls.

The default table shows document names and reading status. **More options** reveals stored vector states, indexing times, stale/inaccessible-file notices and actionable extraction/embedding errors. An unavailable or incompletely scanned root keeps its previous index entries; it is never interpreted as an empty folder. A cancelled scan does not prune unvisited entries. Catalog totals describe stored entries; freshness checks cover the displayed rows. The table displays up to 500 cataloged files plus up to 100 failures without records. Full-index Search files remains available for larger libraries.

Filename/path, type and status filters apply to the displayed catalog. Search files combines lexical and semantic evidence, falls back to exact local search when semantic search is unavailable, and has a result type filter. Activate a search result to open its original file.

## Summaries, comparisons and suggestions

Select one file for **Summarize selected** or **Suggest organization**. Select exactly two with Ctrl-click for **Compare two files**. Choose English, Arabic or Swedish. **Browse files → Summarize file** opens the same selected-file workflow.

These tasks use indexed text from the selected paths only and local Ollama; they do not require embedding generation. Missing, changed or unreadable selected files are rejected with a request to update or inspect their index status. Source IDs are validated, and the exact cited excerpts remain visible below the answer. Citation ID validation does not prove every generated factual claim; users should inspect the supporting text.

Summaries and comparisons are **excerpt-based**. Extraction is bounded to 100,000 characters and, for PDFs, the first 20 pages. Up to 16,000 context characters are evenly sampled across the indexed portion of the selected documents. The view does not claim a complete review of an arbitrarily long original. Excerpt/chunk numbers are not PDF page numbers.

Organization suggestions must use an existing configured category (or `others`) and a validated Windows-safe filename preserving the extension. They are proposals only: the feature never renames, moves files or edits classification rules. Actual organization continues through the existing preview/revalidation flow.

## Privacy and support

Settings → About & privacy contains **Check for updates** and **Prepare diagnostics**. Release checks contact the repository's GitHub releases endpoint only on request. They open a validated release page and never download or execute an installer automatically. Diagnostic reports are previewed and saved locally; they exclude document text, file names, folder paths, credentials and raw logs.

## Local AI without a terminal

Summaries and answers automatically try to start an installed Ollama on Windows when needed. The library's **More options → Prepare local AI** button also starts it and checks the saved answer model, reporting missing models separately from a connection failure. No model downloads or cloud fallback occur. This preparation runs in the background.

If a generated answer omits valid excerpt references, FilePilot makes one fresh generation attempt with the same evidence and an explicit citation reminder. Both generations share the original time budget; a second invalid response remains an error. Citation-format validation does not prove factual accuracy: inspect the displayed excerpts. Requested Arabic/Swedish/English output takes precedence over the internal English task prompt.

### Structured answer validation

Local Ollama summaries, comparisons and questions now request a JSON schema with separate factual points and source-ID arrays. Every point must reference provided excerpts; missing or invented IDs reject the entire response. FilePilot renders the references from those validated fields. A response with no relevant evidence is displayed as such, without a fabricated summary. One regeneration is allowed within the shared request budget. This validates the reference structure, not the truth of every generated claim. The existing category/name organization proposal keeps its separate text protocol. No document contents or raw model responses are written to diagnostics.

### Use a completed review

After a summary, comparison or organization proposal finishes, **Copy result** copies the text and its source references. **Save result…** writes a UTF-8 text review containing the answer, reference list, source paths and the exact cited excerpts. Choose a `.txt` filename; the app refuses to overwrite the current source documents or any document in the displayed library snapshot. Saving is atomic and reports write failures. It does not rename or move library files.

Select a source and use **Open source** to open that original document, or **Copy excerpt** to copy its exact text. These actions use the completed review's sources even if you select another document in the library table. Starting a new review or indexing clears the previous review actions. Arabic result text uses right-to-left reading direction; file paths remain isolated left-to-right. Inline reference labels returned inside structured point text are validated and rendered once beside each point.

### A simpler library journey

The main view presents Add folder, Prepare documents and Search documents. Pause and Stop appear only while preparation runs. More options keeps folder removal, refresh status, the optional periodic update, local AI preparation, stored-vector details and job issues available. Search documents opens the full-index search tab.

The library distinguishes an empty catalog from filters with no matches. Select one document with current indexed text to summarize or suggest organization, or two readable documents to compare. Files needing OCR, an update or another reading fix remain available to open, with an explanation; their summary actions stay disabled. Adding a folder and indexing run sequentially in the worker lane, and other mutating tasks are blocked until the current task completes. Monitoring is not started.

## Scanned documents and images

OCR-enabled Windows installers include local English, Arabic and Swedish OCR.
Add a folder containing scanned PDFs or supported images, let **Prepare documents**
finish, then search the extracted text or select a readable file for an insight.
Existing locations can use **Prepare documents** again after upgrading; the
extraction capability fingerprint invalidates old OCR-unavailable entries.
Summaries still require the configured local Ollama model; OCR itself does not.

PNG, JPEG, BMP, TIFF and WebP inputs are supported. Non-PNG images are converted
locally to temporary PNGs with EXIF orientation applied. Temporary files are
removed on success or failure; originals are never rewritten. For multi-frame
images only the first frame is read. PDF OCR runs when the normal PDF reader finds
no embedded text, and indexes at most the first 20 pages, within the existing
60-second document/20-second page budgets. Mixed PDFs containing some embedded
text continue to use that text; this update does not OCR every image in a mixed
PDF. OCR quality depends on image clarity; extracted excerpts should be reviewed
when exact wording matters.

## Preview and selected-file actions

The library now pairs the document list with an inline preview. Selecting a PDF
shows its rendered pages with Previous/Next controls; supported images show their
contents, and text/Office documents show a bounded text preview. Rendering and
reading run in the background. Fast selection changes are debounced; stale
responses cannot replace the currently selected file. Previews do not move or
rewrite source files and do not require Ollama.

Open file and Summarize selected appear beside the preview. Compare appears when
two documents are selected, while organization proposals and technical controls
remain under More options. The layout stacks vertically at narrow widths and
uses the page's existing scroll area. Empty catalogs and filters with no matches
show the next-step message instead of an empty preview panel.

Reading failures remain visible and cannot be summarized. Check OCR inspects
local runtime availability and language files; a missing bundled runtime points
to installing the Setup build. It does not claim to diagnose every damaged PDF,
OCR recognition error or extraction timeout. Preview errors separately suggest
opening the original document. Stored vectors on unreadable records are labelled
Not ready rather than implying that the document can be summarized.

## Visible document review feedback

While a selected-file review runs, its button reads **Preparing result…** and a
local progress indicator explains that the first answer can take longer while
the model loads. Failures appear beside the document controls and allow another
attempt. Completed summaries, comparisons and organization suggestions show the
requested file names above the answer. The outer My Files page scrolls to that
heading after layout updates, including at narrow window sizes; completion does
not switch tabs or scroll an unrelated workspace. An unsupported/no-evidence
response is also revealed so users can read the outcome.

## Guided document preparation

An empty library shows the three steps and a **Choose document folder** action.
Once a folder is saved, guidance points to preparation; a completed scan with no
documents explains which file types to add. Filters stay hidden until there are
catalog entries. A stopped scan offers **Continue preparation** without discarding
completed work.

Health badges count distinct paths in the current catalog snapshot, before filename,
type and status filtering. Ready-to-read means the displayed record is indexed;
attention includes unreadable/stale records and recorded issues, so the badges can
overlap when readable text has a smart-search issue. The view is bounded; truncated
catalogs explain that these counts cover the displayed catalog while Search documents
uses the full index. **Review issues** clears other filters and shows the attention
rows. **Clear filters** restores the catalog after a filter has no matches.

Reading and smart-search preparation use separate progress descriptions. Status
refreshes preserve the active progress range/value and any pause or stop request.
Pause/Stop explain that the current file or model request may finish first; they
do not claim an immediate interruption. Completing a job resets its controls without
sending an accidental resume request. All preparation keeps originals in place.

## Search evidence and source previews

Search files now displays a bounded indexed excerpt directly in each result, with
literal query terms highlighted in English, Swedish and Arabic. Matching is visual;
it does not change search ranking or claim that semantic matches share the exact
query words. Document text, file names and paths are escaped before rich-text
rendering. Older stored vectors with no chunk text explicitly show that an excerpt
is unavailable.

Semantic matches carry the excerpt from their best scoring stored chunk (up to 800
characters); hybrid results prefer the exact-search excerpt when one is present.
Rows display a shorter version. Select a result to inspect the indexed excerpt and
the current source preview. The preview uses an independent background request key
so browsing search results cannot replace the Document library preview. Open original
file, double-click or Enter opens the source externally; missing files show a repair
message. No files are moved or rewritten.

At narrow widths, results and source stack vertically. View selected source scrolls
the outer workspace to the selected source. PDF previews start on page 1 with normal
page navigation; the UI explicitly distinguishes this from the matched excerpt and
does not invent a PDF page reference. Changing queries or filtering out the current
result clears its evidence and preview.
