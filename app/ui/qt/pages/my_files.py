from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.application_service import MoveStatus, OperationPreview, SafetyDataState
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
        self._organize_pending = False

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
            "Preview where a file will go, then organize it only after FilePilot revalidates safety."
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
        search_header = QHBoxLayout()
        search_copy = QVBoxLayout()
        search_copy.setSpacing(2)
        search_title = QLabel("Search your organized files")
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
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self.search_button)
        self.search_card.content_layout.addLayout(search_row)

        self.search_status = QLabel(
            "Smart search uses the local semantic index. Update the index after adding or changing files."
        )
        self.search_status.setProperty("role", "caption")
        self.search_status.setWordWrap(True)
        self.search_card.content_layout.addWidget(self.search_status)

        self.search_results = QListWidget()
        self.search_results.setAccessibleName("Search results")
        self.search_results.setAlternatingRowColors(False)
        self.search_results.setMinimumHeight(120)
        self.search_results.hide()
        self.search_card.content_layout.addWidget(self.search_results)

        layout.addWidget(self.search_card)

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
        layout.addStretch(1)

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

    def _request_search(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            self.search_results.clear()
            self.search_results.hide()
            self.search_status.setText("Enter a search term to find organized files.")
            return
        request = getattr(self.bridge, "request_semantic_search", None)
        mode = "smart"
        if request is None:
            request = getattr(self.bridge, "request_search", None)
            mode = "exact"
        if request is None:
            self.search_status.setText("Local search is unavailable in this runtime.")
            return
        self.search_button.setEnabled(False)
        self.search_status.setText(
            "Searching by meaning on this device…"
            if mode == "smart"
            else "Searching the local FilePilot index…"
        )
        request(query, 25)

    def _request_search_refresh(self) -> None:
        request = getattr(self.bridge, "request_search_refresh", None)
        if request is None:
            self.search_status.setText("Search indexing is unavailable in this runtime.")
            return
        self.refresh_search_button.setEnabled(False)
        self.search_status.setText("Updating the local search index…")
        request()

    def _render_search_results(self, query: str, results) -> None:
        if query != self.search_input.text().strip():
            return
        self.search_button.setEnabled(True)
        self.search_results.clear()
        for result in results:
            category = result.category or "Uncategorized"
            item = QListWidgetItem(
                f"{result.filename}\n{category}  •  {result.path}"
            )
            detail = getattr(result, "snippet", "").strip()
            if detail:
                item.setToolTip(detail)
            item.setData(Qt.ItemDataRole.UserRole, str(result.path))
            self.search_results.addItem(item)
        count = self.search_results.count()
        self.search_results.setVisible(count > 0)
        if count:
            self.search_status.setText(
                f"{count} smart result{'s' if count != 1 else ''} found for “{query}”."
            )
        else:
            self.search_status.setText(
                f"No indexed files matched “{query}”. Update the index if files changed."
            )

    def _search_refresh_completed(self, result) -> None:
        self.refresh_search_button.setEnabled(True)
        self.search_status.setText(
            "Index updated — "
            f"{result.indexed} indexed, "
            f"{result.unchanged} unchanged, "
            f"{result.removed} removed"
            + (f", {result.failed} failed." if result.failed else ".")
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
            self.search_button.setEnabled(True)
        elif name == "search":
            self.search_button.setEnabled(True)
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
