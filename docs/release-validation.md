# Validation status — document workspace update

## Passing checks

The final focused runner completed 19 impacted/regression modules: **328 tests reported, 18 skipped for environment/platform conditions, no failed modules**. Coverage includes library persistence, legacy database compatibility, scan cancellation, unavailable-root preservation, cache reuse without repeated embedding requests, selected-file freshness, grounded summaries/comparison, safe organization proposals, diagnostics, release-link validation, signing-command validation, Qt responsiveness, search/preview/bridge behavior, setup and packaging/configuration regressions.

A separate legacy-runtime import check completed 31 tests. Windows-only startup integration imports were moved into the operations that need them, preserving Windows behavior while making backend tests importable on Linux. Deferred Qt widget cleanup was fixed in the safety-dialog test teardown. Library layout was visually reviewed at 1100 px and 620 px widths.

A one-off synthetic lexical-index smoke check used 1,000 text files in this Linux environment: first indexing took 5.32 seconds, the unchanged second scan 0.41 seconds, and lexical search about 0.002 seconds; the second scan extracted zero files. This is not a Windows or live-Ollama performance benchmark. A follow-up status-reading change uses one SQL snapshot rather than 500 per-row database reads and removes obsolete diagnostic entries after a location is removed. Its impacted library/search tests passed (46 tests).

## Whole-project findings

An isolated whole-project run during this review reported **790 tests across 50 modules, 19 skipped**. Three modules failed because of five file-replacement/ownership cases:

- `test_cross_volume_moves.CrossVolumeMoveTests.test_cleanup_does_not_remove_replacement_at_temporary_path`
- `test_recovery.RecoveryAssessmentTests.test_replaced_temporary_object_requires_review`
- `test_recovery.RecoveryReconciliationTests.test_staging_restores_replacement_that_arrives_at_rename_boundary`
- `test_undo.UndoTests.test_same_content_replacement_during_same_volume_restore_is_rejected`
- `test_undo.UndoTests.test_same_content_temporary_replacement_is_not_adopted`

All five were separately reproduced against an untouched checkout of the previous GitHub head, **764df38**, in the same Linux environment. They are pre-existing failures, not introduced by the document workspace. This observation does not make them acceptable for a stable product: file ownership/replacement safety requires investigation and resolution before public release. The moving, undo and recovery implementations were not weakened or their tests skipped to obtain a green result.

One additional read-only-before-monitoring-setup regression test was added after that whole-project run and is included in the 328-test focused result. Do not confuse the whole-project result with an entirely passing final suite.

## Windows and live-model evidence

Earlier build, installation, application launch and uninstallation succeeded on the user's Windows PC. That historical result remains valid for the tested artifact. The user subsequently confirmed that local structured summarization and the completed-review tools work on their PC. The later simplified add-folder journey has automated Qt/service coverage; interactive validation of that journey and live AI in the new installer still remains. Automated document tests use controlled providers; no live Ollama model inference was claimed in this environment.

The first Windows workflow actually ran 791 tests with no skips and exposed six failing modules. The logs showed Windows short-path aliases (`RUNNER~1`) breaking path-equality fixtures and fault injectors, and Qt URL slash formatting breaking string comparisons. The runner now uses a canonical temporary directory, and URL assertions compare resolved Path objects. The second Windows run passed every module except a new legacy-database compatibility test whose fixture left its SQLite connection open during temporary-directory cleanup. The fixture now explicitly closes the connection. The third Windows run verified the final fix: **791 tests across 50 modules, zero skipped, no failed modules** on runtime commit `30ecaa44255a9c4f4b6725fb18fe3600892e7684`. Evidence: https://github.com/SaDi-SL/FilePilot/actions/runs/39118578778. No runtime tests were skipped to obtain that result. The test runner fails on imports, native crashes and timeouts as well as assertion failures.

An additional integrated smoke check exercised the real Qt bridge, adding a read-only folder, indexing, selected-file summary, exact source display and shutdown while preserving the original file. It used controlled local providers, not live Ollama.

The later validation-record commit changes documentation only; the linked Windows run identifies the exact tested application code.

## Public-release gate

- Resolve the five ownership/replacement failures and run the complete suite on the intended platforms.
- Inspect the actual Windows CI result and test the new document journey in the intended Windows package.
- Keep installed OCR dependencies and their notices verified through the existing packaging checks.
- Obtain/configure the publisher signing identity and verify a signed build if distributing a signed release.
- Publish versioned binaries/checksums only after those checks. This branch remains a development build; it was not merged into `main` or published as a stable release by this update.

## Latest Windows candidate — 18 October 2026

Candidate commit: `24d769293d3f9f1cbd43d37c91895de393f0d94f`. All **820 tests across 52 modules passed, with zero skips**, in the candidate workflow.

The executable and Inno installer were actually built on Windows 2025. Automated validation then installed the candidate in a disposable profile, checked the installed executable hash, observed its visible Qt main window using the Windows platform plugin, closed it normally, uninstalled it, and verified the external configuration plus a preservation marker remained. This is an installed-package startup/uninstall smoke check, not a live-model or full interactive product test.

- Successful candidate run: https://github.com/SaDi-SL/FilePilot/actions/runs/39135436281
- Downloadable candidate artifact: https://github.com/SaDi-SL/FilePilot/actions/runs/39135436281/artifacts/12139682374
- Validation evidence: https://github.com/SaDi-SL/FilePilot/actions/runs/39135436281/artifacts/12139885886
- Candidate ZIP: 135,407,431 bytes; artifacts expire 1 November 2026.

The first packaging attempt exposed the command-line OCR flag being treated as a truthy string. The installer now uses an explicit integer comparison, and the final actual compilation passed with OCR disabled. The candidate is **unsigned**, includes **no OCR runtime**, and requires separately installed Ollama/models for local AI. No stable release was published and main was not merged. The known Linux ownership/replacement failures remain unresolved.
