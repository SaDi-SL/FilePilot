from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.application_service import OperationPreview, SafetyDataState
from app.ui.qt.service_bridge import QtServiceBridge
from app.ui.qt.theme.tokens import SPACING
from app.ui.qt.widgets.section_card import SectionCard


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

        icon = QLabel("FILE")
        icon.setProperty("role", "eyebrow")
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

        self.setObjectName("MyFilesPage")
        self.setProperty("pageSurface", True)
        self.setAccessibleName("My Files")

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
        heading.setSpacing(SPACING.xs)
        eyebrow = QLabel("WORKSPACE")
        eyebrow.setProperty("role", "eyebrow")
        title = QLabel("My Files")
        title.setProperty("role", "pageTitle")
        description = QLabel(
            "Choose a file and see exactly how FilePilot would organize it before anything changes."
        )
        description.setProperty("role", "secondary")
        description.setWordWrap(True)
        heading.addWidget(eyebrow)
        heading.addWidget(title)
        heading.addWidget(description)
        header.addLayout(heading, 1)

        preview_only = QLabel("Preview only")
        preview_only.setProperty("badgeTone", "info")
        preview_only.setToolTip("Preview never changes or moves the selected file.")
        header.addWidget(preview_only, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self.workspace = QWidget()
        self.workspace_layout = QGridLayout(self.workspace)
        self.workspace_layout.setContentsMargins(0, 0, 0, 0)
        self.workspace_layout.setHorizontalSpacing(SPACING.md)
        self.workspace_layout.setVerticalSpacing(SPACING.md)

        self.source_card = SectionCard()
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
        self.destination_value = self._preview_row("Destination", "—")
        self.classification_value = self._preview_row("Classified by", "—")
        self.duplicate_value = self._preview_row("Duplicate check", "—")
        self.safety_value = self._preview_row("Safety", "—")
        self.preview_card.content_layout.addLayout(self.preview_rows)

        self.preview_warning = QLabel("")
        self.preview_warning.setProperty("role", "caption")
        self.preview_warning.setWordWrap(True)
        self.preview_warning.hide()
        self.preview_card.content_layout.addWidget(self.preview_warning)
        self.preview_card.content_layout.addStretch(1)

        footer = QLabel(
            "Preview does not move, rename, delete, or overwrite files. Execution will remain separate and revalidate safety."
        )
        footer.setProperty("role", "caption")
        footer.setWordWrap(True)
        self.preview_card.content_layout.addWidget(footer)

        self.workspace_layout.addWidget(self.source_card, 0, 0)
        self.workspace_layout.addWidget(self.preview_card, 0, 1)
        self.workspace_layout.setColumnStretch(0, 2)
        self.workspace_layout.setColumnStretch(1, 3)
        layout.addWidget(self.workspace, 0, Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)

        self.bridge.preview_changed.connect(self.render_preview)
        self.bridge.safety_request_failed.connect(self._request_failed)

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
        self._reset_preview(
            "Ready to preview",
            "File selected. Preview will inspect the current FilePilot configuration without changing the file.",
        )

    def _request_preview(self) -> None:
        if self._source is None or self._preview_pending:
            return
        self._preview_pending = True
        self.preview_button.setEnabled(False)
        self._set_badge("Checking", "info")
        self.preview_message.setText(
            "FilePilot is checking classification, duplicate evidence, destination, and safety."
        )
        self.bridge.request_preview(str(self._source))

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
        self.category_value.setText(preview.category or "Unavailable")
        self.destination_value.setText(
            str(preview.proposed_destination)
            if preview.proposed_destination is not None
            else "No destination proposed"
        )
        classification = preview.classification_method or preview.classification_source
        self.classification_value.setText(classification or "Unavailable")
        duplicate_text = preview.duplicate_status.value.replace("_", " ").title()
        if preview.duplicate_of is not None:
            duplicate_text += f" — {preview.duplicate_of}"
        self.duplicate_value.setText(duplicate_text)
        self.safety_value.setText(
            "Validated for preview"
            if preview.safety_validated
            else "Not validated for execution"
        )
        warning = preview.warning
        if preview.alternative_name_required:
            warning = (
                (warning + " ") if warning else ""
            ) + "The destination name is occupied; a safe alternative name would be required."
        self.preview_warning.setText(warning or "")
        self.preview_warning.setVisible(bool(warning))

    def _request_failed(self, name: str, message: str) -> None:
        if name != "preview":
            return
        self._preview_pending = False
        self.preview_button.setEnabled(self._source is not None)
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

    def _set_badge(self, text: str, tone: str) -> None:
        self.preview_badge.setText(text)
        self.preview_badge.setProperty("badgeTone", tone)
        style = self.preview_badge.style()
        style.unpolish(self.preview_badge)
        style.polish(self.preview_badge)

    def resizeEvent(self, event) -> None:
        width = event.size().width()
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
