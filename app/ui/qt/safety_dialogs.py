from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.application_service import (
    ActivityRecord,
    MonitorState,
    OperationPreview,
    PreviewStatus,
    SafetyDataState,
    UndoAvailability,
    UndoResult,
    UndoStatus,
)
from app.ui.qt.activity_presentation import activity_status_presentation
from app.ui.qt.service_bridge import QtServiceBridge
from app.ui.qt.theme.tokens import SPACING


def _display_path(path: str | None) -> str:
    return path or "Not available"


class OperationDetailsDialog(QDialog):
    def __init__(
        self,
        bridge: QtServiceBridge,
        record: ActivityRecord,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.record = record
        self._availability: UndoAvailability | None = None
        self._undo_active = False
        self._lifecycle_allows_undo = (
            bridge.snapshot.monitor_state is MonitorState.STOPPED
        )
        self.setWindowTitle("Operation details")
        self.setAccessibleName("Operation details")
        self.setModal(True)
        self.setMinimumWidth(660)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACING.xl,
            SPACING.xl,
            SPACING.xl,
            SPACING.xl,
        )
        layout.setSpacing(SPACING.lg)

        presentation = activity_status_presentation(record.status)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        title = QLabel(record.filename or "File operation")
        title.setProperty("role", "headline")
        subtitle = QLabel(presentation.label)
        subtitle.setProperty("badgeTone", presentation.tone)
        subtitle.setSizePolicy(
            subtitle.sizePolicy().horizontalPolicy(),
            subtitle.sizePolicy().verticalPolicy(),
        )
        heading.addWidget(title)
        heading.addWidget(subtitle, 0, Qt.AlignmentFlag.AlignLeft)
        header.addLayout(heading, 1)
        layout.addLayout(header)

        facts = QGridLayout()
        facts.setHorizontalSpacing(SPACING.lg)
        facts.setVerticalSpacing(SPACING.md)
        self._add_fact(facts, 0, "Source", str(record.source_path))
        destination = (
            str(record.display_destination)
            if record.display_destination is not None
            else None
        )
        self._add_fact(facts, 1, "Destination", _display_path(destination))
        self._add_fact(facts, 2, "Category", record.category or "Not available")
        timestamp = (
            record.occurred_at_utc.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            if record.occurred_at_utc is not None
            else "Not available"
        )
        self._add_fact(facts, 3, "Time", timestamp)
        self._add_fact(
            facts,
            4,
            "Operation ID",
            record.operation_id or "Not journaled",
        )
        layout.addLayout(facts)

        divider = QFrame()
        divider.setProperty("divider", True)
        layout.addWidget(divider)

        evidence_title = QLabel("Safety evidence")
        evidence_title.setProperty("role", "sectionTitle")
        layout.addWidget(evidence_title)
        evidence = []
        if record.error:
            evidence.append(f"Error: {record.error}")
        if record.metadata_warning:
            evidence.append(f"Metadata warning: {record.metadata_warning}")
        if record.recovery_state:
            evidence.append(
                "Recovery state: " + record.recovery_state.replace("_", " ").title()
            )
        if record.classification_method:
            evidence.append(
                "Classification method: "
                + record.classification_method.replace("_", " ").title()
            )
        if record.classification_source:
            evidence.append(f"Classification source: {record.classification_source}")
        if not record.durable:
            evidence.append("Durable journal confirmation is still pending.")
        self.evidence_label = QLabel(
            "\n".join(evidence)
            or "The durable operation record contains no warnings or errors."
        )
        self.evidence_label.setProperty("role", "secondary")
        self.evidence_label.setWordWrap(True)
        self.evidence_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.evidence_label)

        undo_title = QLabel("Undo")
        undo_title.setProperty("role", "sectionTitle")
        layout.addWidget(undo_title)
        self.undo_explanation = QLabel(
            "Checking the operation journal and current filesystem evidence..."
        )
        self.undo_explanation.setProperty("role", "secondary")
        self.undo_explanation.setWordWrap(True)
        layout.addWidget(self.undo_explanation)

        actions = QHBoxLayout()
        self.undo_button = QPushButton("Checking Undo...")
        self.undo_button.setProperty("variant", "destructive")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self._confirm_undo)
        actions.addWidget(self.undo_button)
        actions.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        actions.addWidget(close_button)
        layout.addLayout(actions)

        self.bridge.undo_availability_changed.connect(self._availability_changed)
        self.bridge.undo_started.connect(self._undo_started)
        self.bridge.undo_completed.connect(self._undo_completed)
        self.bridge.safety_request_failed.connect(self._request_failed)
        self.bridge.state_changed.connect(self._state_changed)

        if record.operation_id:
            self.bridge.request_undo_availability(record.operation_id)
        else:
            self.undo_button.setText("Undo unavailable")
            self.undo_explanation.setText(
                "This activity has no durable operation identifier, so it cannot "
                "be evaluated for Undo."
            )

    @staticmethod
    def _add_fact(layout: QGridLayout, row: int, name: str, value: str) -> None:
        label = QLabel(name)
        label.setProperty("role", "caption")
        content = QLabel(value)
        content.setProperty("role", "body")
        content.setWordWrap(True)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(content, row, 1)
        layout.setColumnStretch(1, 1)

    def _availability_changed(self, availability: UndoAvailability) -> None:
        if availability.operation_id != self.record.operation_id:
            return
        self._availability = availability
        self.undo_explanation.setText(availability.reason)
        if availability.eligible:
            self.undo_button.setText("Undo this move")
            self.undo_button.setEnabled(
                not self._undo_active and self._lifecycle_allows_undo
            )
        else:
            self.undo_button.setText("Undo unavailable")
            self.undo_button.setEnabled(False)

    def _confirm_undo(self) -> None:
        availability = self._availability
        if (
            availability is None
            or not availability.eligible
            or self._undo_active
            or not self._lifecycle_allows_undo
        ):
            return
        answer = QMessageBox.question(
            self,
            "Confirm Undo",
            "Move this file back to its original source path?\n\n"
            f"From: {_display_path(str(availability.current_path) if availability.current_path else None)}\n"
            f"To: {_display_path(str(availability.restore_path) if availability.restore_path else None)}\n\n"
            "FilePilot will recheck file identity and both paths immediately "
            "before acting. It will never overwrite an existing file.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.bridge.request_undo(availability.operation_id)

    def _undo_started(self, operation_id: str) -> None:
        if operation_id != self.record.operation_id:
            return
        self._undo_active = True
        self.undo_button.setText("Undoing safely...")
        self.undo_button.setEnabled(False)
        self.undo_explanation.setText(
            "FilePilot is revalidating the operation and current filesystem "
            "state before restoring the original path."
        )

    def _undo_completed(self, result: UndoResult) -> None:
        if result.original_operation_id != self.record.operation_id:
            return
        self._undo_active = False
        if result.status is UndoStatus.SUCCESS:
            self.undo_button.setText("Move restored")
            self.undo_button.setEnabled(False)
            self.undo_explanation.setText(
                result.reason or "The file was restored to its original path."
            )
        elif result.status is UndoStatus.ALREADY_UNDONE:
            self.undo_button.setText("Already undone")
            self.undo_button.setEnabled(False)
            self.undo_explanation.setText(result.reason or "No further action is needed.")
        else:
            self.undo_button.setText("Undo unavailable")
            self.undo_button.setEnabled(False)
            self.undo_explanation.setText(
                result.reason
                or "Undo was refused because current evidence was not safe."
            )

    def _request_failed(self, name: str, message: str) -> None:
        if name not in {"undo", "undo_availability"}:
            return
        self._undo_active = False
        self.undo_button.setText("Undo unavailable")
        self.undo_button.setEnabled(False)
        self.undo_explanation.setText(message)

    def _state_changed(self, snapshot) -> None:
        self._lifecycle_allows_undo = (
            snapshot.monitor_state is MonitorState.STOPPED
        )
        if self._undo_active:
            return
        if not self._lifecycle_allows_undo:
            self.undo_button.setText("Stop monitoring to use Undo")
            self.undo_button.setEnabled(False)
            return
        if self._availability is not None:
            self._availability_changed(self._availability)


class PreviewDialog(QDialog):
    def __init__(
        self,
        bridge: QtServiceBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.setWindowTitle("Preview a file")
        self.setAccessibleName("Preview a FilePilot move")
        self.setModal(True)
        self.setMinimumWidth(680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACING.xl,
            SPACING.xl,
            SPACING.xl,
            SPACING.xl,
        )
        layout.setSpacing(SPACING.lg)

        title = QLabel("Preview before FilePilot moves a file")
        title.setProperty("role", "headline")
        copy = QLabel(
            "Preview applies configured extension rules and FilePilot's destination "
            "planner. It does not create folders, update metadata, or change files."
        )
        copy.setProperty("role", "secondary")
        copy.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(copy)

        path_row = QHBoxLayout()
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Choose a file to preview")
        self.path_input.setAccessibleName("File to preview")
        self.path_input.returnPressed.connect(self._request_preview)
        path_row.addWidget(self.path_input, 1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        path_row.addWidget(browse)
        self.preview_button = QPushButton("Preview")
        self.preview_button.setProperty("variant", "primary")
        self.preview_button.clicked.connect(self._request_preview)
        path_row.addWidget(self.preview_button)
        layout.addLayout(path_row)

        divider = QFrame()
        divider.setProperty("divider", True)
        layout.addWidget(divider)

        self.result_title = QLabel("No preview yet")
        self.result_title.setProperty("role", "sectionTitle")
        self.result_copy = QLabel(
            "Select a file to see its category, planned destination, and "
            "duplicate or collision outcome."
        )
        self.result_copy.setProperty("role", "secondary")
        self.result_copy.setWordWrap(True)
        self.result_copy.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.result_title)
        layout.addWidget(self.result_copy)

        footer = QHBoxLayout()
        self.non_mutating_label = QLabel("Preview only: no files will be changed")
        self.non_mutating_label.setProperty("badgeTone", "success")
        footer.addWidget(self.non_mutating_label)
        footer.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)

        self.bridge.preview_changed.connect(self._preview_changed)
        self.bridge.safety_request_failed.connect(self._request_failed)

    def _browse(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a file to preview",
            str(Path.home()),
        )
        if selected:
            self.path_input.setText(selected)
            self._request_preview()

    def _request_preview(self) -> None:
        source = self.path_input.text().strip()
        if not source:
            self.result_title.setText("Choose a file")
            self.result_copy.setText("Enter or browse to a file before previewing.")
            return
        self.preview_button.setEnabled(False)
        self.preview_button.setText("Previewing...")
        self.result_title.setText("Evaluating safely")
        self.result_copy.setText(
            "FilePilot is reading classification and destination evidence. "
            "Nothing is being changed."
        )
        self.bridge.request_preview(source)

    def _preview_changed(self, preview: OperationPreview) -> None:
        self.preview_button.setEnabled(True)
        self.preview_button.setText("Preview")
        if preview.state is not SafetyDataState.AVAILABLE:
            self.result_title.setText("Preview unavailable")
            self.result_copy.setText(
                preview.message or "FilePilot could not calculate a safe preview."
            )
            return

        status = preview.status
        if status is PreviewStatus.READY and not preview.destination_collision:
            self.result_title.setText("Ready for a normal move")
        elif status is PreviewStatus.READY and preview.destination_collision:
            self.result_title.setText("Name collision handled safely")
        elif status is PreviewStatus.DUPLICATE:
            self.result_title.setText("Verified duplicate detected")
        else:
            self.result_title.setText("Preview could not plan a move")

        lines = [f"Source: {_display_path(preview.source)}"]
        if preview.category:
            lines.append(f"Category: {preview.category}")
        if preview.classification_source:
            lines.append(
                "Classification: "
                + preview.classification_source.replace("_", " ").title()
            )
        if preview.proposed_destination:
            lines.append(f"Planned destination: {preview.proposed_destination}")
        if preview.duplicate_of:
            lines.append(
                f"Existing verified duplicate: {preview.duplicate_of}"
            )
        if preview.alternative_name_required:
            lines.append(
                "A collision-safe alternative filename would be used."
            )
        lines.append(preview.message)
        if preview.warning:
            lines.append(f"Warning: {preview.warning}")
        lines.append("No folders, metadata, or files were changed by this preview.")
        self.result_copy.setText("\n\n".join(lines))

    def _request_failed(self, name: str, message: str) -> None:
        if name != "preview":
            return
        self.preview_button.setEnabled(True)
        self.preview_button.setText("Preview")
        self.result_title.setText("Preview unavailable")
        self.result_copy.setText(message)
