# Ask your files

Open **My Files**, update the local index, then enter a question under **Ask your files**. FilePilot retrieves indexed excerpts and uses the Ollama model in Settings to answer. The general AI provider selection does not enable cloud fallback for this feature.

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
