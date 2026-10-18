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

Earlier build, installation, application launch and uninstallation succeeded on the user's Windows PC. That historical result remains valid for the tested artifact. The new library/AI functionality has not yet been tried on that PC or in a newly built Windows package. Automated document tests use controlled providers; no live Ollama model inference was claimed in this environment.

The first Windows workflow actually ran 791 tests with no skips and exposed six failing modules. The logs showed Windows short-path aliases (`RUNNER~1`) breaking path-equality fixtures and fault injectors, and Qt URL slash formatting breaking string comparisons. The runner now uses a canonical temporary directory, and URL assertions compare resolved Path objects. A new Windows run must verify those fixes; failures are not skipped. The test runner fails on imports, native crashes and timeouts as well as assertion failures.

## Public-release gate

- Resolve the five ownership/replacement failures and run the complete suite on the intended platforms.
- Inspect the actual Windows CI result and test the new document journey in the intended Windows package.
- Keep installed OCR dependencies and their notices verified through the existing packaging checks.
- Obtain/configure the publisher signing identity and verify a signed build if distributing a signed release.
- Publish versioned binaries/checksums only after those checks. This branch remains a development build; it was not merged into `main` or published as a stable release by this update.
