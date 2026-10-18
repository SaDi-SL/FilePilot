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
    ask_requested = Signal()
    result_ready = Signal(QWidget)

    def __init__(self, controller, bridge, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.bridge = bridge
        self.snapshot = None
        self._busy = False
        self._document_busy = False
        self._document_mode = "overview"
        self._root_busy = False
        self._ai_busy = False
        self._pause_requested = False
        self._stop_requested = False
        self._index_feedback = ""
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
        self.steps = QWidget()
        self.step_layout = _ActionGrid()
        self.steps.setLayout(self.step_layout)
        self.step_labels = [QLabel(text) for text in ("1 · Choose a folder", "2 · Prepare documents", "3 · Search or summarize")]
        for label in self.step_labels:
            label.setMinimumHeight(40)
            label.setWordWrap(True)
            self.step_layout.addWidget(label)
        self.step_layout.reflow(3)
        card.content_layout.addWidget(self.steps)
        self.next_step = QLabel("Loading your document library…")
        self.next_step.setWordWrap(True)
        card.content_layout.addWidget(self.next_step)
        self.health = QWidget()
        health_layout = QHBoxLayout(self.health)
        health_layout.setContentsMargins(0, 0, 0, 0)
        self.ready_count = QLabel()
        self.ready_count.setProperty("badgeTone", "success")
        self.attention_count = QLabel()
        self.attention_count.setProperty("badgeTone", "warning")
        self.attention_button = QPushButton("Review issues")
        self.attention_button.clicked.connect(self._review_issues)
        for widget in (self.ready_count, self.attention_count, self.attention_button):
            health_layout.addWidget(widget)
        health_layout.addStretch(1)
        self.health.hide()
        card.content_layout.addWidget(self.health)
        self.advanced = QWidget()
        advanced_layout = _ActionGrid()
        self.advanced.setLayout(advanced_layout)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.advanced.hide()
        roots = _ActionGrid()
        self._rows = [roots]
        self.roots = QComboBox()
        self.roots.setAccessibleName("Additional read-only library folders")
        self.roots.setPlaceholderText("No extra document folders added")
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
        self.ask_files_button = QPushButton("Ask your files")
        self.ask_files_button.setToolTip("Ask across the indexed library. For one selected file, use Summarize selected.")
        self.ask_files_button.clicked.connect(self.ask_requested.emit)
        advanced_layout.addWidget(self.refresh_button)
        for button in (self.update_button, self.search_button, self.ask_files_button):
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
        self.filters = QWidget()
        self.filters.setLayout(filters)
        filters.setContentsMargins(0, 0, 0, 0)
        self.filters.hide()
        card.content_layout.addWidget(self.filters)
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
        self.clear_filters_button = QPushButton("Clear filters")
        self.clear_filters_button.clicked.connect(self._clear_filters)
        self.clear_filters_button.hide()
        card.content_layout.addWidget(self.clear_filters_button)
        self.document_body = QSplitter(Qt.Orientation.Horizontal)
        self.document_body.setChildrenCollapsible(False)
        self.document_body.addWidget(self.files)
        self.preview = LibraryPreview(controller)
        self.preview.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
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
        self.summary_mode = QComboBox()
        for label, value in (("Quick summary", "quick"), ("Key points", "key_points"), ("Actions & dates", "actions")):
            self.summary_mode.addItem(label, value)
        saved_mode = self._preferences.value("library/summary_format", "quick")
        mode_index = self.summary_mode.findData(saved_mode)
        if mode_index >= 0:
            self.summary_mode.setCurrentIndex(mode_index)
        self.summary_mode.setAccessibleName("Summary format")
        self.summary_mode.setToolTip("Choose what to extract from one selected document. All formats use sampled indexed excerpts and source references.")
        self.mode_hint = QLabel()
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setProperty("role", "caption")
        self.summary_mode.currentIndexChanged.connect(self._summary_mode_changed)
        self._summary_mode_changed()
        self.preview.content_layout.insertWidget(3, self.mode_hint)
        for widget in (self.summary_mode, self.language, self.summary_button, self.open_button, self.compare_button, self.suggest_button):
            document_actions.addWidget(widget)
        self.document_controls = QWidget()
        self.document_controls.setLayout(document_actions)
        self.preview.content_layout.insertWidget(3, self.ocr_check_button)
        self.document_controls.hide()
        self.compare_button.hide()
        self.suggest_button.hide()
        self.preview.content_layout.insertWidget(3, self.document_controls)
        self.document_status = QLabel()
        self.document_status.setWordWrap(True)
        self.document_status.setTextFormat(Qt.TextFormat.PlainText)
        self.document_status.hide()
        self.preview.content_layout.insertWidget(3, self.document_status)
        self.document_progress = QProgressBar()
        self.document_progress.setRange(0, 0)
        self.document_progress.setAccessibleName("Preparing document insights")
        self.document_progress.hide()
        self.preview.content_layout.insertWidget(4, self.document_progress)
        self.result_heading = QLabel()
        self.result_heading.setProperty("role", "sectionTitle")
        self.result_heading.setTextFormat(Qt.TextFormat.PlainText)
        self.result_heading.setWordWrap(True)
        self.result_heading.hide()
        card.content_layout.addWidget(self.result_heading)
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
        self.step_layout.reflow(1 if narrow else 3)
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
        self._pause_requested = paused
        self.controller.pause(paused)
        self.pause_button.setText("Resume" if paused else "Pause")
        self._show_index_feedback()

    def _stop(self):
        self._stop_requested = True
        self.controller.cancel()
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self._show_index_feedback()

    def _busy_changed(self, busy):
        starting = busy and not self._busy
        self._busy = busy
        enabled = not self._locked()
        self.summary_button.setText("Preparing result…" if self._document_busy else "Summarize selected")
        self.ai_button.setEnabled(enabled)
        self.update_button.setEnabled(enabled)
        self.add_button.setEnabled(enabled)
        self.refresh_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled and self.roots.count() > 0)
        self.pause_button.setEnabled(busy and not self._stop_requested)
        self.stop_button.setEnabled(busy and not self._stop_requested)
        self.job_controls.setVisible(busy)
        self.progress.setVisible(busy)
        if starting:
            self._pause_requested = self._stop_requested = False
            self.pause_button.setEnabled(True)
            self.stop_button.setEnabled(True)
            self._clear_review()
            self.answer.hide()
            self.sources.hide()
            self.excerpt.hide()
            self.progress.setRange(0, 0)
            self._index_feedback = "Finding and reading your documents… originals stay in place."
            self._show_index_feedback()
        elif not busy:
            self.pause_button.blockSignals(True)
            self.pause_button.setChecked(False)
            self.pause_button.blockSignals(False)
            self.pause_button.setText("Pause")
            self._pause_requested = self._stop_requested = False
        self._selection()
        self._refresh_next_step()

    def _refresh_next_step(self):
        records = self.snapshot.files if self.snapshot else ()
        ready = len({r.path for r in records if r.status == "indexed"})
        attention = len({r.path for r in records if r.status != "indexed" or r.issue})
        self.health.setVisible(bool(records))
        self.search_button.setEnabled(bool(self.snapshot and self.snapshot.searchable))
        self.ask_files_button.setEnabled(bool(self.snapshot and self.snapshot.searchable) and not self._locked())
        self.ready_count.setText(f"Shown: {ready} ready to read")
        self.attention_count.setText(f"Needs attention: {attention}")
        self.attention_count.setVisible(bool(attention))
        self.attention_button.setVisible(bool(attention))
        self.attention_button.setEnabled(not self._locked())
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
        elif not records:
            text = ("Your folder is saved. Select Prepare documents to read its files."
                if self.snapshot.roots and not self.snapshot.last_run else
                "No documents found in the last preparation. Add a folder containing PDF, Office, text or image files."
                if self.snapshot.last_run else
                "Choose a document folder to begin. FilePilot will read its files without moving them.")
        else:
            text = ("Select a ready document to preview or summarize, or search across your prepared documents."
                if ready else "Your documents need a reading check. Review issues, then prepare documents again.")
            if self.snapshot.truncated:
                text += " Counts describe the displayed catalog; Search documents covers the full index."
        if self.snapshot and self.snapshot.last_run.get("cancelled") and not self._locked():
            text = "Preparation was stopped. Completed files are kept; select Continue preparation to resume. " + text
        self.steps.setVisible(not ready)
        active = 0 if not self.snapshot or not (self.snapshot.roots or records) else 1 if not ready else 2
        for index, label in enumerate(self.step_labels):
            label.setProperty("badgeTone", "success" if index < active else "info" if index == active else "neutral")
            label.style().unpolish(label)
            label.style().polish(label)
        self.add_button.setText("Add another folder" if records or self.snapshot and self.snapshot.roots else "Choose document folder")
        self.update_button.setText("Preparing documents…" if self._busy else
            "Continue preparation" if self.snapshot and self.snapshot.last_run.get("cancelled") else "Prepare documents")
        self.next_step.setText(text)
        if self._busy and not records:
            self.empty_state.setText("Your documents will appear here when preparation finishes. Completed work is kept if you stop.")

    def _clear_filters(self):
        self.filter.clear()
        self.type_filter.setCurrentIndex(0)
        self.status_filter.setCurrentIndex(0)

    def _review_issues(self):
        self.filter.clear()
        self.type_filter.setCurrentIndex(0)
        self.status_filter.setCurrentIndex(2)

    def _show_index_feedback(self):
        prefix = ("Stopping after the current file or model request. Completed work is kept. " if self._stop_requested else
            "Pause requested; the current file or model request may finish first. Select Resume to continue. " if self._pause_requested else "")
        self.message.setText(prefix + self._index_feedback)

    def _progress(self, progress):
        if progress.total is None:
            self.progress.setRange(0, 0)
            count = f"{progress.completed} files scanned"
        else:
            self.progress.setRange(0, max(1, progress.total))
            self.progress.setValue(progress.completed)
            count = f"{progress.completed}/{progress.total} files"
        stage = {"extracting": "Reading documents", "semantic": "Improving smart search"}.get(progress.stage, "Preparing documents")
        self._index_feedback = f"{stage} · {count}" + (f" · {progress.filename}" if progress.filename else "")
        if progress.stage == "semantic":
            self._index_feedback += ". Text search and summaries do not require smart search."
        self._show_index_feedback()

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
            if self.roots.count():
                self.roots.setCurrentIndex(index if index >= 0 else 0)
            last = result.last_run
            stamp = last.get("finished_at", "Not recorded by this version")
            self.stats.setText(f"{result.total} files cataloged · {result.searchable} with indexed text · {result.semantic} with stored smart vectors\nLast update: {stamp}" + (" · Stopped before completion" if last.get("cancelled") else ""))
            self._render_files()
            self.document_controls.setVisible(bool(result.files))
            self.scope.setVisible(bool(result.files))
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
            self.document_progress.hide()
            self.document_status.setText(("No explicit tasks or dates were identified in the selected excerpts." if self._document_mode == "actions" else "No supported summary found. See the result below.") if result.status == "no_evidence"
                else "Your result is ready below, with sources to check.")
            self.result_heading.show()
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
            self.message.setText(("No explicit tasks or dates were identified in the selected excerpts; the full document may contain more." if self._document_mode == "actions" else "The selected excerpts did not provide enough relevant evidence. No supported summary was produced.")
                if result.status == "no_evidence" else
                "Local excerpt-based review complete. Verify the sources below. Organization proposals are suggestions only; no files were changed.")
            self._busy_changed(self._busy)

            self.result_ready.emit(self.result_heading)

    def _error(self, key, message):
        if key in {"preview", "search_preview"}:
            return
        if key == "local_ai":
            self._ai_busy = False
        if key in {"add", "remove"}:
            self._root_busy = False
        if key == "document":
            self._document_busy = False
            self.document_progress.hide()
            self.document_status.setText(message)
            self.document_status.show()
        self.message.setText(message)
        if key == "document" and "Prepare local AI" in message:
            self.more_button.setChecked(True)
        self._busy_changed(self._busy)

    def _render_files(self, *_args):
        if self.snapshot is None:
            return
        selected = set(self._selected_paths())
        self.filters.setVisible(bool(self.snapshot.files))
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
        self.clear_filters_button.setVisible(not visible and bool(self.filter.text() or self.status_filter.currentIndex() or self.type_filter.currentIndex()))
        self.empty_state.setText("No documents match these filters. Clear or change the filters to see your files." if self.snapshot.files else
            "No documents found. Add a folder containing PDF, Office, text or image files." if self.snapshot.last_run else
            "Select Prepare documents to read the saved library folders." if self.snapshot.roots else
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
        self.summary_button.setText("Preparing result…" if self._document_busy else "Summarize selected")
        readable = bool(paths) and all(item.data(0, Qt.ItemDataRole.UserRole).status == "indexed" for item in self.files.selectedItems())
        self.summary_button.setEnabled(enabled and readable and len(paths) == 1)
        self.summary_mode.setEnabled(enabled and readable and len(paths) == 1)
        self.language.setEnabled(enabled and readable)
        self.mode_hint.setVisible(readable and len(paths) == 1)
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

    def _summary_mode_changed(self, *_args):
        mode = self.summary_mode.currentData()
        self._preferences.setValue("library/summary_format", mode)
        self.mode_hint.setText({"quick": "Up to 3 brief points from the selected excerpts.",
            "key_points": "Findings, decisions, requirements and limitations supported by the excerpts.",
            "actions": "Only explicit tasks or dates. Missing owners and deadlines must not be guessed; verify the cited context."}[mode])

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
        self._document_mode = "overview" if compare or suggest else self.summary_mode.currentData()
        operation = "Comparison" if compare else "Organization suggestions" if suggest else self.summary_mode.currentText()
        self.result_heading.setText(f"{operation} · " + " / ".join(Path(path).name for path in paths))
        self.document_status.setText("Reading your document and preparing the result… The first answer may take longer while the local model loads.")
        self.document_status.show()
        self.document_progress.show()
        self.message.setText("Preparing local AI and reading selected excerpts… the first answer may take longer while the model loads.")
        self._busy_changed(False)
        self.controller.request("document", paths=paths, compare=compare, suggest=suggest, language=self.language.currentText(), mode=self._document_mode)

    def _source(self, index):
        if 0 <= index < len(self._source_records):
            source = self._source_records[index]
            self.excerpt.setPlainText(f"\u2066{source.path}\u2069\n\n{source.excerpt}")
            self._reading_direction(self.excerpt, source.excerpt)
            self.excerpt.show()

    def _clear_review(self):
        self._review = None
        self.result_heading.hide()
        self.document_status.hide()
        self.document_progress.hide()
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
        lines = ["FilePilot document review", self.result_heading.text(), "", self._review.answer, "",
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
