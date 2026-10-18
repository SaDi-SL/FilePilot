# Guided setup

Overview starts with two explicit choices. **Add document folder** opens the read-only
Document library folder picker; adding a folder prepares its documents without
requiring watched folders or an organized destination. Cancelling the picker adds
nothing. The next-step button follows the current library status:

- **Prepare documents** reads a saved folder when explicitly clicked.
- **Continue preparation** starts a new preparation pass after a stopped run, keeping completed work.
- **View preparation** opens the running job without starting another one.
- **Review issues** opens the library with unrelated filters cleared and Needs attention selected.
- **Search documents** opens keyword search with guidance for using Text search without AI. It does not submit a query or start Ollama.

Before a folder is added, **Open document library** opens the workspace. If no
documents were found in the last preparation, Home explains that a folder containing
supported files is needed, rather than presenting it as an unread folder. Status
updates only change guidance and button labels; they do not start preparation.
Text search does not require AI; summaries require local Ollama and a saved model.

**Set up file organization** opens the optional organization guide below. It never
opens automatically when organization settings are incomplete. On a standard Qt
launch, a missing configuration is seeded through the existing exclusive-create
default-config helper; existing configuration is preserved. Monitoring starts only
through the explicit Start organizing action after configuration validation.
Empty organization counters/history are omitted on an unconfigured workspace;
actual history, journal errors and recovery notices remain available.

The guide fits its initial size to the available screen in Qt logical pixels, including Windows display scaling. Resize it using the bottom-corner grip or window edges. Step content scrolls while Continue, Back and Finish stay outside the scrolling area.

## 1. Choose folders

Use **Add folder** to select existing incoming folders, then **Browse** for the organized destination. Review monthly grouping and click **Save folders** after changing anything. The guide uses the same complete configuration validation and revision checks as Folders. Saving a valid first-run candidate marks folder setup complete and builds the runtime while leaving monitoring stopped.

## 2. Local AI (optional)

Click **Check local AI**. This performs one background, three-second GET to local Ollama's model inventory. It checks the saved answer model and the current default embedding model, bge-m3. It does not load models, run inference, contact a cloud provider, install anything or start indexing.

The guide distinguishes unavailable inventory from a reachable server with missing models. Model tags match exactly; an omitted tag aliases only `:latest`. Installed models do not guarantee successful inference: indexing and a first answer verify actual use.

Choose an installed chat model, click **Use selected model**, then **Save settings** and check again. The model picker also contains embedding models; these are for search, not answering questions. Automatic classification remains a separate optional setting. Local answers use Ollama even if a cloud classification provider is selected.

Continue without local AI if desired. Basic rule-based organization remains available. Unsaved changes must be saved or reverted before progressing.

## 3. Review

Review the saved incoming folders, destination, local-model result and current monitoring state. **Finish** closes the guide. **Go to search indexing** opens the document library (or Search files in runtimes without that workspace); select Prepare documents there to index an existing organized destination. Adding a read-only folder starts preparation automatically. Start monitoring yourself from Overview.

Closing setup asks before discarding unsaved edits and waits for an active save. Closing does not undo saved configuration. Read-only model checks are ignored after a saved-model change or dialog dismissal.

## Validation

Qt guide checks cover unsaved/invalid configuration gates, optional AI, a worker-thread inventory request, stale-result rejection, model selection, save-in-progress closing, compact dialog controls, explicit guide opening, first-use document-folder routing without monitoring and the indexing link. Existing configuration-service tests cover first-run save without monitoring. Layout was reviewed at 980 × 760 and 680 × 540. Short-screen regression checks also cover 1024 × 640 and 800 × 480 logical work areas with navigation buttons inside the dialog. Windows display scaling and live Ollama should be verified on the target computer.
