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
