# Validation status — document workspace update

## Passing checks

The final focused runner completed 19 impacted/regression modules: **328 tests reported, 18 skipped for environment/platform conditions, no failed modules**. Coverage includes library persistence, legacy database compatibility, scan cancellation, unavailable-root preservation, cache reuse without repeated embedding requests, selected-file freshness, grounded summaries/comparison, safe organization proposals, diagnostics, release-link validation, signing-command validation, Qt responsiveness, search/preview/bridge behavior, setup and packaging/configuration regressions.

A separate legacy-runtime import check completed 31 tests. Windows-only startup integration imports were moved into the operations that need them, preserving Windows behavior while making backend tests importable on Linux. Deferred Qt widget cleanup was fixed in the safety-dialog test teardown. Library layout was visually reviewed at 1100 px and 620 px widths.

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

The Windows quality workflow is configured to run every test module in a fresh process on Windows with declared dependencies. Its actual run result must be inspected after upload; a configured workflow is not evidence of a successful Windows run. The test runner fails on imports, native crashes and timeouts as well as assertion failures.

## Public-release gate

- Resolve the five ownership/replacement failures and run the complete suite on the intended platforms.
- Inspect the actual Windows CI result and test the new document journey in the intended Windows package.
- Keep installed OCR dependencies and their notices verified through the existing packaging checks.
- Obtain/configure the publisher signing identity and verify a signed build if distributing a signed release.
- Publish versioned binaries/checksums only after those checks. This branch remains a development build; it was not merged into `main` or published as a stable release by this update.
