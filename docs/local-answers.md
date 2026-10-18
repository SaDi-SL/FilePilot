# Ask your files

Open **My Files → Search files** and update the local index, then switch to **Ask your files** to enter a question. **Organize a file** contains the organization preview and move controls. FilePilot retrieves indexed excerpts and uses the Ollama model in Settings to answer. The general AI provider selection does not enable cloud fallback for this feature.

The answer panel displays plain text and the exact excerpts sent to the model for each cited source. Select a source to read its full excerpt and indexed file path. **Copy excerpt** copies that text; **Open source file** (or Enter on the source list) opens the original file in your default application. This opens the file, not a specific PDF page. If the file has moved or been deleted, update the index. If retrieval finds no evidence, the panel explains that instead of invoking the answer model. Missing Ollama, generation failures, and invalid citations produce an explicit error and permit retry.

Source IDs are validated against the retrieved excerpts. This catches invented source IDs; it does not independently prove that every generated claim is entailed by its cited excerpt. Review the displayed excerpts for important decisions.

Run focused checks:

```powershell
python -m unittest tests.test_local_rag tests.test_application_search tests.test_search_index tests.test_product_search tests.test_qt_my_files tests.test_qt_service_bridge
```

Start the desktop app:

```powershell
python -m app.ui.qt
```

A live Ollama answer requires an installed answer model and a current semantic index. Automated tests use controlled providers and do not require model downloads.

## Reach answers from your prepared library

Document library offers **Search documents** and **Ask your files** once it has
indexed text. Ask your files asks across relevant indexed library excerpts, not
only the selected document and not every file at once. Use Summarize selected for
one file. Switching from the library brings the search/question controls into view.

The question workspace exposes Prepare documents, Check local AI and a route to
text search. Check local AI explicitly starts an installed Ollama if necessary,
checks the saved answer and embedding models, and reports missing models separately.
It never downloads models and does not claim that inventory checks prove inference.
Check failures release the controls for retry; an answer is generated only when Ask
is pressed. Questions show an indeterminate progress bar, clear prior evidence, and
reveal the completed answer only if the question workspace is still active. Arabic
answer/excerpt direction is applied independently of source-file paths.

Search files also has an explicit **Text search** action. It calls the stored local
text search directly, without requesting a semantic query or loading an AI model.
The question workspace's text-search route only opens that page; enter keywords and
press Text search to run it. Smart search and its existing exact-search fallback
remain available. Text search works independently of Ollama and answer readiness.
