from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QBoxLayout, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from app.application_service import ProductDataState, ProductSnapshot, RecoverySnapshot, SafetyDataState, StartupStatus
from app.ui.qt.service_bridge import QtServiceBridge, ServiceSnapshot
from app.ui.qt.theme.tokens import SPACING
from app.ui.qt.widgets.activity_table import ActivityTable
from app.ui.qt.widgets.metric_card import MetricCard
from app.ui.qt.widgets.section_card import SectionCard
from app.ui.qt.widgets.state_panel import StatePanel


class OverviewPage(QWidget):
    view_all_requested = Signal()
    recovery_requested = Signal()
    setup_requested = Signal()
    library_requested = Signal()
    add_documents_requested = Signal()
    document_action_requested = Signal(str)

    def __init__(self, bridge: QtServiceBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.setObjectName("OverviewPage")
        self.setAccessibleName("Overview page")
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("OverviewScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.viewport().setObjectName("OverviewViewport")
        self.scroll_area.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root_layout.addWidget(self.scroll_area)

        self.content = QWidget()
        self.content.setObjectName("OverviewContent")
        self.content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.scroll_area.setWidget(self.content)
        self.page_layout = QVBoxLayout(self.content)
        self.page_layout.setContentsMargins(SPACING.xl, SPACING.xl, SPACING.xl, SPACING.xl)
        self.page_layout.setSpacing(SPACING.lg)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        copy = QVBoxLayout()
        copy.setSpacing(SPACING.xs)
        eyebrow = QLabel("WORKSPACE")
        eyebrow.setProperty("role", "eyebrow")
        self.page_title = QLabel("Your files, under control")
        self.page_title.setProperty("role", "pageTitle")
        subtitle = QLabel("See what FilePilot organized, what needs attention, and what is happening now.")
        subtitle.setProperty("role", "secondary")
        subtitle.setWordWrap(True)
        copy.addWidget(eyebrow)
        copy.addWidget(self.page_title)
        copy.addWidget(subtitle)
        header_layout.addLayout(copy, 1)
        self.header_status = QLabel("Checking status")
        self.header_status.setProperty("badgeTone", "info")
        self.header_status.setAccessibleName("FilePilot status: Checking status")
        header_layout.addWidget(self.header_status, 0, Qt.AlignmentFlag.AlignTop)
        self.page_layout.addWidget(header)

        self._library_snapshot = None
        self._overview_product_snapshot = None
        self._startup_status = None
        self._library_busy = False
        self._document_action = "library"
        self.welcome_card = SectionCard()
        self.welcome_card.setProperty("cardAccent", "purple")
        welcome_title = QLabel("What would you like to do?")
        welcome_title.setProperty("role", "headline")
        self.welcome_card.content_layout.addWidget(welcome_title)
        self.welcome_grid = QGridLayout()
        self.welcome_grid.setContentsMargins(0, 0, 0, 0)
        self.welcome_grid.setHorizontalSpacing(SPACING.xl)
        self.document_entry = QWidget()
        document_layout = QVBoxLayout(self.document_entry)
        document_layout.setContentsMargins(0, 0, 0, 0)
        document_title = QLabel("Find answers in your documents")
        document_title.setProperty("role", "sectionTitle")
        document_title.setWordWrap(True)
        document_copy = QLabel("Choose a folder, prepare its documents, then search or summarize. Your original files stay in place.")
        document_copy.setProperty("role", "secondary")
        document_copy.setWordWrap(True)
        self.document_next = QLabel("Add a document folder to begin. Folder monitoring is not required.")
        self.document_next.setWordWrap(True)
        self.document_next.setTextFormat(Qt.TextFormat.PlainText)
        self.document_next.setAccessibleName("Next step for your document library")
        self.add_documents_button = QPushButton("Add document folder")
        self.add_documents_button.setProperty("variant", "primary")
        self.add_documents_button.clicked.connect(self.add_documents_requested)
        self.library_button = QPushButton("Open document library")
        self.library_button.clicked.connect(self._activate_document_action)
        self.document_actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.document_actions.addWidget(self.add_documents_button)
        self.document_actions.addWidget(self.library_button)
        for widget in (document_title, document_copy, self.document_next):
            document_layout.addWidget(widget)
        document_layout.addLayout(self.document_actions)
        self.organization_entry = QWidget()
        organization_layout = QVBoxLayout(self.organization_entry)
        organization_layout.setContentsMargins(0, 0, 0, 0)
        organization_title = QLabel("Organize files · optional")
        organization_title.setProperty("role", "sectionTitle")
        organization_title.setWordWrap(True)
        organization_copy = QLabel("Choose watched folders and a destination when you want automatic organization. It stays stopped until you choose Start organizing.")
        organization_copy.setProperty("role", "secondary")
        organization_copy.setWordWrap(True)
        self.setup_button = QPushButton("Set up file organization")
        self.setup_button.clicked.connect(self.setup_requested)
        organization_layout.addWidget(organization_title)
        organization_layout.addWidget(organization_copy)
        organization_layout.addStretch(1)
        organization_layout.addWidget(self.setup_button)
        self.welcome_grid.addWidget(self.document_entry, 0, 0)
        self.welcome_grid.addWidget(self.organization_entry, 0, 1)
        self.welcome_card.content_layout.addLayout(self.welcome_grid)
        ai_note = QLabel("Text search works without AI. Summaries need Ollama and a local model; FilePilot checks these when you request an answer.")
        ai_note.setProperty("role", "caption")
        ai_note.setWordWrap(True)
        self.welcome_card.content_layout.addWidget(ai_note)
        self.page_layout.addWidget(self.welcome_card)

        self.metrics_widget = QWidget()
        self.metrics_layout = QGridLayout(self.metrics_widget)
        self.metrics_layout.setContentsMargins(0, 0, 0, 0)
        self.metrics_layout.setHorizontalSpacing(SPACING.md)
        self.metrics_layout.setVerticalSpacing(SPACING.md)
        self.processed_card = MetricCard("Processed", "Loading", "Completed moves")
        self.failed_card = MetricCard("Failed", "Loading", "Journaled failures")
        self.duplicates_card = MetricCard("Duplicates", "Loading", "Verified duplicates")
        self.review_card = MetricCard("Needs review", "Loading", "Requires attention")
        self._metric_cards = (self.processed_card, self.failed_card, self.duplicates_card, self.review_card)
        self._arrange_metrics(4)
        self.page_layout.addWidget(self.metrics_widget)

        self.state_panel = StatePanel()
        self.state_panel.start_requested.connect(self.bridge.request_start)
        self.state_panel.stop_requested.connect(self.bridge.request_stop)
        self.page_layout.addWidget(self.state_panel)

        self.recovery_attention = SectionCard(elevated=True)
        self.recovery_attention.setAccessibleName("Recovery attention")
        attention_layout = QHBoxLayout()
        attention_copy = QVBoxLayout()
        self.recovery_attention_title = QLabel("Recovery needs attention")
        self.recovery_attention_title.setProperty("role", "sectionTitle")
        self.recovery_attention_copy = QLabel("Interrupted operations are waiting for evidence review.")
        self.recovery_attention_copy.setProperty("role", "secondary")
        self.recovery_attention_copy.setWordWrap(True)
        attention_copy.addWidget(self.recovery_attention_title)
        attention_copy.addWidget(self.recovery_attention_copy)
        attention_layout.addLayout(attention_copy, 1)
        self.open_recovery_button = QPushButton("Review recovery")
        self.open_recovery_button.setProperty("variant", "primary")
        self.open_recovery_button.clicked.connect(self.recovery_requested)
        attention_layout.addWidget(self.open_recovery_button)
        self.recovery_attention.content_layout.addLayout(attention_layout)
        self.recovery_attention.hide()
        self.page_layout.addWidget(self.recovery_attention)

        self.recent_activity = SectionCard()
        self.recent_activity.setAccessibleName("Recent activity")
        recent_header = QHBoxLayout()
        recent_copy = QVBoxLayout()
        recent_copy.setSpacing(2)
        recent_heading = QLabel("Recent activity")
        recent_heading.setProperty("role", "sectionTitle")
        recent_caption = QLabel("Latest durable FilePilot operations")
        recent_caption.setProperty("role", "caption")
        recent_copy.addWidget(recent_heading)
        recent_copy.addWidget(recent_caption)
        recent_header.addLayout(recent_copy)
        recent_header.addStretch(1)
        self.view_all_button = QPushButton("View all")
        self.view_all_button.setProperty("variant", "quiet")
        self.view_all_button.setAccessibleName("View all FilePilot activity")
        self.view_all_button.clicked.connect(self.view_all_requested)
        recent_header.addWidget(self.view_all_button)
        self.recent_activity.content_layout.addLayout(recent_header)
        self.recent_state_label = QLabel("Loading authoritative activity...")
        self.recent_state_label.setProperty("role", "secondary")
        self.recent_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recent_state_label.setWordWrap(True)
        self.recent_state_label.setMinimumHeight(80)
        self.recent_activity.content_layout.addWidget(self.recent_state_label)
        self.recent_table = ActivityTable(compact=True)
        self.recent_activity.content_layout.addWidget(self.recent_table)
        self.recent_table.hide()
        self.page_layout.addWidget(self.recent_activity)
        self.page_layout.addStretch(1)

        self.bridge.state_changed.connect(self.render_state)
        self.bridge.product_snapshot_changed.connect(self.render_product_snapshot)
        recovery_signal = getattr(self.bridge, "recovery_snapshot_changed", None)
        if recovery_signal is not None:
            recovery_signal.connect(self.render_recovery_snapshot)
        controller = getattr(self.bridge, "library", None)
        if controller is not None:
            controller.result.connect(self._library_result)
            controller.busy_changed.connect(self._library_busy_changed)
            controller.error.connect(self._library_error)
        self.render_state(self.bridge.snapshot)
        self.render_product_snapshot(self.bridge.product_snapshot)
        recovery_snapshot = getattr(self.bridge, "recovery_snapshot", None)
        if recovery_snapshot is not None:
            self.render_recovery_snapshot(recovery_snapshot)

    def showEvent(self, event):
        super().showEvent(event)
        controller = getattr(self.bridge, "library", None)
        if controller is not None:
            controller.request("status")

    def _library_result(self, key, result):
        if key in {"status", "add", "remove"}:
            self._library_snapshot = result
            self._render_document_next()

    def _library_busy_changed(self, busy):
        self._library_busy = busy
        self._render_document_next()

    def _library_error(self, key, _message):
        if key in {"status", "index", "add", "remove"}:
            self.document_next.setText("The library needs attention. Open Document library to review the issue and retry.")
            self._set_document_action("library", "Review document library")

    def _activate_document_action(self):
        if self._document_action == "library":
            self.library_requested.emit()
        else:
            self.document_action_requested.emit(self._document_action)

    def _set_document_action(self, action, label):
        self._document_action = action
        self.library_button.setText(label)
        self.library_button.setAccessibleName(label)
        has_library = bool(self._library_snapshot and (
            self._library_snapshot.roots or self._library_snapshot.files
            or self._library_snapshot.searchable))
        for button, primary in ((self.library_button, has_library),
                                (self.add_documents_button, not has_library)):
            button.setProperty("variant", "primary" if primary else "secondary")
            button.style().unpolish(button)
            button.style().polish(button)

    def _render_document_next(self):
        catalog = self._library_snapshot
        if self._library_busy:
            text = "Preparing your documents… Open the library to see progress or pause."
            action, label = "library", "View preparation"
        elif catalog is None:
            text = "Add a document folder to begin. Folder monitoring is not required."
            action, label = "library", "Open document library"
        elif catalog.last_run.get("cancelled"):
            text = "Preparation was stopped. Choose Continue preparation; completed work is kept."
            action, label = "prepare", "Continue preparation"
        elif catalog.searchable:
            text = "Your library has indexed text. Choose Search documents, or open Document library in My Files to select a document to summarize."
            action, label = "search", "Search documents"
        elif catalog.files:
            text = "Your documents need a reading check. Choose Review issues to see the files needing attention."
            action, label = "issues", "Review issues"
        elif catalog.roots:
            text = ("No documents were found in the last preparation. Add a folder containing PDF, Office, text or image files, or prepare again after adding files."
                    if catalog.last_run else "Your folder is saved. Choose Prepare documents to read its files.")
            action, label = "prepare", "Prepare documents"
        else:
            text = "Add a document folder to begin. Folder monitoring is not required."
            action, label = "library", "Open document library"
        self.document_next.setText(text)
        self.add_documents_button.setEnabled(not self._library_busy)
        self.add_documents_button.setText("Add another folder" if catalog and (catalog.roots or catalog.files) else "Add document folder")
        self._set_document_action(action, label)

    def _status_row(self, name: str, value: str) -> QLabel:
        row = QHBoxLayout()
        label = QLabel(name)
        label.setProperty("role", "secondary")
        value_label = QLabel(value)
        value_label.setProperty("role", "body")
        value_label.setAccessibleName(f"{name}: {value}")
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(value_label)
        self.system_status.content_layout.addLayout(row)
        return value_label

    def render_state(self, snapshot: ServiceSnapshot) -> None:
        self._startup_status = snapshot.startup_status
        self.state_panel.render(snapshot)
        self._update_operation_visibility()
        if snapshot.startup_status is StartupStatus.BLOCKED:
            header_text, tone = "Action required", "error"
        elif snapshot.startup_status is StartupStatus.SETUP_REQUIRED:
            header_text, tone = "Organization not set up", "info"
        elif snapshot.monitor_state.value == "running":
            header_text, tone = "Monitoring", "success"
        elif snapshot.startup_status is StartupStatus.READY:
            header_text, tone = "Ready", "neutral"
        else:
            header_text, tone = "Checking status", "info"
        self.header_status.setText(header_text)
        self.header_status.setProperty("badgeTone", tone)
        self.header_status.setAccessibleName(f"FilePilot status: {header_text}")
        style = self.header_status.style()
        style.unpolish(self.header_status)
        style.polish(self.header_status)


    def render_product_snapshot(self, snapshot: ProductSnapshot) -> None:
        self._overview_product_snapshot = snapshot
        self._update_operation_visibility()
        if snapshot.state is ProductDataState.AVAILABLE:
            metrics = snapshot.metrics
            self.processed_card.set_value(str(metrics.total_processed), "Completed moves", "success")
            self.failed_card.set_value(str(metrics.failed), "Journaled failures", "error" if metrics.failed else "neutral")
            self.duplicates_card.set_value(str(metrics.duplicates), "Verified duplicates", "warning" if metrics.duplicates else "neutral")
            self.review_card.set_value(str(metrics.needs_review), "Requires attention", "error" if metrics.needs_review else "neutral")
        else:
            value = "Loading" if snapshot.state is ProductDataState.LOADING else "Unavailable"
            detail = snapshot.error or "Waiting for journal data"
            tone = "info" if snapshot.state is ProductDataState.LOADING else "warning"
            for card in self._metric_cards:
                card.set_value(value, detail, tone)

        if snapshot.activity:
            self.recent_state_label.hide()
            self.recent_table.show()
            self.recent_table.set_records(snapshot.activity[:6])
        elif snapshot.state is ProductDataState.LOADING:
            self._show_recent_state("Loading activity...")
        elif snapshot.state in {ProductDataState.UNAVAILABLE, ProductDataState.ERROR}:
            self._show_recent_state(snapshot.error or "Recent activity is currently unavailable.")
        else:
            self._show_recent_state("Nothing here yet. Completed FilePilot operations will appear here.")

    def _update_operation_visibility(self):
        product = self._overview_product_snapshot
        first_use = (self._startup_status is StartupStatus.SETUP_REQUIRED and product is not None
            and (product.state is ProductDataState.LOADING or
                 product.state is ProductDataState.AVAILABLE and not product.activity
                 and product.metrics is not None and not any((product.metrics.total_processed,
                     product.metrics.failed, product.metrics.duplicates, product.metrics.needs_review))))
        # Keep real history and errors visible; omit empty operation counters on first use.
        self.metrics_widget.setVisible(not first_use)
        self.recent_activity.setVisible(not first_use)

    def render_recovery_snapshot(self, snapshot: RecoverySnapshot) -> None:
        if snapshot.state is not SafetyDataState.AVAILABLE or not snapshot.items:
            self.recovery_attention.hide()
            return
        review_count = sum(item.manual_review_required for item in snapshot.items)
        actionable_count = sum(bool(item.available_actions) for item in snapshot.items)
        total = snapshot.total_items
        self.recovery_attention_title.setText(f"{total} interrupted operation" + ("" if total == 1 else "s") + " need attention")
        details = []
        if actionable_count:
            details.append(f"{actionable_count} can use a verified recovery action")
        if review_count:
            details.append(f"{review_count} require manual review")
        if not details:
            details.append("Safe recovery actions become available after monitoring stops")
        self.recovery_attention_copy.setText(". ".join(details) + ". FilePilot will not overwrite existing files.")
        self.recovery_attention.show()

    def _show_recent_state(self, message: str) -> None:
        self.recent_table.hide()
        self.recent_state_label.setText(message)
        self.recent_state_label.show()

    def resizeEvent(self, event) -> None:
        width = event.size().width()
        narrow = width < 820
        self.welcome_grid.addWidget(self.document_entry, 0, 0)
        self.welcome_grid.addWidget(self.organization_entry, 1 if narrow else 0, 0 if narrow else 1)
        self.welcome_grid.setColumnStretch(0, 1)
        self.welcome_grid.setColumnStretch(1, 0 if narrow else 1)
        self.document_actions.setDirection(QBoxLayout.Direction.TopToBottom if width < 580 else QBoxLayout.Direction.LeftToRight)
        columns = 1 if width < 560 else 2 if width < 980 else 4
        self._arrange_metrics(columns)
        margin = SPACING.lg if width < 760 else SPACING.xl
        self.page_layout.setContentsMargins(margin, margin, margin, margin)
        self.page_layout.setSpacing(SPACING.md if width < 760 else SPACING.lg)
        super().resizeEvent(event)

    def _arrange_metrics(self, columns: int) -> None:
        while self.metrics_layout.count():
            item = self.metrics_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(self.metrics_widget)
        for index, card in enumerate(self._metric_cards):
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self.metrics_layout.addWidget(card, index // columns, index % columns)
        for column in range(len(self._metric_cards)):
            self.metrics_layout.setColumnStretch(column, 0)
        for column in range(columns):
            self.metrics_layout.setColumnStretch(column, 1)
