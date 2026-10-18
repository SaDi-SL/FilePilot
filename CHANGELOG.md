# Changelog

## Unreleased — document workspace

- Persistent read-only document-library locations alongside the organized destination.
- Index status, extraction/OCR problems, freshness indicators and last-run metadata.
- Background indexing with progress, pause, cooperative stop and incremental resume.
- Optional visible-library periodic reconciliation; unchanged chunk embeddings avoid repeated Ollama requests.
- Selected-document summaries, two-document comparisons and validated organization proposals with exact cited excerpts.
- Direct summary action from file previews; hybrid search and result type filtering.
- Explicit release checks, local redacted diagnostics and optional Authenticode build signing.
- Windows CI and a module-isolated test runner that reports crashes and timeouts as failures.
- Updated multi-resolution icon and current feature documentation.

This remains a development build. Validation blockers and external signing requirements are documented separately.

### Local summary usability follow-up (18 October 2026)

- Regenerate once from the original excerpts when the local model omits valid source IDs; retain rejection of invented IDs and a shared generation time budget. Accept lowercase IDs and Arabic digit variants.
- Honor the requested answer language over the internal English task prompt. Remember the selected library answer language.
- Start installed Windows Ollama on demand, check the configured model, and provide a background Prepare local AI action with clear recovery guidance. No model downloads or cloud fallback.
- Add `build-windows.cmd` to prepare an isolated Python 3.12 build environment and run the existing build with one command. Inno Setup remains required for installer output.
- Local validation: 333 tests reported across 19 affected modules (18 skipped), plus 7 local-AI preparation tests passed. Static packaging review passed. Real Windows installation and live model inference require verification of this follow-up.

### Structured local answers (18 October 2026)

- Replace free-text citation formatting for actual Ollama summaries, comparisons and questions with schema-constrained points and source arrays. Validate each point before rendering, reject missing/unknown references, and support truthful no-evidence responses. Keep ordinary classification and organization proposal transport compatible.
- Add regression coverage for real adapter request schema, malformed response retry, per-point reference validation, unknown references, invalid shapes, no-evidence results and shared timeout. These tests use controlled responses; live Gemma inference on the user's Windows PC remains to be verified.

### Completed review tools (18 October 2026)

- The user confirmed that structured summarization works with their local model.
- Add Copy result, Save result, Open source and Copy excerpt to completed library reviews. Saved UTF-8 text includes captured source excerpts and paths; source documents are protected against overwrite. Clear stale review actions when a new job starts.
- Improve Arabic reading direction and preserve left-to-right source paths. Render validated reference labels once instead of duplicating model inline labels.
- Add regression checks for Arabic clipboard/export, source overwrite protection, cancellation, review-bound source opening and citation normalization.
