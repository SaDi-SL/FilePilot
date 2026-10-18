# FilePilot product priorities

The product goal is a simple Windows workflow: find a document, understand its
contents, verify the source, and organize files with a clear preview and recovery
path. Keep the established charcoal/purple/orange theme. Favor complete user
journeys over adding disconnected features.

| Priority | Implemented foundation | Remaining work |
| --- | --- | --- |
| Easy first use | Overview offers read-only document folders and optional organization setup. Preparation guidance follows library state. No automatic setup modal or monitoring start. | Review the complete first-use journey on the user's Windows PC, including no models, an empty folder and unreadable documents. |
| Clear preparation | Reading/smart-search stages, pause/stop requests, continuation, visible issues and filter recovery. | Validate larger and mixed-language collections, inaccessible folders and partial preparation on realistic hardware. |
| Useful search | Visible indexed excerpts, literal highlighting, source preview, external open, explicit text search without AI. | Improve relevance against a real document/query set. Matched PDF-page navigation needs reliable page mapping; current previews explicitly start on page 1. |
| Verifiable answers | Selected-file summaries/comparisons, validated citation structure, exact excerpts, library-question entry, explicit AI checks and answer progress. | Add clear quick-summary, key-point and action/deadline task choices with evidence checks. Evaluate completeness and factual accuracy on real English, Arabic and Swedish documents. Citation-format validation alone is insufficient. |
| Safe organization | Existing preview/revalidation, operation journal, conflict handling and recovery UI. | Review the entire proposal → confirmation → outcome → recovery journey with a new user. Explain proposed changes and conflicts in user language. |
| Windows readiness | Direct project launcher, shared profile, installed OCR reuse, Windows tests and installer validation infrastructure. | Stress/performance checks, target-PC display scaling and failure cases, convenient updates, signing and public-release validation. A development CI pass is not a stable-release declaration. |

## Development and review

Use `start-filepilot.cmd` to review normal code changes without repeated Setup
installation. Close the app, pull the development branch, and restart. Keep
installer testing for packaging/release validation. The source preview shares the
installed profile by default, so changes to settings and the index persist.

User-confirmed milestones include direct startup, preparation improvements and
search excerpt/source preview. The latest first-use and question-workspace changes
still need target-PC user review. Never interpret a successful model-inventory
check as a successful answer, or claim a full-document review of sampled excerpts.
