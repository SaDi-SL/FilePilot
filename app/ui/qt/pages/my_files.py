from __future__ import annotations

from pathlib import Path
from html import escape

from PySide6.QtCore import Qt, Signal, QUrl, QTimer, QPoint
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QBoxLayout,
    QProgressBar,
    QLabel,
    QLineEdit,
    QLayout,
    QScrollArea,
    QTabWidget,
    QListWidget,
    QListView,
    QListWidgetItem,
    QPushButton,
    QPlainTextEdit,
    QTextBrowser,
    QSplitter,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.application_service import MoveStatus, OperationPreview, SafetyDataState
from app.ui.qt.service_bridge import QtServiceBridge
from app.ui.qt.icons import folder_art
from app.ui.qt.theme.tokens import SPACING
from app.ui.qt.widgets.section_card import SectionCard
from app.ui.qt.widgets.file_browser import FileBrowser
from app.ui.qt.widgets.library_workspace import LibraryWorkspace
from app.ui.qt.widgets.library_preview import LibraryPreview
from app.ui.qt.widgets.search_results import SearchResultDelegate, HTML_ROLE, RESULT_ROLE, compact_excerpt, highlighted_text


SMART_RESULTS_VISIBLE_LIMIT = 8


class FileDropZone(QFrame):
    file_selected = Signal(str)
    browse_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("dropZone", True)
        self.setAcceptDrops(True)
        self.setAccessibleName("Choose a file for organization preview")
        self.setMinimumHeight(170)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACING.xl,
            SPACING.xl,
            SPACING.xl,
            SPACING.xl,
        )
        layout.setSpacing(SPACING.sm)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel()
        icon.setPixmap(folder_art())
        icon.setAccessibleName("Folder illustration")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Drop a file here")
        title.setProperty("role", "sectionTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        hint = QLabel("or choose one from your computer")
        hint.setProperty("role", "secondary")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.browse_button = QPushButton("Choose file")
        self.browse_button.setAccessibleName("Choose a file to preview")
        self.browse_button.clicked.connect(self.browse_requested)

        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addSpacing(SPACING.xs)
        layout.addWidget(self.browse_button, 0, Qt.AlignmentFlag.AlignCenter)

    def dragEnterEvent(self, event) -> None:
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(Path(url.toLocalFile()).is_file() for url in urls if url.isLocalFile()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_file():
                self.file_selected.emit(str(path))
                event.acceptProposedAction()
                return
        event.ignore()


class MyFilesPage(QWidget):
    def __init__(
        self,
        bridge: QtServiceBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._source: Path | None = None
        self._preview_pending = False
        self._organize_pending = False

        self.setObjectName("MyFilesPage")
        self.setProperty("pageSurface", True)
        self.setAccessibleName("My Files")

        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setAccessibleName("My Files scrollable workspace")
        content = QWidget()
        self.scroll_area.setWidget(content)
        page_layout.addWidget(self.scroll_area)
        layout = QVBoxLayout(content)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(
            SPACING.xl,
            SPACING.xl,
            SPACING.xl,
            SPACING.xl,
        )
        layout.setSpacing(SPACING.lg)

        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(SPACING.xs)
        eyebrow = QLabel("WORKSPACE")
        eyebrow.setProperty("role", "eyebrow")
        title = QLabel("My Files")
        title.setProperty("role", "pageTitle")
        description = QLabel(
            "Browse your organized files, find what you need, or preview a file before organizing it."
        )
        description.setProperty("role", "secondary")
        description.setWordWrap(True)
        heading.addWidget(eyebrow)
        heading.addWidget(title)
        heading.addWidget(description)
        header.addLayout(heading, 1)

        preview_only = QLabel("Safety first")
        preview_only.setProperty("badgeTone", "info")
        preview_only.setToolTip("FilePilot previews first, then revalidates safety again before organizing.")
        header.addWidget(preview_only, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self.search_card = SectionCard()
        self.search_card.setProperty("cardAccent", "cool")
        search_header = QHBoxLayout()
        search_copy = QVBoxLayout()
        search_copy.setSpacing(2)
        search_title = QLabel("Search your documents")
        search_title.setProperty("role", "sectionTitle")
        search_caption = QLabel(
            "Search by meaning across filenames, categories, and extracted document text. "
            "Smart search stays local on this device."
        )
        search_caption.setProperty("role", "caption")
        search_caption.setWordWrap(True)
        search_copy.addWidget(search_title)
        search_copy.addWidget(search_caption)
        search_header.addLayout(search_copy, 1)

        self.refresh_search_button = QPushButton("Update index")
        self.refresh_search_button.setAccessibleName("Update local search index")
        self.refresh_search_button.clicked.connect(self._request_search_refresh)
        search_header.addWidget(
            self.refresh_search_button,
            0,
            Qt.AlignmentFlag.AlignTop,
        )
        self.search_card.content_layout.addLayout(search_header)

        search_row = QHBoxLayout()
        search_row.setSpacing(SPACING.sm)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Try: documents about train testing…")
        self.search_input.setAccessibleName("Search organized files")
        self.search_input.returnPressed.connect(self._request_search)
        self.search_button = QPushButton("Smart search")
        self.search_button.setProperty("variant", "primary")
        self.search_button.setAccessibleName("Search organized files")
        self.search_button.clicked.connect(self._request_search)
        self.text_search_button = QPushButton("Text search")
        self.text_search_button.setToolTip("Search stored text and file names without Ollama or an AI model.")
        self.text_search_button.clicked.connect(lambda: self._request_search(exact=True))
        self._last_search_results = None
        self.search_type_filter = QComboBox()
        self.search_type_filter.addItems(("All types", ".pdf", ".docx", ".txt", ".xlsx", ".csv", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"))
        self.search_type_filter.setAccessibleName("Search result file type")
        self.search_type_filter.currentIndexChanged.connect(self._refilter_search)
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self.search_type_filter)
        search_row.addWidget(self.search_button)
        search_row.addWidget(self.text_search_button)
        self.search_card.content_layout.addLayout(search_row)

        self.search_status = QLabel(
            "Smart search uses the local semantic index. Update the index after adding or changing files."
        )
        self.search_status.setTextFormat(Qt.TextFormat.PlainText)
        self.search_status.setProperty("role", "caption")
        self.search_status.setWordWrap(True)
        self.search_card.content_layout.addWidget(self.search_status)
        self.search_preview_button = QPushButton("View selected source")
        self.search_preview_button.clicked.connect(self._reveal_search_source)
        self.search_preview_button.hide()
        self.search_card.content_layout.addWidget(self.search_preview_button)

        self.search_results = QListWidget()
        self.search_results.setAccessibleName("Search results")
        self.search_results.setAlternatingRowColors(False)
        self.search_results.setToolTip("Select a result to preview its source. Double-click or press Enter to open the original file.")
        self.search_results.itemActivated.connect(self._open_search_result)
        self.search_results.setMinimumHeight(240)
        self.search_results.setMaximumHeight(560)
        self.search_results.setItemDelegate(SearchResultDelegate(self.search_results))
        self.search_results.setWordWrap(True)
        self.search_results.currentItemChanged.connect(self._select_search_result)
        self.search_body = QSplitter(Qt.Orientation.Horizontal)
        self.search_body.setChildrenCollapsible(False)
        self.search_body.addWidget(self.search_results)
        controller = getattr(self.bridge, "library", None)
        self.search_preview = LibraryPreview(controller, request_key="search_preview") if controller is not None else None
        self.search_source = self.search_preview or SectionCard(elevated=True)
        self.search_source.content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.search_source.setMinimumWidth(240)
        self.search_source_title = self.search_preview.title if self.search_preview else QLabel("Select a result")
        self.search_source_title.setTextFormat(Qt.TextFormat.PlainText)
        if self.search_preview is None:
            self.search_source.content_layout.addWidget(self.search_source_title)
        self.search_evidence_note = QLabel("Select a result to read its indexed excerpt and preview its source.")
        self.search_evidence_note.setWordWrap(True)
        self.search_evidence_note.setProperty("role", "caption")
        self.search_evidence = QTextBrowser()
        self.search_evidence.setAccessibleName("Matching indexed search excerpt")
        self.search_evidence.setOpenLinks(False)
        self.search_evidence.setOpenExternalLinks(False)
        self.search_evidence.setFixedHeight(120)
        self.search_open_button = QPushButton("Open original file")
        self.search_open_button.clicked.connect(lambda: self._open_search_result(self.search_results.currentItem()))
        for index, widget in enumerate((self.search_evidence_note, self.search_evidence, self.search_open_button), start=2 if self.search_preview else 1):
            self.search_source.content_layout.insertWidget(index, widget)
        self.search_source.hide()
        self.search_body.addWidget(self.search_source)
        self.search_body.setStretchFactor(0, 1)
        self.search_body.setStretchFactor(1, 1)
        self.search_body.hide()
        self.search_card.content_layout.addWidget(self.search_body)

        layout.addWidget(self.search_card)

        self.answer_card = SectionCard()
        self.answer_card.setProperty("cardAccent", "purple")
        answer_title = QLabel("Ask your files")
        answer_title.setProperty("role", "sectionTitle")
        self.answer_card.content_layout.addWidget(answer_title)
        self._answer_ai_check_pending = False
        self.answer_scope = QLabel("Ask about a topic across your prepared library. Answers use relevant excerpts, not every file. For one document, use Summarize selected in Document library.")
        self.answer_scope.setWordWrap(True)
        self.answer_scope.setProperty("role", "secondary")
        self.answer_card.content_layout.addWidget(self.answer_scope)
        self.answer_tools = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.answer_prepare = QPushButton("Prepare documents")
        self.answer_prepare.clicked.connect(self._request_search_refresh)
        self.answer_ai_check = QPushButton("Check local AI")
        self.answer_ai_check.setToolTip("Start installed Ollama if needed and check the saved models. No models are downloaded.")
        self.answer_ai_check.clicked.connect(self._check_answer_ai)
        self.answer_text_search = QPushButton("Use text search without AI")
        self.answer_text_search.clicked.connect(self._show_text_search)
        for widget in (self.answer_prepare, self.answer_ai_check, self.answer_text_search):
            self.answer_tools.addWidget(widget)
        self.answer_card.content_layout.addLayout(self.answer_tools)
        self.answer_status = QLabel("Prepare documents first. Questions need local Ollama, an answer model and the smart-search model. Check local AI if an answer fails.")
        self.answer_status.setWordWrap(True)
        self.answer_status.setTextFormat(Qt.TextFormat.PlainText)
        self.answer_status.setProperty("role", "caption")
        self.answer_card.content_layout.addWidget(self.answer_status)
        answer_row = QHBoxLayout()
        self.question_input = QLineEdit()
        self.question_input.setMaxLength(2000)
        self.question_input.setPlaceholderText("What does the report say about traction testing?")
        self.question_input.setAccessibleName("Question about indexed files")
        self.question_input.returnPressed.connect(self._request_answer)
        self.ask_button = QPushButton("Ask")
        self.ask_button.setProperty("variant", "primary")
        self.ask_button.clicked.connect(self._request_answer)
        answer_row.addWidget(self.question_input, 1)
        answer_row.addWidget(self.ask_button)
        self.answer_card.content_layout.addLayout(answer_row)
        self.answer_progress = QProgressBar()
        self.answer_progress.setRange(0, 0)
        self.answer_progress.setAccessibleName("Local AI request in progress")
        self.answer_progress.hide()
        self.answer_card.content_layout.addWidget(self.answer_progress)
        self.answer_text = QPlainTextEdit()
        self.answer_text.setReadOnly(True)
        self.answer_text.setAccessibleName("Answer from indexed files")
        self.answer_text.setFixedHeight(180)
        self.answer_text.hide()
        self.answer_card.content_layout.addWidget(self.answer_text)
        self.answer_sources = QListWidget()
        self.answer_sources.setAccessibleName("Answer sources and excerpts")
        self.answer_sources.setWordWrap(True)
        self.answer_sources.setResizeMode(QListView.ResizeMode.Adjust)
        self.answer_sources.setFixedHeight(140)
        self.answer_sources.currentItemChanged.connect(self._select_answer_source)
        self.answer_sources.itemActivated.connect(self._open_answer_source)
        self.answer_sources.hide()
        self.answer_card.content_layout.addWidget(self.answer_sources)
        self.source_details = QWidget()
        details_layout = QVBoxLayout(self.source_details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        self.source_location = QLabel()
        self.source_location.setWordWrap(True)
        self.source_location.setTextFormat(Qt.TextFormat.PlainText)
        self.source_location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self.source_location)
        self.source_excerpt = QPlainTextEdit()
        self.source_excerpt.setReadOnly(True)
        self.source_excerpt.setAccessibleName("Selected source full excerpt")
        self.source_excerpt.setFixedHeight(160)
        details_layout.addWidget(self.source_excerpt)
        source_actions = QHBoxLayout()
        self.open_source_button = QPushButton("Open source file")
        self.open_source_button.clicked.connect(self._open_answer_source)
        source_actions.addWidget(self.open_source_button)
        self.copy_source_button = QPushButton("Copy excerpt")
        self.copy_source_button.clicked.connect(self._copy_answer_source)
        source_actions.addWidget(self.copy_source_button)
        source_actions.addStretch(1)
        details_layout.addLayout(source_actions)
        self.source_action_status = QLabel()
        self.source_action_status.setWordWrap(True)
        self.source_action_status.setTextFormat(Qt.TextFormat.PlainText)
        details_layout.addWidget(self.source_action_status)
        self.source_details.hide()
        self.answer_card.content_layout.addWidget(self.source_details)
        layout.addWidget(self.answer_card)
        self._pending_question = None
        self._answer_busy()

        self.workspace = QWidget()
        self.workspace_layout = QGridLayout(self.workspace)
        self.workspace_layout.setContentsMargins(0, 0, 0, 0)
        self.workspace_layout.setHorizontalSpacing(SPACING.md)
        self.workspace_layout.setVerticalSpacing(SPACING.md)

        self.source_card = SectionCard()
        self.source_card.setProperty("cardAccent", "warm")
        source_title = QLabel("Choose a file")
        source_title.setProperty("role", "sectionTitle")
        source_caption = QLabel(
            "Drag and drop one file or browse your computer. Nothing is changed during preview."
        )
        source_caption.setProperty("role", "caption")
        source_caption.setWordWrap(True)
        self.source_card.content_layout.addWidget(source_title)
        self.source_card.content_layout.addWidget(source_caption)

        self.drop_zone = FileDropZone()
        self.drop_zone.file_selected.connect(self._select_source)
        self.drop_zone.browse_requested.connect(self._browse)
        self.source_card.content_layout.addWidget(self.drop_zone)

        self.selected_name = QLabel("No file selected")
        self.selected_name.setProperty("role", "body")
        self.selected_path = QLabel("Choose a file to begin.")
        self.selected_path.setProperty("role", "caption")
        self.selected_path.setWordWrap(True)
        self.selected_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.source_card.content_layout.addWidget(self.selected_name)
        self.source_card.content_layout.addWidget(self.selected_path)

        self.preview_button = QPushButton("Preview organization")
        self.preview_button.setProperty("variant", "primary")
        self.preview_button.setAccessibleName("Preview how FilePilot would organize the selected file")
        self.preview_button.setEnabled(False)
        self.preview_button.clicked.connect(self._request_preview)
        self.source_card.content_layout.addWidget(
            self.preview_button,
            0,
            Qt.AlignmentFlag.AlignLeft,
        )

        self.preview_card = SectionCard(elevated=True)
        preview_header = QHBoxLayout()
        preview_copy = QVBoxLayout()
        preview_copy.setSpacing(2)
        preview_title = QLabel("Organization preview")
        preview_title.setProperty("role", "sectionTitle")
        preview_caption = QLabel("Authoritative, non-mutating plan")
        preview_caption.setProperty("role", "caption")
        preview_copy.addWidget(preview_title)
        preview_copy.addWidget(preview_caption)
        preview_header.addLayout(preview_copy)
        preview_header.addStretch(1)
        self.preview_badge = QLabel("Waiting")
        self.preview_badge.setProperty("badgeTone", "neutral")
        preview_header.addWidget(self.preview_badge)
        self.preview_card.content_layout.addLayout(preview_header)

        self.preview_message = QLabel(
            "Select a file, then ask FilePilot to preview where it would go."
        )
        self.preview_message.setProperty("role", "secondary")
        self.preview_message.setWordWrap(True)
        self.preview_card.content_layout.addWidget(self.preview_message)

        self.preview_rows = QVBoxLayout()
        self.preview_rows.setSpacing(SPACING.sm)
        self.category_value = self._preview_row("Category", "—")
        self.destination_value = self._preview_block("Proposed destination", "—")
        self.classification_value = self._preview_row("Classified by", "—")
        self.duplicate_value = self._preview_row("Duplicate check", "—")
        self.safety_value = self._preview_row("Safety", "—")
        self.preview_card.content_layout.addLayout(self.preview_rows)

        self.preview_warning = QLabel("")
        self.preview_warning.setProperty("role", "caption")
        self.preview_warning.setWordWrap(True)
        self.preview_warning.hide()
        self.preview_card.content_layout.addWidget(self.preview_warning)

        action_row = QHBoxLayout()
        self.organize_button = QPushButton("Organize file")
        self.organize_button.setProperty("variant", "primary")
        self.organize_button.setAccessibleName("Organize the selected file using FilePilot safety checks")
        self.organize_button.setEnabled(False)
        self.organize_button.clicked.connect(self._organize_file)
        action_row.addStretch(1)
        action_row.addWidget(self.organize_button)
        self.preview_card.content_layout.addLayout(action_row)
        self.preview_card.content_layout.addStretch(1)

        footer = QLabel(
            "Preview never changes the file. Organize rechecks duplicates, collisions, and safety before moving anything."
        )
        footer.setProperty("role", "caption")
        footer.setWordWrap(True)
        self.preview_card.content_layout.addWidget(footer)

        self.workspace_layout.addWidget(self.source_card, 0, 0)
        self.workspace_layout.addWidget(self.preview_card, 0, 1)
        self.workspace_layout.setColumnStretch(0, 2)
        self.workspace_layout.setColumnStretch(1, 3)
        layout.addWidget(self.workspace, 0, Qt.AlignmentFlag.AlignTop)
        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setAccessibleName("My Files tools")
        for title, widget in (
            ("Organize a file", self.workspace),
            ("Search files", self.search_card),
            ("Ask your files", self.answer_card),
        ):
            layout.removeWidget(widget)
            panel = QWidget()
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(0, SPACING.lg, 0, 0)
            panel_layout.addWidget(widget)
            panel_layout.addStretch(1)
            self.workspace_tabs.addTab(panel, title)
        self.file_browser = FileBrowser(self.bridge)
        self.workspace_tabs.addTab(self.file_browser, "Browse files")
        self.library_workspace = None
        controller = getattr(self.bridge, "library", None)
        if controller is not None:
            self.library_workspace = LibraryWorkspace(controller, self.bridge)
            self.workspace_tabs.addTab(self.library_workspace, "Document library")
            self.library_workspace.search_requested.connect(self._show_library_search)
            self.library_workspace.ask_requested.connect(self._show_library_ask)
            controller.error.connect(self._library_error)
            self.library_workspace.result_ready.connect(self._reveal_document_result)
            controller.result.connect(self._library_result)
            self.file_browser.summarize_requested.connect(self._summarize_browsed_file)
        self.workspace_tabs.setCurrentIndex(3)
        layout.addWidget(self.workspace_tabs)
        layout.addStretch(1)

        answer_signal = getattr(self.bridge, "answer_changed", None)
        if answer_signal is not None:
            answer_signal.connect(self._render_answer)
        answer_failed = getattr(self.bridge, "answer_request_failed", None)
        if answer_failed is not None:
            answer_failed.connect(self._answer_failed)
        search_signal = getattr(self.bridge, "search_results_changed", None)
        if search_signal is not None:
            search_signal.connect(self._render_search_results)
        semantic_search_signal = getattr(
            self.bridge,
            "semantic_search_results_changed",
            None,
        )
        if semantic_search_signal is not None:
            semantic_search_signal.connect(self._render_search_results)
        search_refresh_signal = getattr(
            self.bridge,
            "search_refresh_completed",
            None,
        )
        if search_refresh_signal is not None:
            search_refresh_signal.connect(self._search_refresh_completed)
        search_failed_signal = getattr(self.bridge, "search_request_failed", None)
        if search_failed_signal is not None:
            search_failed_signal.connect(self._search_failed)

        preview_signal = getattr(self.bridge, "preview_changed", None)
        if preview_signal is not None:
            preview_signal.connect(self.render_preview)
        organize_started_signal = getattr(self.bridge, "organize_started", None)
        if organize_started_signal is not None:
            organize_started_signal.connect(self._organize_started)
        organize_completed_signal = getattr(self.bridge, "organize_completed", None)
        if organize_completed_signal is not None:
            organize_completed_signal.connect(self._organize_completed)
        safety_failed_signal = getattr(self.bridge, "safety_request_failed", None)
        if safety_failed_signal is not None:
            safety_failed_signal.connect(self._request_failed)

    def _reveal_document_result(self, heading):
        # Wait for the newly visible result controls to take their final positions.
        def reveal():
            if self.workspace_tabs.currentWidget() is not self.library_workspace:
                return
            self.scroll_area.widget().layout().activate()
            position = heading.mapTo(self.scroll_area.widget(), QPoint(0, 0))
            self.scroll_area.verticalScrollBar().setValue(max(0, position.y() - 24))
        QTimer.singleShot(0, self, reveal)

    def _answer_busy(self):
        busy = self._pending_question is not None or self._answer_ai_check_pending
        self.answer_progress.setVisible(busy)
        self.ask_button.setEnabled(not busy)
        self.question_input.setEnabled(not busy)
        self.answer_prepare.setEnabled(not busy)
        self.answer_ai_check.setEnabled(not busy and getattr(self.bridge, "library", None) is not None)

    def _check_answer_ai(self):
        controller = getattr(self.bridge, "library", None)
        if controller is None or self._pending_question is not None or self._answer_ai_check_pending:
            return
        self._answer_ai_check_pending = True
        self._answer_busy()
        self.answer_status.setText("Checking local Ollama and saved models… no models will be downloaded.")
        controller.request("local_ai")

    def _show_library_ask(self):
        self.workspace_tabs.setCurrentIndex(2)
        self.scroll_area.verticalScrollBar().setValue(0)
        self.question_input.setFocus()

    def _show_text_search(self):
        self._show_library_search()
        self.search_status.setText("Text search works without AI. Enter keywords and choose Text search.")

    def _library_error(self, key, message):
        if key == "local_ai" and self._answer_ai_check_pending:
            self._answer_ai_check_pending = False
            self._answer_busy()
            self.answer_status.setText(message or "Local AI could not be checked. Try again or use text search.")

    def _request_answer(self) -> None:
        self.workspace_tabs.setCurrentIndex(2)
        if self._pending_question is not None or self._answer_ai_check_pending:
            return
        question = self.question_input.text().strip()
        if not question:
            self.answer_status.setText("Enter a question about your indexed files.")
            return
        request = getattr(self.bridge, "request_answer", None)
        if request is None:
            self.answer_status.setText("Local answers are unavailable in this runtime.")
            return
        self._pending_question = question
        self._answer_busy()
        self.answer_text.clear()
        self.answer_text.hide()
        self.answer_sources.clear()
        self.answer_sources.hide()
        self._select_answer_source(None)
        self.answer_status.setText("Reading relevant excerpts and answering on this device… the first answer may take longer while the local model loads.")
        request(question)

    def _render_answer(self, question: str, result) -> None:
        if question != self._pending_question:
            return
        self._pending_question = None
        self._answer_busy()
        self.answer_text.setPlainText(result.answer)
        LibraryWorkspace._reading_direction(self.answer_text, result.answer)
        self.answer_text.show()
        self.answer_sources.clear()
        for source in result.sources:
            preview = " ".join(source.excerpt.split())
            if len(preview) > 160:
                preview = preview[:160] + "…"
            item = QListWidgetItem(
                f"[{source.source_id}] {source.filename} · Chunk {source.chunk_index + 1}\n{preview}"
            )
            item.setToolTip("<qt>" + escape(str(source.path)) + "</qt>")
            item.setData(Qt.ItemDataRole.UserRole, str(source.path))
            item.setData(Qt.ItemDataRole.UserRole + 1, source.excerpt)
            self.answer_sources.addItem(item)
        self.answer_sources.setVisible(bool(result.sources))
        if result.sources:
            self.answer_sources.setCurrentRow(0)
        self.answer_status.setText(
            "No supporting excerpts found. Update the index or try a more specific question."
            if result.status == "no_evidence"
            else f"Answered locally · {len(result.sources)} cited source excerpts"
        )
        self._reveal_answer_result()

    def _reveal_answer_result(self):
        def reveal():
            if self.workspace_tabs.currentIndex() != 2:
                return
            self.scroll_area.widget().layout().activate()
            position = self.answer_status.mapTo(self.scroll_area.widget(), QPoint(0, 0))
            self.scroll_area.verticalScrollBar().setValue(max(0, position.y() - 24))
        QTimer.singleShot(0, self, reveal)

    def _select_answer_source(self, current, previous=None) -> None:
        self.source_action_status.clear()
        self.source_details.setVisible(current is not None)
        self.source_excerpt.clear()
        self.source_location.clear()
        if current is None:
            return
        path = current.data(Qt.ItemDataRole.UserRole)
        self.source_location.setText(str(path))
        self.source_excerpt.setPlainText(current.data(Qt.ItemDataRole.UserRole + 1) or "")
        LibraryWorkspace._reading_direction(self.source_excerpt, self.source_excerpt.toPlainText())

    def _open_answer_source(self, *args) -> None:
        item = self.answer_sources.currentItem()
        if item is None:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        if not path.is_file():
            self.source_action_status.setText("Source file is no longer available at this path. Update the index.")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
            self.source_action_status.setText("Could not open this file. Check the default application for its file type.")
            return
        self.source_action_status.setText("Opened source file in your default application.")

    def _copy_answer_source(self) -> None:
        if self.answer_sources.currentItem() is None:
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.source_excerpt.toPlainText())
        self.source_action_status.setText("Excerpt copied.")

    def _answer_failed(self, message: str) -> None:
        if self._pending_question is None:
            return
        self._pending_question = None
        self._answer_busy()
        self.answer_status.setText(message or "Local answer generation failed. Check Ollama and the index.")

    def _reveal_search_source(self):
        def reveal():
            if self.workspace_tabs.currentIndex() != 1 or self.search_results.currentItem() is None:
                return
            self.scroll_area.widget().layout().activate()
            position = self.search_source_title.mapTo(self.scroll_area.widget(), QPoint(0, 0))
            self.scroll_area.verticalScrollBar().setValue(max(0, position.y() - 24))
        QTimer.singleShot(0, self, reveal)

    def _select_search_result(self, item, previous=None):
        self.search_preview_button.setVisible(item is not None)
        self.search_source.setVisible(item is not None)
        if self.search_preview is not None:
            self.search_preview.select(Path(item.data(Qt.ItemDataRole.UserRole)) if item else None)
        if item is None:
            self.search_evidence.clear()
            return
        result = item.data(RESULT_ROLE)
        path = Path(result.path)
        self.search_source_title.setText(result.filename)
        self.search_source_title.setToolTip("<qt>" + escape(str(path)) + "</qt>")
        self.search_open_button.setEnabled(True)
        query = self._last_search_results[0] if self._last_search_results else ""
        snippet = getattr(result, "snippet", "").strip()
        self.search_evidence.setHtml(highlighted_text(snippet, query) if snippet else
            "No content excerpt is stored for this match. Open the original file to verify it.")
        self.search_evidence_note.setText("Indexed excerpt; the preview shows the current file. " +
            ("PDF preview starts at page 1; this is not an exact page reference." if path.suffix.lower() == ".pdf" else
             "Open the original to verify the complete context."))

    def _open_search_result(self, item):
        if item is None:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        if path.is_file():
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                self.search_status.setText("Windows could not open this file. Choose an application for this file type in Windows.")
        else:
            self.search_status.setText("File unavailable. Update the document library.")

    def _search_busy(self, busy):
        self.search_button.setEnabled(not busy)
        self.text_search_button.setEnabled(not busy)

    def _request_search(self, exact=False) -> None:
        self.workspace_tabs.setCurrentIndex(1)
        query = self.search_input.text().strip()
        if not query:
            self.search_results.clear()
            self.search_body.hide()
            self._last_search_results = None
            self.search_status.setText("Enter a search term to find organized files.")
            return
        request = getattr(self.bridge, "request_search" if exact else "request_semantic_search", None)
        mode = "exact" if exact else "smart"
        if request is None:
            request = getattr(self.bridge, "request_search", None)
            mode = "exact"
        if request is None:
            self.search_status.setText("Local search is unavailable in this runtime.")
            return
        self.search_results.clear()
        self.search_body.hide()
        self._last_search_results = None
        self._search_busy(True)
        self.search_status.setText(
            "Searching by meaning on this device…"
            if mode == "smart"
            else "Searching the local FilePilot index…"
        )
        request(query, 25)

    def _show_library_search(self):
        self.workspace_tabs.setCurrentIndex(1)
        self.scroll_area.verticalScrollBar().setValue(0)
        self.search_input.setFocus()

    def _summarize_browsed_file(self, path):
        if self.library_workspace is not None:
            self.workspace_tabs.setCurrentWidget(self.library_workspace)
            self.library_workspace.summarize_path(path)

    def _library_result(self, key, result):
        if key == "local_ai" and self._answer_ai_check_pending:
            self._answer_ai_check_pending = False
            self._answer_busy()
            if not result.reachable:
                text = (result.error or "Ollama is unavailable.") + " Text search still works without AI."
            else:
                missing = []
                if not result.answer_installed:
                    missing.append("answer model " + result.answer_model)
                if not result.embedding_installed:
                    missing.append("smart-search model " + result.embedding_model)
                text = ("Missing " + ", ".join(missing) + ". Review Settings → AI or install the model in Ollama. Text search remains available."
                    if missing else "Ollama and the saved models were found. Ask a question to verify an actual answer; check its source excerpts.")
            self.answer_status.setText(text)
        if key == "index":
            self._search_refresh_completed(result)

    def _request_search_refresh(self) -> None:
        if self.library_workspace is not None:
            self.workspace_tabs.setCurrentWidget(self.library_workspace)
            self.library_workspace.start_index()
            return
        request = getattr(self.bridge, "request_search_refresh", None)
        if request is None:
            self.search_status.setText("Search indexing is unavailable in this runtime.")
            return
        self.refresh_search_button.setEnabled(False)
        self.search_status.setText("Updating the local search index…")
        request()

    def _render_search_results(self, query: str, results) -> None:
        if query != self.search_input.text().strip():
            self._search_busy(False)
            return
        self._search_busy(False)
        self.search_results.clear()

        raw_results = tuple(results)
        self._last_search_results = (query, raw_results)
        extension = self.search_type_filter.currentText()
        all_results = tuple(item for item in raw_results if self.search_type_filter.currentIndex() == 0
                            or getattr(item, "extension", Path(item.path).suffix).lower() == extension)
        is_semantic = bool(all_results) and hasattr(all_results[0], "score")
        visible_results = (
            all_results[:SMART_RESULTS_VISIBLE_LIMIT]
            if is_semantic
            else all_results
        )

        for result in visible_results:
            category = result.category or "Uncategorized"
            location = self._compact_result_location(result.path)
            detail = getattr(result, "snippet", "").strip()
            excerpt = compact_excerpt(detail) if detail else "No indexed content excerpt; open the original to verify this match."
            item = QListWidgetItem(f"{result.filename}\n{category}  •  {location}\n{excerpt}")
            item.setData(HTML_ROLE, f"<b>{highlighted_text(result.filename, query)}</b><br>" +
                escape(f"{category}  •  {location}") + "<br>" + highlighted_text(excerpt, query))
            item.setData(RESULT_ROLE, result)
            tooltip_parts = [str(result.path)]
            if detail:
                tooltip_parts.append(detail)
            item.setToolTip("<qt>" + escape("\n\n".join(tooltip_parts)).replace("\n", "<br>") + "</qt>")
            item.setData(Qt.ItemDataRole.UserRole, str(result.path))
            self.search_results.addItem(item)

        count = self.search_results.count()
        self.search_body.setVisible(count > 0)
        if count:
            self.search_results.setCurrentRow(0)
        if count and is_semantic:
            total = len(all_results)
            if total > count:
                self.search_status.setText(
                    f"Showing the {count} best matches for “{query}” "
                    f"from {total} semantic candidates."
                )
            else:
                self.search_status.setText(
                    f"{count} best match{'es' if count != 1 else ''} for “{query}”."
                )
        elif count:
            self.search_status.setText(
                f"{count} exact result{'s' if count != 1 else ''} found for “{query}”."
            )
        else:
            self.search_status.setText(
                f"No {extension} results in these matches. Choose All types or try another query."
                if self.search_type_filter.currentIndex() and raw_results else
                f"No indexed files matched “{query}”. Update the index if files changed."
            )

    def _refilter_search(self, *_args):
        if self._last_search_results is not None:
            self._render_search_results(*self._last_search_results)

    @staticmethod
    def _compact_result_location(path: Path) -> str:
        parts = path.parts
        if len(parts) <= 3:
            return str(path)
        return str(Path(*parts[-3:-1]))

    def _search_refresh_completed(self, result) -> None:
        self.refresh_search_button.setEnabled(True)
        semantic_note = ""
        if getattr(result, "semantic_available", False):
            semantic_note = (
                f" Smart index: {result.semantic_embedded} embedded, "
                f"{result.semantic_unchanged} unchanged"
                + (
                    f", {result.semantic_failed} failed."
                    if result.semantic_failed
                    else "."
                )
            )
        else:
            semantic_note = " Smart index unavailable; exact search was still updated."
        self.search_status.setText(
            "Index updated — "
            f"{result.indexed} indexed, "
            f"{result.unchanged} unchanged, "
            f"{result.removed} removed"
            + (f", {result.failed} failed." if result.failed else ".")
            + semantic_note
        )

    def _search_failed(self, name: str, message: str) -> None:
        if name == "semantic_search":
            fallback = getattr(self.bridge, "request_search", None)
            if fallback is not None:
                query = self.search_input.text().strip()
                self.search_status.setText(
                    "Smart search is unavailable. Falling back to exact local search…"
                )
                fallback(query, 25)
                return
            self._search_busy(False)
        elif name == "search":
            self._search_busy(False)
        elif name == "search_refresh":
            self.refresh_search_button.setEnabled(True)
        else:
            return
        self.search_status.setText(
            message or "FilePilot could not complete the local search request."
        )

    def _preview_row(self, label: str, value: str) -> QLabel:
        row = QHBoxLayout()
        name = QLabel(label)
        name.setProperty("role", "secondary")
        value_label = QLabel(value)
        value_label.setProperty("role", "body")
        value_label.setWordWrap(True)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        row.addWidget(name)
        row.addStretch(1)
        row.addWidget(value_label, 2)
        self.preview_rows.addLayout(row)
        return value_label

    def _preview_block(self, label: str, value: str) -> QLabel:
        block = QVBoxLayout()
        block.setSpacing(2)
        name = QLabel(label)
        name.setProperty("role", "secondary")
        value_label = QLabel(value)
        value_label.setProperty("role", "body")
        value_label.setWordWrap(True)
        value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        block.addWidget(name)
        block.addWidget(value_label)
        self.preview_rows.addLayout(block)
        return value_label

    def _browse(self) -> None:
        source, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a file to preview",
            str(self._source.parent if self._source is not None else Path.home()),
        )
        if source:
            self._select_source(source)

    def _select_source(self, source: str) -> None:
        path = Path(source)
        if not path.is_file():
            return
        self._source = path
        self.selected_name.setText(path.name)
        self.selected_path.setText(str(path))
        self.preview_button.setEnabled(True)
        self._preview_pending = False
        self._organize_pending = False
        self.organize_button.setEnabled(False)
        self._reset_preview(
            "Ready to preview",
            "File selected. Preview will inspect the current FilePilot configuration without changing the file.",
        )

    def _request_preview(self) -> None:
        if self._source is None or self._preview_pending:
            return
        self._preview_pending = True
        self.preview_button.setEnabled(False)
        self.organize_button.setEnabled(False)
        self._set_badge("Checking", "info")
        self.preview_message.setText(
            "FilePilot is checking classification, duplicate evidence, destination, and safety."
        )
        request = getattr(self.bridge, "request_preview", None)
        if request is None:
            self._preview_pending = False
            self.preview_button.setEnabled(True)
            self._reset_preview(
                "Unavailable",
                "Organization preview is unavailable in this runtime.",
                "error",
            )
            return
        request(str(self._source))

    def render_preview(self, preview: OperationPreview) -> None:
        if self._source is not None and Path(preview.source) != self._source:
            return
        self._preview_pending = False
        self.preview_button.setEnabled(self._source is not None)

        if preview.state is not SafetyDataState.AVAILABLE:
            self._reset_preview(
                "Unavailable",
                preview.message or "Preview is currently unavailable. Nothing changed.",
                "warning",
            )
            return

        status = preview.status.value if preview.status is not None else "unknown"
        if status == "ready":
            badge, tone = "Ready", "success"
        elif status == "duplicate":
            badge, tone = "Duplicate", "warning"
        elif status in {"unsafe", "hash_failed", "source_missing"}:
            badge, tone = "Needs attention", "error"
        else:
            badge, tone = status.replace("_", " ").title(), "neutral"

        self._set_badge(badge, tone)
        self.preview_message.setText(preview.message or "Preview completed.")
        self.organize_button.setEnabled(
            preview.status is not None
            and preview.status.value == "ready"
            and preview.execution_possible
            and not self._organize_pending
        )
        self.category_value.setText(preview.category or "Unavailable")
        self.destination_value.setText(
            str(preview.proposed_destination)
            if preview.proposed_destination is not None
            else "No destination proposed"
        )
        classification = preview.classification_method or preview.classification_source
        classification_labels = {
            "extension": "Extension rule",
            "fallback": "Fallback rule",
        }
        self.classification_value.setText(
            classification_labels.get(classification, classification or "Unavailable")
        )
        duplicate_labels = {
            "not_found": "No duplicate found",
            "proven": "Verified duplicate",
            "unknown": "Could not verify",
        }
        duplicate_text = duplicate_labels.get(
            preview.duplicate_status.value,
            preview.duplicate_status.value.replace("_", " ").title(),
        )
        if preview.duplicate_of is not None:
            duplicate_text += f" — {preview.duplicate_of}"
        self.duplicate_value.setText(duplicate_text)
        self.safety_value.setText(
            "Preview checks passed"
            if preview.safety_validated
            else "Execution not currently validated"
        )
        warning = preview.warning
        if preview.alternative_name_required:
            warning = (
                (warning + " ") if warning else ""
            ) + "The destination name is occupied; a safe alternative name would be required."
        self.preview_warning.setText(warning or "")
        self.preview_warning.setVisible(bool(warning))

    def _organize_file(self) -> None:
        if self._source is None or self._organize_pending:
            return
        self._organize_pending = True
        self.preview_button.setEnabled(False)
        self.organize_button.setEnabled(False)
        self._set_badge("Revalidating", "info")
        self.preview_message.setText(
            "FilePilot is rechecking current evidence before changing anything."
        )
        request = getattr(self.bridge, "request_organize", None)
        if request is None:
            self._organize_pending = False
            self.preview_button.setEnabled(self._source.is_file())
            self._reset_preview(
                "Unavailable",
                "Manual organization is unavailable in this runtime.",
                "error",
            )
            return
        request(str(self._source))

    def _organize_started(self, source: str) -> None:
        if self._source is None or Path(source) != self._source:
            return
        self._organize_pending = True
        self.preview_button.setEnabled(False)
        self.organize_button.setEnabled(False)

    def _organize_completed(self, result) -> None:
        if self._source is None or Path(result.source) != self._source:
            return
        self._organize_pending = False
        if result.status is MoveStatus.MOVED:
            self._set_badge("Organized", "success")
            self.preview_message.setText("File organized safely. The operation was journaled and can be reviewed in Activity.")
            self.destination_value.setText(str(result.destination) if result.destination is not None else "Moved")
            self.safety_value.setText("Execution completed safely")
            self.preview_button.setEnabled(False)
            self.organize_button.setEnabled(False)
            self.selected_path.setText("Original source moved by FilePilot")
            return
        if result.status is MoveStatus.DUPLICATE:
            self._set_badge("Duplicate", "warning")
            self.preview_message.setText(
                "Execution-time checks found a verified duplicate. FilePilot kept the source file."
            )
            self.duplicate_value.setText(
                f"Verified duplicate — {result.duplicate_of}"
                if result.duplicate_of is not None
                else "Verified duplicate"
            )
        else:
            self._set_badge("Not organized", "error")
            self.preview_message.setText(
                result.error or "FilePilot refused the operation. Nothing unsafe was done."
            )
        self.preview_button.setEnabled(self._source.is_file())
        self.organize_button.setEnabled(False)

    def _request_failed(self, name: str, message: str) -> None:
        if name not in {"preview", "organize"}:
            return
        self._preview_pending = False
        self._organize_pending = False
        self.preview_button.setEnabled(self._source is not None and self._source.is_file())
        self.organize_button.setEnabled(False)
        self._reset_preview(
            "Unavailable",
            message or "Preview could not be completed. Nothing changed.",
            "error",
        )

    def _reset_preview(
        self,
        badge: str,
        message: str,
        tone: str = "neutral",
    ) -> None:
        self._set_badge(badge, tone)
        self.preview_message.setText(message)
        self.category_value.setText("—")
        self.destination_value.setText("—")
        self.classification_value.setText("—")
        self.duplicate_value.setText("—")
        self.safety_value.setText("—")
        self.preview_warning.clear()
        self.preview_warning.hide()
        self.organize_button.setEnabled(False)

    def _set_badge(self, text: str, tone: str) -> None:
        self.preview_badge.setText(text)
        self.preview_badge.setProperty("badgeTone", tone)
        style = self.preview_badge.style()
        style.unpolish(self.preview_badge)
        style.polish(self.preview_badge)

    def resizeEvent(self, event) -> None:
        width = event.size().width()
        self.search_body.setOrientation(Qt.Orientation.Vertical if width < 920 else Qt.Orientation.Horizontal)
        self.search_results.doItemsLayout()
        self.answer_tools.setDirection(QBoxLayout.Direction.TopToBottom if width < 920 else QBoxLayout.Direction.LeftToRight)
        if width < 920:
            self.workspace_layout.addWidget(self.source_card, 0, 0)
            self.workspace_layout.addWidget(self.preview_card, 1, 0)
            self.workspace_layout.setColumnStretch(0, 1)
            self.workspace_layout.setColumnStretch(1, 0)
        else:
            self.workspace_layout.addWidget(self.source_card, 0, 0)
            self.workspace_layout.addWidget(self.preview_card, 0, 1)
            self.workspace_layout.setColumnStretch(0, 2)
            self.workspace_layout.setColumnStretch(1, 3)
        super().resizeEvent(event)
