# Browse organized files

Open **My Files → Browse files**. The browser reads the organized destination configured in **Folders**, without requiring the search index or Ollama.

- Double-click a folder or select it and press **Open** to browse inside.
- **Up** goes to the parent folder; **Organized folder** returns to the destination.
- Filter names in the current folder and sort by name, modification time, or size. Folders stay before files.
- Select an item for its full path, size, and modification time. **Open** opens files in the default application; **Open containing folder** opens their parent folder.
- Plain text formats (TXT, MD, CSV, JSON, LOG, PY) show up to the first 16 KB, decoded as UTF-8. Other formats open externally.
- **Refresh** rereads the current directory after files change.

Folder counts include direct files and direct subfolders; folder sizes include only direct files. Nested files are excluded. Partial folder counts are labeled “At least.” Directory enumeration and text preview run in background tasks, and stale results are ignored.

The browser limits each listing to 1,000 entries and folder statistics to a shared 10,000-entry budget. Linked locations are excluded. Missing and inaccessible directories show a retryable error. It does not rename, move, or delete files.

Validation: `python -m unittest tests.test_file_browser tests.test_qt_file_browser tests.test_qt_my_files`.
