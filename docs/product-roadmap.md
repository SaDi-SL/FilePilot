# FilePilot product priorities

The product goal is a simple Windows workflow: find a document, understand its
contents, verify the source, and organize files with a clear preview and recovery
path. Keep the established charcoal/purple/orange theme. Favor complete user
journeys over adding disconnected features.

| Priority | Implemented foundation | Remaining work |
| --- | --- | --- |
| Easy first use | Overview offers read-only document folders and optional organization setup. Its next-step button routes to preparation, continuation, issues or text-search guidance according to library state. No automatic setup modal or monitoring start. | Review the complete first-use journey on the user's Windows PC, including no models, an empty folder and unreadable documents. |
| Clear preparation | Reading/smart-search stages, pause/stop requests, continuation, visible issues and filter recovery. | Validate larger and mixed-language collections, inaccessible folders and partial preparation on realistic hardware. |
| Useful search | Visible indexed excerpts, literal highlighting, source preview, external open, explicit text search without AI. | Improve relevance against a real document/query set. Matched PDF-page navigation needs reliable page mapping; current previews explicitly start on page 1. |
| Verifiable answers | Selected-file summaries/comparisons, validated citation structure, exact excerpts, library-question entry, explicit AI checks, answer progress, and quick-summary/key-point/action-and-date formats. | Evaluate completeness and factual accuracy on real English, Arabic and Swedish documents. Citation-format validation alone is insufficient. |
| Safe organization | Preview explains file name, destination, rule and name conflicts; outcomes show the final path, warnings, local open actions and an exact Activity/Undo route. Existing revalidation, journal and recovery safeguards remain authoritative. | Review the entire proposal → explicit Organize action → outcome → recovery journey on Windows, including collisions, metadata warnings and refused Undo. |
| Windows readiness | Direct project launcher, shared profile, installed OCR reuse, Windows tests and installer validation infrastructure. | Stress/performance checks, target-PC display scaling and failure cases, convenient updates, signing and public-release validation. A development CI pass is not a stable-release declaration. |

## Development and review

Use `start-filepilot.cmd` to review normal code changes without repeated Setup
installation. Close the app, pull the development branch, and restart. Keep
installer testing for packaging/release validation. The source preview shares the
installed profile by default, so changes to settings and the index persist.

User-confirmed milestones include direct startup, preparation improvements and
search excerpt/source preview, explicit text search and a local library question
with cited excerpts, and selected-file summary formats. The latest first-use overview
and organization journey still need target-PC user review. Never interpret a successful model-inventory
check as a successful answer, or claim a full-document review of sampled excerpts.
