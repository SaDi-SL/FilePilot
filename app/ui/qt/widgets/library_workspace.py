from pathlib import Path

from PySide6.QtCore import QSettings, QTimer, Qt, QUrl, QSize, QSaveFile, QIODevice, Signal
from PySide6.QtGui import QDesktopServices, QColor
from PySide6.QtWidgets import (QApplication, QAbstractItemView, QCheckBox, QComboBox, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QGridLayout, QLayout, QSplitter, QStyle)
from app.ui.qt.widgets.section_card import SectionCard
from app.ui.qt.widgets.library_preview import LibraryPreview


STATUS = {
    "indexed": "Ready", "metadata_only": "Filename only", "unsupported_format": "Filename only",
    "ocr_required": "Needs OCR", "ocr_unavailable": "OCR unavailable", "extraction_failed": "Read failed",
    "stale": "Update needed", "unavailable": "Unavailable", "failed": "Failed",
}


class _ActionGrid(QGridLayout):
    def __init__(self):
        super().__init__()
        self.widgets = []
        self.columns = 2

    def addWidget(self, widget, stretch=0):
        self.widgets.append(widget)
        self.reflow(self.columns)

    def reflow(self, columns):
        self.columns = columns
        while self.count():
            self.takeAt(0)
        for i, widget in enumerate(self.widgets):
            super().addWidget(widget, i // columns, i % columns)
        for i in range(5):
            self.setColumnStretch(i, 1 if i < columns else 0)


class LibraryWorkspace(QWidget):
    """A read-only document library with explicit indexing and grounded insights."""
    search_requested = Signal()

    def __init__(self, controller, bridge, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.bridge = bridge
        self.snapshot = None
        self._busy = False
        self._document_busy = False
        self._root_busy = False
        self._ai_busy = False
        self._source_records = ()
        self._review = None
        self._preferences = QSettings()
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(0, 12, 0, 0)
        card = SectionCard()
        card.setProperty("cardAccent", "purple")
        heading = QLabel("Your document library")
        heading.setProperty("role", "headline")
        hint = QLabel("Find information inside your documents, preview their pages and get summaries with sources. Your original files stay in place.")
        hint.setWordWrap(True)
        hint.setProperty("role", "secondary")
        card.content_layout.addWidget(heading)
        card.content_layout.addWidget(hint)
        self.journey = QLabel("1  Add a folder   →   2  Prepare documents   →   3  Search or summarize")
        self.journey.setWordWrap(True)
        self.journey.setProperty("role", "sectionTitle")
        card.content_layout.addWidget(self.journey)
        self.next_step = QLabel("Loading your document library…")
        self.next_step.setWordWrap(True)
        card.content_layout.addWidget(self.next_step)
        self.advanced = QWidget()
        advanced_layout = _ActionGrid()
        self.advanced.setLayout(advanced_layout)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.advanced.hide()
        roots = _ActionGrid()
        self._rows = [roots]
        self.roots = QComboBox()
        self.roots.setAccessibleName("Additional read-only library folders")
        self.roots.setMinimumWidth(100)
        self.roots.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        roots.addWidget(self.roots, 1)
        self.add_button = QPushButton("Add folder")
        self.add_button.clicked.connect(self._add)
        roots.addWidget(self.add_button)
        self.remove_button = QPushButton("Remove from library")
        self.remove_button.setToolTip("Removes only this library location; original files are kept. Select Prepare documents afterward.")
        self.remove_button.clicked.connect(self._remove)
        advanced_layout.addWidget(self.remove_button)
        card.content_layout.addLayout(roots)
        actions = _ActionGrid()
        self._rows.append(actions)
        self.update_button = QPushButton("Prepare documents")
        self.update_button.setProperty("variant", "primary")
        self.update_button.clicked.connect(self.start_index)
        self.pause_button = QPushButton("Pause")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._pause)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self._stop)
        self.refresh_button = QPushButton("Refresh status")
        self.refresh_button.clicked.connect(lambda: controller.request("status"))
        self.search_button = QPushButton("Search documents")
        self.search_button.clicked.connect(self.search_requested.emit)
        advanced_layout.addWidget(self.refresh_button)
        for button in (self.update_button, self.search_button):
            actions.addWidget(button)
        card.content_layout.addLayout(actions)
        self.job_controls = QWidget()
        job_layout = QHBoxLayout(self.job_controls)
        job_layout.setContentsMargins(0, 0, 0, 0)
        job_layout.addWidget(self.pause_button)
        job_layout.addWidget(self.stop_button)
        self.job_controls.hide()
        card.content_layout.addWidget(self.job_controls)
        self.auto_refresh = QCheckBox("Update every 2 minutes while this library is open")
        self.auto_refresh.setChecked(self._preferences.value("library/auto_refresh", False, type=bool))
        self.auto_refresh.toggled.connect(lambda value: self._preferences.setValue("library/auto_refresh", value))
        advanced_layout.addWidget(self.auto_refresh)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("Document indexing progress")
        self.progress.hide()
        card.content_layout.addWidget(self.progress)
        self.stats = QLabel("Load the library to see indexing status.")
        self.stats.setWordWrap(True)
        advanced_layout.addWidget(self.stats)
        self.job_details = QLabel()
        self.job_details.setWordWrap(True)
        self.job_details.setTextFormat(Qt.TextFormat.PlainText)
        self.job_details.hide()
        advanced_layout.addWidget(self.job_details)
        self.more_button = QPushButton("More options")
        self.more_button.setMaximumWidth(170)
        self.more_button.setCheckable(True)
        self.more_button.toggled.connect(self._toggle_options)
        card.content_layout.addWidget(self.more_button)
        card.content_layout.addWidget(self.advanced)
        self.message = QLabel("The organized destination is included automatically. Added folders are for search only.")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setProperty("role", "caption")
        card.content_layout.addWidget(self.message)
        self.ai_button = QPushButton("Prepare local AI")
        self.ai_button.setToolTip("Start installed Ollama and check your saved answer model. No models are downloaded.")
        self.ai_button.clicked.connect(self._prepare_ai)
        advanced_layout.addWidget(self.ai_button)
        filters = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter filename or path")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._render_files)
        filters.addWidget(self.filter, 1)
        self.status_filter = QComboBox()
        self.status_filter.addItems(("All files", "Ready to read", "Needs attention"))
        self.status_filter.currentIndexChanged.connect(self._render_files)
        filters.addWidget(self.status_filter)
        self.type_filter = QComboBox()
        self.type_filter.addItems(("All types", ".pdf", ".docx", ".txt", ".xlsx", ".csv", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"))
        self.type_filter.currentIndexChanged.connect(self._render_files)
        filters.addWidget(self.type_filter)
        card.content_layout.addLayout(filters)
        self.files = QTreeWidget()
        self.files.setAccessibleName("Library document status")
        self.files.setProperty("activityTable", True)
        self.files.setRootIsDecorated(False)
        self.files.setHeaderLabels(("Document", "Reading status", "Smart index", "Last indexed (UTC)"))
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.files.setMinimumHeight(340)
        self.files.setMaximumHeight(580)
        self.files.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            self.files.header().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.files.itemSelectionChanged.connect(self._selection)
        self.files.itemDoubleClicked.connect(self._open)
        self.empty_state = QLabel("Add a folder to start. FilePilot will prepare its documents automatically without moving them.")
        self.empty_state.setWordWrap(True)
        self.empty_state.hide()
        card.content_layout.addWidget(self.empty_state)
        self.document_body = QSplitter(Qt.Orientation.Horizontal)
        self.document_body.setChildrenCollapsible(False)
        self.document_body.addWidget(self.files)
        self.preview = LibraryPreview(controller)
        self.document_body.addWidget(self.preview)
        self.document_body.setStretchFactor(0, 3)
        self.document_body.setStretchFactor(1, 2)
        card.content_layout.addWidget(self.document_body)
        self.detail = QLabel("Select a document to see its full path and any indexing issue.")
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.preview.content_layout.insertWidget(2, self.detail)
        document_actions = _ActionGrid()
        # Keep selected-file actions compact inside the preview panel.
        document_actions.reflow(2)
        self.summary_button = QPushButton("Summarize selected")
        self.summary_button.setProperty("variant", "primary")
        self.summary_button.clicked.connect(lambda: self._document(False))
        self.compare_button = QPushButton("Compare two files")
        self.compare_button.clicked.connect(lambda: self._document(True))
        self.suggest_button = QPushButton("Suggest organization")
        self.suggest_button.clicked.connect(lambda: self._document(False, suggest=True))
        self.open_button = QPushButton("Open file")
        self.open_button.clicked.connect(self._open)
        self.ocr_check_button = QPushButton("Check OCR")
        self.ocr_check_button.clicked.connect(lambda: self.controller.request("ocr_status"))
        self.ocr_check_button.hide()
        self.language = QComboBox()
        self.language.addItems(("English", "Arabic", "Swedish"))
        saved_language = self._preferences.value("library/answer_language", "English")
        if saved_language in {"English", "Arabic", "Swedish"}:
            self.language.setCurrentText(saved_language)
        self.language.currentTextChanged.connect(lambda value: self._preferences.setValue("library/answer_language", value))
        self.language.setAccessibleName("Document insights language")
        for widget in (self.summary_button, self.open_button, self.language, self.compare_button, self.suggest_button):
            document_actions.addWidget(widget)
        self.document_controls = QWidget()
        self.document_controls.setLayout(document_actions)
        self.preview.content_layout.insertWidget(3, self.ocr_check_button)
        self.document_controls.hide()
        self.compare_button.hide()
        self.suggest_button.hide()
        self.preview.content_layout.insertWidget(3, self.document_controls)
        self.scope = scope = QLabel("Insights use selected indexed excerpts and local Ollama. Long documents are sampled; chunk numbers are not PDF page numbers.")
        scope.setProperty("role", "caption")
        scope.setWordWrap(True)
        scope.hide()
        card.content_layout.addWidget(scope)
        self.result_tools = QWidget()
        result_actions = _ActionGrid()
        self._rows.append(result_actions)
        self.result_tools.setLayout(result_actions)
        self.copy_button = QPushButton("Copy result")
        self.copy_button.clicked.connect(self._copy_result)
        self.save_button = QPushButton("Save result…")
        self.save_button.clicked.connect(self._save_result)
        self.source_button = QPushButton("Open source")
        self.source_button.clicked.connect(self._open_source)
        self.copy_excerpt_button = QPushButton("Copy excerpt")
        self.copy_excerpt_button.clicked.connect(self._copy_excerpt)
        for button in (self.copy_button, self.save_button, self.source_button, self.copy_excerpt_button):
            result_actions.addWidget(button)
        self.result_tools.hide()
        card.content_layout.addWidget(self.result_tools)
        self.answer = QPlainTextEdit()
        self.answer.setAccessibleName("Document summary or comparison")
        self.answer.setReadOnly(True)
        self.answer.setMinimumHeight(200)
        self.answer.document().setDocumentMargin(12)
        self.answer.hide()
        card.content_layout.addWidget(self.answer)
        self.sources = QListWidget()
        self.sources.setAccessibleName("Document insight sources")
        self.sources.setMaximumHeight(110)
        self.sources.currentRowChanged.connect(self._source)
        self.sources.hide()
        card.content_layout.addWidget(self.sources)
        self.excerpt = QPlainTextEdit()
        self.excerpt.setReadOnly(True)
        self.excerpt.setAccessibleName("Exact selected insight excerpt")
        self.excerpt.setMaximumHeight(160)
        self.excerpt.hide()
        card.content_layout.addWidget(self.excerpt)
        layout.addWidget(card)
        layout.addStretch(1)
        controller.result.connect(self._result)
        controller.error.connect(self._error)
        controller.progress.connect(self._progress)
        controller.busy_changed.connect(self._busy_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(120_000)
        self.timer.timeout.connect(self._auto_tick)
        self.timer.start()
        self._busy_changed(False)
        self._update_columns()

    def minimumSizeHint(self):
        return QSize(420, self.layout().minimumSize().height())

    def resizeEvent(self, event):
        columns = 2 if event.size().width() < 840 else 5
        for row in self._rows:
            row.reflow(min(columns, len(row.widgets)))
        narrow = event.size().width() < 840
        self.document_body.setOrientation(Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal)
        self.files.setMinimumHeight(220 if narrow else 340)
        self.files.setMaximumHeight(280 if narrow else 700)
        self.document_body.setMinimumHeight(890 if narrow else 650)
        self._update_columns(event.size().width())
        super().resizeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self.snapshot is None:
            self.controller.request("status")

    def _update_columns(self, width=None):
        width = self.width() if width is None else width
        advanced = self.more_button.isChecked()
        self.files.setColumnHidden(2, not advanced)
        self.files.setColumnHidden(3, not advanced or width < 840)

    def _toggle_options(self, checked):
        self.advanced.setVisible(checked)
        self.suggest_button.setVisible(checked)
        self._update_columns()
        self.more_button.setText("Fewer options" if checked else "More options")
        self.more_button.setAccessibleDescription("Advanced document library controls expanded" if checked else "Advanced document library controls collapsed")

    def _locked(self):
        return self._busy or self._document_busy or self._root_busy or self._ai_busy

    def _add(self):
        if self._locked():
            return
        path = QFileDialog.getExistingDirectory(self, "Add a read-only document folder")
        if path:
            self._root_busy = True
            self._busy_changed(self._busy)
            self.message.setText("Adding your folder… documents will be prepared automatically.")
            self.controller.request("add", path=path)

    def _remove(self):
        if self._locked():
            return
        path = self.roots.currentData()
        if path:
            self._root_busy = True
            self._busy_changed(self._busy)
            self.controller.request("remove", path=path)

    def start_index(self):
        if not self._locked():
            self.controller.request("index")

    def _auto_tick(self):
        if self.isVisible() and self.auto_refresh.isChecked() and not self._locked():
            self.start_index()

    def _pause(self, paused):
        self.controller.pause(paused)
        self.pause_button.setText("Resume" if paused else "Pause")
        if paused:
            self.message.setText("Pausing after the current file or model request. Resume to continue.")

    def _stop(self):
        self.controller.cancel()
        self.message.setText("Stopping after the current file or model request. Completed work is kept; update again to resume.")

    def _busy_changed(self, busy):
        self._busy = busy
        enabled = not self._locked()
        self.ai_button.setEnabled(enabled)
        self.update_button.setEnabled(enabled)
        self.add_button.setEnabled(enabled)
        self.refresh_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled and self.roots.count() > 0)
        self.pause_button.setEnabled(busy)
        self.stop_button.setEnabled(busy)
        self.job_controls.setVisible(busy)
        self.progress.setVisible(busy)
        if busy:
            self._clear_review()
            self.answer.hide()
            self.sources.hide()
            self.excerpt.hide()
            self.progress.setRange(0, 0)
            self.message.setText("Scanning local documents. You can pause or stop between files.")
        else:
            self.pause_button.setChecked(False)
        self._selection()
        self._refresh_next_step()

    def _refresh_next_step(self):
        if self._busy:
            text = "Preparing documents… completed work is kept if you stop."
        elif self._document_busy:
            text = "Creating your local document review…"
        elif self._root_busy:
            text = "Saving the library location…"
        elif self._ai_busy:
            text = "Checking local AI…"
        elif self.snapshot is None:
            text = "Loading your document library…"
        elif not self.snapshot.total:
            text = "Add a folder to begin, or prepare documents from your organized destination."
        else:
            attention = self.snapshot.total - self.snapshot.searchable
            text = f"{self.snapshot.searchable} documents ready" + (f" · {attention} need attention" if attention else "") + ". Select a document to preview, search or summarize."
        self.journey.setVisible(self.snapshot is None or not self.snapshot.total)
        self.next_step.setText(text)

    def _progress(self, progress):
        if progress.total is None:
            self.progress.setRange(0, 0)
            count = f"{progress.completed} files scanned"
        else:
            self.progress.setRange(0, max(1, progress.total))
            self.progress.setValue(progress.completed)
            count = f"{progress.completed}/{progress.total} files"
        self.message.setText(f"{progress.stage.title()} · {count} · {progress.filename}")

    def _prepare_ai(self):
        if self._locked():
            return
        self._ai_busy = True
        self._busy_changed(self._busy)
        self.message.setText("Preparing local AI… checking Ollama and the saved answer model.")
        self.controller.request("local_ai")

    def _result(self, key, result):
        if key == "ocr_status":
            if result.available:
                languages = ", ".join({"eng": "English", "ara": "Arabic", "swe": "Swedish"}.get(code, code) for code in result.languages)
                self.detail.setText(f"Local OCR found ({result.source}). Languages: {languages or 'language files missing'}.\nPrepare documents to retry. If reading still fails, open the original to check it is readable.")
            else:
                self.detail.setText("Local OCR is missing. Install FilePilot with Setup from the installer folder, reopen it and select Prepare documents. " + (result.detail or ""))
            return
        if key == "local_ai":
            self._ai_busy = False
            if not result.reachable:
                message = result.error
            elif not result.answer_installed:
                message = f"Ollama is running, but the saved answer model '{result.answer_model}' is not installed. Choose an installed model in Settings → AI. Installed models: " + ", ".join(result.models)
            else:
                message = f"Ready to summarize with {result.answer_model}. Select a document and press Summarize selected."
                if not result.embedding_installed:
                    message += " Smart search model is missing; summaries still work with indexed text."
            self.message.setText(message)
            self._busy_changed(self._busy)
            return
        if key in {"status", "add", "remove"}:
            if key in {"add", "remove"}:
                self._root_busy = False
            self.snapshot = result
            current = self.roots.currentData()
            self.roots.clear()
            for path in result.roots:
                self.roots.addItem(str(path), str(path))
            index = self.roots.findData(current)
            if index >= 0:
                self.roots.setCurrentIndex(index)
            last = result.last_run
            stamp = last.get("finished_at", "Not recorded by this version")
            self.stats.setText(f"{result.total} files cataloged · {result.searchable} with indexed text · {result.semantic} with stored smart vectors\nLast update: {stamp}" + (" · Stopped before completion" if last.get("cancelled") else ""))
            self._render_files()
            self.document_controls.setVisible(bool(result.total))
            self.scope.setVisible(bool(result.total))
            self._busy_changed(self._busy)
            if key == "add":
                self.start_index()
            elif key == "remove":
                self.message.setText("Folder removed from library locations. Prepare documents to update search results; original files are kept.")
        elif key == "index":
            text = "Preparation stopped. Completed work is kept; select Prepare documents to continue." if result.cancelled else (
                f"Preparation finished: {result.indexed} updated, {result.unchanged} already up to date.")
            if result.failed or result.semantic_failed:
                text += f" {result.failed + result.semantic_failed} issues need attention; check the file status or More options."
            if not result.semantic_available:
                text += " You can search indexed text without smart search."
            self.message.setText(text)
            self.job_details.setText("\n".join(result.errors[:5]))
            self.job_details.setVisible(bool(result.errors))
            self.controller.request("status")
        elif key == "document":
            self._document_busy = False
            self._review = result
            self.answer.setPlainText(result.answer)
            self._reading_direction(self.answer, result.answer)
            self.result_tools.show()
            self.answer.show()
            self._source_records = result.sources
            self.excerpt.clear()
            self.excerpt.hide()
            self.sources.clear()
            for source in result.sources:
                self.sources.addItem(f"[{source.source_id}] {source.filename} · Excerpt {source.chunk_index + 1}")
            self.sources.setVisible(bool(result.sources))
            self.source_button.setEnabled(bool(result.sources))
            self.copy_excerpt_button.setEnabled(bool(result.sources))
            if result.sources:
                self.sources.setCurrentRow(0)
            self.message.setText("The selected excerpts did not provide enough relevant evidence. No supported summary was produced."
                if result.status == "no_evidence" else
                "Local excerpt-based review complete. Verify the sources below. Organization proposals are suggestions only; no files were changed.")
            self._busy_changed(self._busy)

    def _error(self, key, message):
        if key == "preview":
            return
        if key == "local_ai":
            self._ai_busy = False
        if key in {"add", "remove"}:
            self._root_busy = False
        if key == "document":
            self._document_busy = False
        self.message.setText(message)
        if key == "document" and "Prepare local AI" in message:
            self.more_button.setChecked(True)
        self._busy_changed(self._busy)

    def _render_files(self, *_args):
        if self.snapshot is None:
            return
        selected = set(self._selected_paths())
        self.files.clear()
        query = self.filter.text().casefold().strip()
        for record in self.snapshot.files:
            if query and query not in str(record.path).casefold():
                continue
            if self.type_filter.currentIndex() and record.path.suffix.lower() != self.type_filter.currentText():
                continue
            if self.status_filter.currentIndex() == 1 and record.status != "indexed":
                continue
            if self.status_filter.currentIndex() == 2 and record.status == "indexed" and not record.issue:
                continue
            item = QTreeWidgetItem((record.path.name, STATUS.get(record.status, record.status),
                "Stored" if record.semantic and record.status == "indexed" else "Not ready", record.indexed_at[:19].replace("T", " ")))
            icon = QStyle.StandardPixmap.SP_FileIcon
            item.setIcon(0, self.style().standardIcon(icon))
            item.setForeground(1, QColor("#8dd9b0" if record.status == "indexed" else "#ffc17a"))
            item.setData(0, Qt.ItemDataRole.UserRole, record)
            item.setToolTip(0, str(record.path))
            item.setToolTip(1, record.issue or STATUS.get(record.status, record.status))
            self.files.addTopLevelItem(item)
            item.setSelected(record.path in selected)
        visible = self.files.topLevelItemCount() > 0
        self.files.setVisible(visible)
        self.document_body.setVisible(visible)
        self.empty_state.setVisible(not visible)
        self.empty_state.setText("No documents match these filters. Clear or change the filters to see your files." if self.snapshot.total else
            "Add a folder to start. FilePilot will prepare its documents automatically without moving them.")
        self._selection()
        if self.snapshot.truncated:
            self.detail.setText("Showing at most 500 cataloged files. Search files searches the complete index.")

    def _selected_paths(self):
        return [item.data(0, Qt.ItemDataRole.UserRole).path for item in self.files.selectedItems()]

    def _selection(self):
        paths = self._selected_paths()
        self.preview.select(paths[0] if len(paths) == 1 else None)
        enabled = not self._locked()
        readable = bool(paths) and all(item.data(0, Qt.ItemDataRole.UserRole).status == "indexed" for item in self.files.selectedItems())
        self.summary_button.setEnabled(enabled and readable and len(paths) == 1)
        self.suggest_button.setEnabled(enabled and readable and len(paths) == 1)
        self.compare_button.setEnabled(enabled and readable and len(paths) == 2)
        self.compare_button.setVisible(len(paths) == 2)
        self.open_button.setEnabled(len(paths) == 1)
        self.ocr_check_button.setVisible(len(paths) == 1 and self.files.selectedItems()[0].data(0, Qt.ItemDataRole.UserRole).status in {"ocr_required", "ocr_unavailable", "extraction_failed"})
        if len(paths) == 1:
            record = self.files.selectedItems()[0].data(0, Qt.ItemDataRole.UserRole)
            advice = "Select Summarize for a local review with sources." if readable else "This file has no current readable text. Prepare documents or check its reading status before summarizing."
            self.detail.setText(f"{STATUS.get(record.status, record.status)} · {record.path.suffix.upper().lstrip(chr(46)) or chr(8212)}\n{record.issue or advice}")
            self.detail.setToolTip(str(record.path))
        elif paths:
            self.detail.setText(f"{len(paths)} files selected. Select exactly two with indexed text to compare.")
        else:
            self.detail.setText("Select a document to preview its pages and choose what to do. Ctrl-click two readable documents to compare.")

    def _document(self, compare, *, suggest=False):
        paths = self._selected_paths()
        if len(paths) != (2 if compare else 1) or self._locked():
            return
        self._start_document(paths, compare=compare, suggest=suggest)

    def summarize_path(self, path):
        if not self._locked():
            self._start_document([Path(path)])

    def _start_document(self, paths, *, compare=False, suggest=False):
        self._document_busy = True
        self._clear_review()
        self.message.setText("Preparing local AI and reading selected excerpts… the first answer may take longer while the model loads.")
        self._busy_changed(False)
        self.controller.request("document", paths=paths, compare=compare, suggest=suggest, language=self.language.currentText())

    def _source(self, index):
        if 0 <= index < len(self._source_records):
            source = self._source_records[index]
            self.excerpt.setPlainText(f"\u2066{source.path}\u2069\n\n{source.excerpt}")
            self._reading_direction(self.excerpt, source.excerpt)
            self.excerpt.show()

    def _clear_review(self):
        self._review = None
        self._source_records = ()
        self.result_tools.hide()
        self.answer.clear()
        self.answer.hide()
        self.sources.clear()
        self.sources.hide()
        self.excerpt.clear()
        self.excerpt.hide()

    @staticmethod
    def _reading_direction(editor, text):
        import unicodedata
        direction = Qt.LayoutDirection.LeftToRight
        for character in text:
            bidi = unicodedata.bidirectional(character)
            if bidi in {"R", "AL", "L"}:
                direction = Qt.LayoutDirection.RightToLeft if bidi in {"R", "AL"} else Qt.LayoutDirection.LeftToRight
                break
        option = editor.document().defaultTextOption()
        option.setTextDirection(direction)
        option.setAlignment(Qt.AlignmentFlag.AlignRight if direction == Qt.LayoutDirection.RightToLeft else Qt.AlignmentFlag.AlignLeft)
        editor.document().setDefaultTextOption(option)

    def _review_text(self, *, excerpts=False):
        if self._review is None:
            return ""
        lines = ["FilePilot document review", "", self._review.answer, "",
                 "Scope: indexed excerpts; long documents may be sampled. Excerpt numbers are not PDF page numbers."]
        if self._review.sources:
            lines += ["", "Sources"]
        for source in self._review.sources:
            lines.append(f"[{source.source_id}] {source.filename} · Excerpt {source.chunk_index + 1}")
            if excerpts:
                lines.extend([str(source.path), source.excerpt, ""])
        return "\n".join(lines).rstrip() + "\n"

    def _copy_result(self):
        text = self._review_text()
        if text:
            QApplication.clipboard().setText(text)
            self.message.setText("Result and source references copied.")

    def _copy_excerpt(self):
        index = self.sources.currentRow()
        if 0 <= index < len(self._source_records):
            QApplication.clipboard().setText(self._source_records[index].excerpt)
            self.message.setText("Exact source excerpt copied.")

    def _save_result(self):
        if self._review is None:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Save review with source excerpts", "FilePilot-review.txt", "Text files (*.txt)")
        if not filename:
            return
        path = Path(filename)
        if path.suffix.casefold() != ".txt":
            self.message.setText("Choose a review filename ending in .txt.")
            return
        originals = {source.path for source in self._review.sources}
        if self.snapshot is not None:
            originals.update(record.path for record in self.snapshot.files)
        try:
            if any(path.resolve() == original.resolve() or (path.exists() and original.exists() and path.samefile(original)) for original in originals):
                self.message.setText("Choose a new filename for the review. Original library documents cannot be overwritten.")
                return
            output = QSaveFile(str(path))
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                self.message.setText("Could not save the review. Choose a writable folder and try again.")
                return
            data = self._review_text(excerpts=True).encode("utf-8-sig")
            if output.write(data) != len(data):
                output.cancelWriting()
                self.message.setText("Could not finish saving the review. Try another location.")
                return
            if not output.commit():
                self.message.setText("Could not finish saving the review. Try another location.")
                return
        except OSError:
            self.message.setText("Could not save the review. Check the destination and try again.")
            return
        self.message.setText(f"Review saved with its exact source excerpts: {path}")

    def _open_source(self):
        index = self.sources.currentRow()
        if 0 <= index < len(self._source_records):
            path = self._source_records[index].path
            if not path.is_file():
                self.message.setText("This source file is no longer available. The captured excerpt remains visible below.")
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                self.message.setText("Windows could not open the source. Check its default application.")

    def _open(self, *_args):
        paths = self._selected_paths()
        if len(paths) == 1:
            if not paths[0].is_file():
                self.message.setText("This file is no longer available. Update the library.")
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths[0]))):
                self.message.setText("Windows could not open this file. Check its default application.")
