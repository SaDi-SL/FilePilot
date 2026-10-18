from pathlib import Path

from PySide6.QtCore import QSettings, QTimer, Qt, QUrl, QSize, QSaveFile, QIODevice
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QAbstractItemView, QCheckBox, QComboBox, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QGridLayout, QLayout)
from app.ui.qt.widgets.section_card import SectionCard


STATUS = {
    "indexed": "Text indexed", "metadata_only": "Filename only", "unsupported_format": "Filename only",
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
    def __init__(self, controller, bridge, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.bridge = bridge
        self.snapshot = None
        self._busy = False
        self._document_busy = False
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
        hint = QLabel("Add folders to search and understand their documents. Library folders are read only: adding one never moves or reorganizes its files.")
        hint.setWordWrap(True)
        hint.setProperty("role", "secondary")
        card.content_layout.addWidget(heading)
        card.content_layout.addWidget(hint)
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
        self.remove_button.setToolTip("Removes only this library location; original files are kept. Update the index afterward.")
        self.remove_button.clicked.connect(self._remove)
        roots.addWidget(self.remove_button)
        card.content_layout.addLayout(roots)
        actions = _ActionGrid()
        self._rows.append(actions)
        self.update_button = QPushButton("Update / retry index")
        self.update_button.setProperty("variant", "primary")
        self.update_button.clicked.connect(self.start_index)
        self.pause_button = QPushButton("Pause")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._pause)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self._stop)
        self.refresh_button = QPushButton("Refresh status")
        self.refresh_button.clicked.connect(lambda: controller.request("status"))
        for button in (self.update_button, self.pause_button, self.stop_button, self.refresh_button):
            actions.addWidget(button)
        card.content_layout.addLayout(actions)
        self.auto_refresh = QCheckBox("Update every 2 minutes while this library is open")
        self.auto_refresh.setChecked(self._preferences.value("library/auto_refresh", False, type=bool))
        self.auto_refresh.toggled.connect(lambda value: self._preferences.setValue("library/auto_refresh", value))
        card.content_layout.addWidget(self.auto_refresh)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("Document indexing progress")
        self.progress.hide()
        card.content_layout.addWidget(self.progress)
        self.stats = QLabel("Load the library to see indexing status.")
        self.stats.setWordWrap(True)
        card.content_layout.addWidget(self.stats)
        self.message = QLabel("The organized destination is included automatically. Added folders are for search only.")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setProperty("role", "caption")
        card.content_layout.addWidget(self.message)
        self.ai_button = QPushButton("Prepare local AI")
        self.ai_button.setToolTip("Start installed Ollama and check your saved answer model. No models are downloaded.")
        self.ai_button.clicked.connect(self._prepare_ai)
        card.content_layout.addWidget(self.ai_button)
        filters = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter filename or path")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._render_files)
        filters.addWidget(self.filter, 1)
        self.status_filter = QComboBox()
        self.status_filter.addItems(("All files", "Text indexed", "Needs attention"))
        self.status_filter.currentIndexChanged.connect(self._render_files)
        filters.addWidget(self.status_filter)
        self.type_filter = QComboBox()
        self.type_filter.addItems(("All types", ".pdf", ".docx", ".txt", ".xlsx", ".csv"))
        self.type_filter.currentIndexChanged.connect(self._render_files)
        filters.addWidget(self.type_filter)
        card.content_layout.addLayout(filters)
        self.files = QTreeWidget()
        self.files.setAccessibleName("Library document status")
        self.files.setProperty("activityTable", True)
        self.files.setRootIsDecorated(False)
        self.files.setHeaderLabels(("Document", "Text", "Smart index", "Last indexed (UTC)"))
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.files.setMinimumHeight(210)
        self.files.setMaximumHeight(340)
        self.files.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            self.files.header().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.files.itemSelectionChanged.connect(self._selection)
        self.files.itemDoubleClicked.connect(self._open)
        card.content_layout.addWidget(self.files)
        self.detail = QLabel("Select a document to see its full path and any indexing issue.")
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.content_layout.addWidget(self.detail)
        document_actions = _ActionGrid()
        self._rows.append(document_actions)
        self.summary_button = QPushButton("Summarize selected")
        self.summary_button.clicked.connect(lambda: self._document(False))
        self.compare_button = QPushButton("Compare two files")
        self.compare_button.clicked.connect(lambda: self._document(True))
        self.suggest_button = QPushButton("Suggest organization")
        self.suggest_button.clicked.connect(lambda: self._document(False, suggest=True))
        self.open_button = QPushButton("Open file")
        self.open_button.clicked.connect(self._open)
        self.language = QComboBox()
        self.language.addItems(("English", "Arabic", "Swedish"))
        saved_language = self._preferences.value("library/answer_language", "English")
        if saved_language in {"English", "Arabic", "Swedish"}:
            self.language.setCurrentText(saved_language)
        self.language.currentTextChanged.connect(lambda value: self._preferences.setValue("library/answer_language", value))
        self.language.setAccessibleName("Document insights language")
        for widget in (self.summary_button, self.compare_button, self.suggest_button, self.open_button, self.language):
            document_actions.addWidget(widget)
        card.content_layout.addLayout(document_actions)
        scope = QLabel("Insights use selected indexed excerpts and local Ollama. Long documents are sampled; chunk numbers are not PDF page numbers.")
        scope.setProperty("role", "caption")
        scope.setWordWrap(True)
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

    def minimumSizeHint(self):
        return QSize(420, 380)

    def resizeEvent(self, event):
        columns = 2 if event.size().width() < 840 else 5
        for row in self._rows:
            row.reflow(min(columns, len(row.widgets)))
        self.files.setColumnHidden(3, event.size().width() < 840)
        super().resizeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self.snapshot is None:
            self.controller.request("status")

    def _add(self):
        path = QFileDialog.getExistingDirectory(self, "Add a read-only document folder")
        if path:
            self.controller.request("add", path=path)

    def _remove(self):
        path = self.roots.currentData()
        if path:
            self.controller.request("remove", path=path)

    def start_index(self):
        if not self._busy and not self._document_busy:
            self.controller.request("index")

    def _auto_tick(self):
        if self.isVisible() and self.auto_refresh.isChecked() and not self._busy and not self._document_busy:
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
        self.ai_button.setEnabled(not busy and not self._document_busy and not getattr(self, "_ai_busy", False))
        self.update_button.setEnabled(not busy and not self._document_busy)
        self.add_button.setEnabled(not busy and not self._document_busy)
        self.remove_button.setEnabled(not busy and not self._document_busy and self.roots.count() > 0)
        self.pause_button.setEnabled(busy)
        self.stop_button.setEnabled(busy)
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
        self._ai_busy = True
        self.ai_button.setEnabled(False)
        self.message.setText("Preparing local AI… checking Ollama and the saved answer model.")
        self.controller.request("local_ai")

    def _result(self, key, result):
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
            self.remove_button.setEnabled(not self._busy and bool(result.roots))
            self._render_files()
            if key in {"add", "remove"}:
                self.message.setText("Library location saved. Update the index to apply the change. Original files are unchanged.")
        elif key == "index":
            self.message.setText(("Indexing stopped. " if result.cancelled else "Index update finished. ") +
                f"{result.indexed} indexed, {result.unchanged} unchanged, {result.failed + result.semantic_failed} job errors. " +
                ("Smart index updated." if result.semantic_available else "Smart index unavailable; filename and extracted-text search still work.") +
                ("\n" + "\n".join(result.errors[:5]) if result.errors else ""))
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
        if key == "local_ai":
            self._ai_busy = False
        if key == "document":
            self._document_busy = False
        self.message.setText(message)
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
                "Stored" if record.semantic else "Not indexed", record.indexed_at[:19].replace("T", " ")))
            item.setData(0, Qt.ItemDataRole.UserRole, record)
            item.setToolTip(0, str(record.path))
            item.setToolTip(1, record.issue or STATUS.get(record.status, record.status))
            self.files.addTopLevelItem(item)
            item.setSelected(record.path in selected)
        self._selection()
        if self.snapshot.truncated:
            self.detail.setText("Showing at most 500 cataloged files. Search files searches the complete index.")

    def _selected_paths(self):
        return [item.data(0, Qt.ItemDataRole.UserRole).path for item in self.files.selectedItems()]

    def _selection(self):
        paths = self._selected_paths()
        enabled = not self._busy and not self._document_busy
        self.summary_button.setEnabled(enabled and len(paths) == 1)
        self.suggest_button.setEnabled(enabled and len(paths) == 1)
        self.compare_button.setEnabled(enabled and len(paths) == 2)
        self.open_button.setEnabled(len(paths) == 1)
        if len(paths) == 1:
            record = self.files.selectedItems()[0].data(0, Qt.ItemDataRole.UserRole)
            self.detail.setText(f"{record.path}\n{record.issue or 'Select Summarize for a local review with sources.'}")
        elif paths:
            self.detail.setText(f"{len(paths)} files selected. Select exactly two to compare.")

    def _document(self, compare, *, suggest=False):
        paths = self._selected_paths()
        if len(paths) != (2 if compare else 1) or self._busy or self._document_busy:
            return
        self._start_document(paths, compare=compare, suggest=suggest)

    def summarize_path(self, path):
        if not self._busy and not self._document_busy:
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
