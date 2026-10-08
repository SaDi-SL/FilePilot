from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.application_service import ActivityStatus, ProductDataState, ProductSnapshot
from app.ui.qt.safety_dialogs import OperationDetailsDialog, PreviewDialog
from app.ui.qt.service_bridge import QtServiceBridge
from app.ui.qt.theme.tokens import SPACING
from app.ui.qt.widgets.activity_table import ActivityTable


class ActivityPage(QWidget):
    FILTERS = (
        ("All activity", None),
        ("Completed", ActivityStatus.COMPLETED),
        ("Duplicates", ActivityStatus.DUPLICATE),
        ("Failed", ActivityStatus.FAILED),
        ("Needs review", ActivityStatus.NEEDS_REVIEW),
    )

    def __init__(
        self,
        bridge: QtServiceBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._snapshot = bridge.product_snapshot
        self._details_dialog: OperationDetailsDialog | None = None
        self._preview_dialog: PreviewDialog | None = None
        self.setObjectName("ActivityPage")
        self.setProperty("pageSurface", True)
        self.setAccessibleName("Activity page")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACING.xl,
            SPACING.xl,
            SPACING.xl,
            SPACING.xl,
        )
        layout.setSpacing(SPACING.lg)

        eyebrow = QLabel("FILEPILOT / OPERATIONS")
        eyebrow.setProperty("role", "eyebrow")
        title = QLabel("Activity")
        title.setProperty("role", "pageTitle")
        description = QLabel(
            "Track organized files, duplicates and files that need your attention."
        )
        description.setProperty("role", "secondary")
        description.setWordWrap(True)
        layout.addWidget(eyebrow)
        layout.addWidget(title)
        layout.addWidget(description)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(SPACING.sm)
        self.summary_label = QLabel("Loading activity")
        self.summary_label.setProperty("role", "secondary")
        self.summary_label.setWordWrap(True)
        summary_row = QHBoxLayout()
        summary_row.addWidget(self.summary_label, 1)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search files or categories")
        self.search_input.setAccessibleName("Search loaded activity")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(140)
        self.search_input.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.search_input, 1)
        self.preview_button = QPushButton("Preview a file")
        self.preview_button.setAccessibleName("Preview a file before moving it")
        self.preview_button.clicked.connect(self._open_preview)
        summary_row.addWidget(self.preview_button)
        layout.addLayout(summary_row)
        self.filter_combo = QComboBox()
        self.filter_combo.setAccessibleName("Filter activity by result")
        for label, status in self.FILTERS:
            self.filter_combo.addItem(label, status.value if status else None)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        toolbar.addWidget(self.filter_combo)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setAccessibleName("Refresh authoritative activity")
        self.refresh_button.clicked.connect(
            lambda checked=False: self.bridge.request_product_refresh(100)
        )
        toolbar.addWidget(self.refresh_button)
        layout.addLayout(toolbar)

        self.state_label = QLabel()
        self.state_label.setProperty("role", "secondary")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setWordWrap(True)
        self.state_label.setMinimumHeight(180)
        self.state_label.setAccessibleName("Activity availability")
        layout.addWidget(self.state_label, 1)

        self.table = ActivityTable()
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(
            lambda item, column: self._open_details()
        )
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        selection_hint = QLabel("Select an operation to view details. Double-click to open it.")
        selection_hint.setProperty("role", "caption")
        selection_hint.setWordWrap(True)
        actions.addWidget(selection_hint, 1)
        actions.addStretch(1)
        self.details_button = QPushButton("View operation details")
        self.details_button.setProperty("variant", "primary")
        self.details_button.setEnabled(False)
        self.details_button.clicked.connect(self._open_details)
        actions.addWidget(self.details_button)
        layout.addLayout(actions)

        self.bridge.product_snapshot_changed.connect(self.render_snapshot)
        self.render_snapshot(self._snapshot)

    def _selection_changed(self) -> None:
        self.details_button.setEnabled(self.table.selected_record() is not None)

    def _open_details(self) -> None:
        record = self.table.selected_record()
        if record is None:
            return
        self._details_dialog = OperationDetailsDialog(self.bridge, record, self)
        self._details_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._details_dialog.open()

    def _open_preview(self) -> None:
        self._preview_dialog = PreviewDialog(self.bridge, self)
        self._preview_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._preview_dialog.open()

    def render_snapshot(self, snapshot: ProductSnapshot) -> None:
        self._snapshot = snapshot
        self._apply_filter()

    def _apply_filter(self, _index: int | None = None) -> None:
        snapshot = self._snapshot
        selected = self.filter_combo.currentData()
        query = self.search_input.text().strip().casefold()
        records = tuple(
            record
            for record in snapshot.activity
            if (selected is None or record.status.value == selected)
            and (not query or query in f"{record.filename} {record.category or ''} {record.display_destination or ''}".casefold())
        )
        if records:
            self.state_label.hide()
            self.table.show()
            self.table.set_records(records)
            self._selection_changed()
            if snapshot.state is not ProductDataState.AVAILABLE:
                qualifier = (
                    "durable history loading"
                    if snapshot.state is ProductDataState.LOADING
                    else "durable history unavailable"
                )
                self.summary_label.setText(
                    f"Showing {len(records)} live operations; {qualifier}"
                )
            elif snapshot.has_more and (selected is not None or query):
                self.summary_label.setText(
                    f"Showing {len(records)} matches in the latest "
                    f"{len(snapshot.activity)}+ operations"
                )
            else:
                summary = f"Showing {len(records)} operations"
                if snapshot.has_more:
                    summary += "; more activity is available"
                self.summary_label.setText(summary)
            return
        if snapshot.state is ProductDataState.LOADING:
            self._show_state("Loading authoritative activity...")
            self.summary_label.setText("Loading activity")
            return
        if snapshot.state in {ProductDataState.UNAVAILABLE, ProductDataState.ERROR}:
            self._show_state(
                snapshot.error or "Authoritative activity is currently unavailable."
            )
            self.summary_label.setText("Activity unavailable")
            return
        if not records:
            if selected is None and not query:
                message = "No activity yet. Organized files will appear here."
            elif snapshot.has_more:
                message = (
                    "No matching operations are present in the latest "
                    f"{len(snapshot.activity)} records. Older activity was not searched."
                )
            else:
                message = "No operations match your search or filter."
            self._show_state(message)
            self.summary_label.setText(
                "0 shown (bounded history)" if snapshot.has_more else "0 operations"
            )
            return

    def _show_state(self, message: str) -> None:
        self.table.hide()
        self.details_button.setEnabled(False)
        self.state_label.setText(message)
        self.state_label.show()
