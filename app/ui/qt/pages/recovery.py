from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application_service import (
    MonitorState,
    RecoveryAction,
    RecoveryActionResult,
    RecoveryActionStatus,
    RecoveryItem,
    RecoverySnapshot,
    SafetyDataState,
)
from app.ui.qt.service_bridge import QtServiceBridge
from app.ui.qt.theme.tokens import SPACING
from app.ui.qt.widgets.section_card import SectionCard


class RecoveryPage(QWidget):
    def __init__(
        self,
        bridge: QtServiceBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._selected_item: RecoveryItem | None = None
        self._action_active = False
        self._page_offset = 0
        self._lifecycle_allows_action = bridge.snapshot.monitor_state in {
            MonitorState.STOPPED,
            MonitorState.BLOCKED,
        }
        self.setObjectName("RecoveryPage")
        self.setProperty("pageSurface", True)
        self.setAccessibleName("Recovery Center")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACING.xl,
            SPACING.xl,
            SPACING.xl,
            SPACING.xl,
        )
        layout.setSpacing(SPACING.lg)

        header = QHBoxLayout()
        heading = QVBoxLayout()
        eyebrow = QLabel("SAFETY")
        eyebrow.setProperty("role", "eyebrow")
        title = QLabel("Recovery Center")
        title.setProperty("role", "pageTitle")
        description = QLabel(
            "Review interrupted operations using FilePilot's verified recovery "
            "evidence. No action here overwrites an existing file."
        )
        description.setProperty("role", "secondary")
        description.setWordWrap(True)
        heading.addWidget(eyebrow)
        heading.addWidget(title)
        heading.addWidget(description)
        header.addLayout(heading, 1)
        self.refresh_button = QPushButton("Refresh evidence")
        self.refresh_button.setAccessibleName("Refresh recovery evidence")
        self.refresh_button.clicked.connect(
            lambda checked=False: self.bridge.request_recovery_refresh(100)
        )
        header.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self.state_card = SectionCard()
        self.state_label = QLabel("Loading recovery evidence...")
        self.state_label.setProperty("role", "secondary")
        self.state_label.setWordWrap(True)
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setMinimumHeight(100)
        self.state_card.content_layout.addWidget(self.state_label)
        layout.addWidget(self.state_card)

        self.table = QTreeWidget()
        self.table.setProperty("activityTable", True)
        self.table.setAccessibleName("Recovery operations")
        self.table.setColumnCount(4)
        self.table.setHeaderLabels(
            ("Operation", "Evidence", "Recommended action", "State")
        )
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.header().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.table.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.header().setSectionResizeMode(
            2,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.table.header().setSectionResizeMode(
            3,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)

        pagination = QHBoxLayout()
        self.inventory_label = QLabel("")
        self.inventory_label.setProperty("role", "caption")
        pagination.addWidget(self.inventory_label)
        pagination.addStretch(1)
        self.previous_button = QPushButton("Previous")
        self.previous_button.setProperty("variant", "quiet")
        self.previous_button.clicked.connect(self._previous_page)
        pagination.addWidget(self.previous_button)
        self.next_button = QPushButton("Next")
        self.next_button.setProperty("variant", "quiet")
        self.next_button.clicked.connect(self._next_page)
        pagination.addWidget(self.next_button)
        layout.addLayout(pagination)

        self.details = SectionCard()
        details_header = QHBoxLayout()
        self.details_title = QLabel("Select an operation")
        self.details_title.setProperty("role", "sectionTitle")
        details_header.addWidget(self.details_title)
        details_header.addStretch(1)
        self.action_button = QPushButton("Review evidence")
        self.action_button.setProperty("variant", "primary")
        self.action_button.setEnabled(False)
        self.action_button.clicked.connect(self._confirm_action)
        details_header.addWidget(self.action_button)
        self.details.content_layout.addLayout(details_header)
        divider = QFrame()
        divider.setProperty("divider", True)
        self.details.content_layout.addWidget(divider)
        self.evidence_label = QLabel(
            "Choose an operation to inspect FilePilot's evidence and recommendation."
        )
        self.evidence_label.setProperty("role", "secondary")
        self.evidence_label.setWordWrap(True)
        self.details.content_layout.addWidget(self.evidence_label)
        self.outcome_label = QLabel("")
        self.outcome_label.setProperty("role", "caption")
        self.outcome_label.setWordWrap(True)
        self.details.content_layout.addWidget(self.outcome_label)
        layout.addWidget(self.details)

        self.bridge.recovery_snapshot_changed.connect(self.render_snapshot)
        self.bridge.recovery_action_started.connect(self._action_started)
        self.bridge.recovery_action_completed.connect(self._action_completed)
        self.bridge.safety_request_failed.connect(self._request_failed)
        self.bridge.state_changed.connect(self._state_changed)
        self.render_snapshot(self.bridge.recovery_snapshot)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.bridge.request_recovery_refresh()

    def render_snapshot(self, snapshot: RecoverySnapshot) -> None:
        self._page_offset = snapshot.offset
        self.table.clear()
        self._selected_item = None
        self._render_details()
        if snapshot.state is SafetyDataState.LOADING:
            self._set_pagination(0, 0, False)
            self._show_state("Loading recovery evidence...")
            return
        if snapshot.state is not SafetyDataState.AVAILABLE:
            self._set_pagination(0, 0, False)
            self._show_state(
                snapshot.error or "Recovery evidence is currently unavailable."
            )
            return
        if not snapshot.items:
            self._set_pagination(snapshot.offset, snapshot.total_items, False)
            if snapshot.total_items:
                self._show_state(
                    "This recovery page is no longer available because the "
                    "inventory changed. Return to the previous page or refresh."
                )
            else:
                self._show_state(
                    "No interrupted operations need attention. FilePilot's recovery "
                    "journal is clear."
                )
            return

        self.state_card.hide()
        self.table.show()
        self._set_pagination(
            snapshot.offset,
            snapshot.total_items,
            snapshot.has_more,
            len(snapshot.items),
        )
        for item in snapshot.items:
            action = (
                item.recommendation
                if item.available_actions
                else (
                    "Manual review only"
                    if item.manual_review_required
                    else "Stop monitoring to enable"
                )
            )
            row = QTreeWidgetItem(
                (
                    item.filename,
                    "; ".join(item.evidence_summary) or item.reason,
                    action,
                    item.status,
                )
            )
            row.setData(0, Qt.ItemDataRole.UserRole, item)
            self.table.addTopLevelItem(row)

    def _show_state(self, message: str) -> None:
        self.table.hide()
        self.state_label.setText(message)
        self.state_card.show()

    def _selection_changed(self) -> None:
        rows = self.table.selectedItems()
        self._selected_item = (
            rows[0].data(0, Qt.ItemDataRole.UserRole) if rows else None
        )
        self.outcome_label.clear()
        self._render_details()

    def _render_details(self) -> None:
        item = self._selected_item
        if item is None:
            self.details_title.setText("Select an operation")
            self.evidence_label.setText(
                "Choose an operation to inspect FilePilot's evidence and "
                "recommendation."
            )
            self.action_button.setText("Review evidence")
            self.action_button.setEnabled(False)
            return

        self.details_title.setText(f"Operation {item.operation_id}")
        copy = item.reason
        copy += f"\n\nSource: {item.source_path}"
        copy += (
            f"\nDestination: {item.destination_path}"
            if item.destination_path is not None
            else "\nDestination: Not recorded"
        )
        if item.evidence_summary:
            copy += "\n\n" + "\n".join(item.evidence_summary)
        if item.manual_review_required:
            copy += (
                "\n\nThe evidence is ambiguous. FilePilot will not change any "
                "files automatically. Inspect the paths outside FilePilot before "
                "taking further action."
            )
        self.evidence_label.setText(copy)
        if item.available_actions:
            self.action_button.setText(item.recommendation)
            self.action_button.setEnabled(
                not self._action_active and self._lifecycle_allows_action
            )
        elif item.manual_review_required:
            self.action_button.setText("Manual review required")
            self.action_button.setEnabled(False)
        else:
            self.action_button.setText("Stop monitoring to enable recovery")
            self.action_button.setEnabled(False)

    def _confirm_action(self) -> None:
        item = self._selected_item
        if (
            item is None
            or not item.available_actions
            or self._action_active
            or not self._lifecycle_allows_action
        ):
            return
        action = item.available_actions[0]
        label = item.recommendation
        answer = QMessageBox.question(
            self,
            "Confirm recovery action",
            f"{label} for operation {item.operation_id}?\n\n"
            "FilePilot will reassess current filesystem evidence immediately "
            "before acting and will refuse unsafe or ambiguous changes.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.bridge.request_recovery_action(item.operation_id, action)

    def _action_started(self, operation_id: str) -> None:
        self._action_active = True
        self.action_button.setEnabled(False)
        self.previous_button.setEnabled(False)
        self.next_button.setEnabled(False)
        self.outcome_label.setText(
            f"Reassessing evidence for {operation_id} before changing files..."
        )

    def _action_completed(self, result: RecoveryActionResult) -> None:
        self._action_active = False
        if result.status is RecoveryActionStatus.RECONCILED:
            self.outcome_label.setText(
                result.message
            )
        elif result.status is RecoveryActionStatus.STILL_NEEDS_REVIEW:
            self.outcome_label.setText(
                result.message
                or "Current evidence is ambiguous; no unsafe action was taken."
            )
        else:
            self.outcome_label.setText(
                result.message or "The recovery action was refused."
            )
        self._render_details()

    def _set_pagination(
        self,
        offset: int,
        total: int,
        has_more: bool,
        page_count: int = 0,
    ) -> None:
        if total:
            first = offset + 1 if page_count else 0
            last = offset + page_count
            self.inventory_label.setText(
                f"Showing {first}-{last} of {total} interrupted operations"
            )
        else:
            self.inventory_label.setText("")
        self.previous_button.setEnabled(offset > 0 and not self._action_active)
        self.next_button.setEnabled(has_more and not self._action_active)

    def _previous_page(self) -> None:
        self.bridge.request_recovery_refresh(100, max(0, self._page_offset - 100))

    def _next_page(self) -> None:
        self.bridge.request_recovery_refresh(100, self._page_offset + 100)

    def _request_failed(self, name: str, message: str) -> None:
        if name not in {"recovery", "recovery_action"}:
            return
        self._action_active = False
        self.outcome_label.setText(message)
        self._render_details()

    def _state_changed(self, snapshot) -> None:
        self._lifecycle_allows_action = snapshot.monitor_state in {
            MonitorState.STOPPED,
            MonitorState.BLOCKED,
        }
        self._render_details()
