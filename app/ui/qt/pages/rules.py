from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application_service import (
    ConfigurationDataState,
    ConfigurationSaveStatus,
    MonitorState,
    ProductConfigurationCandidate,
    ProductConfigurationSnapshot,
    ProductRule,
    StartupStatus,
)
from app.ui.qt.service_bridge import QtServiceBridge, ServiceSnapshot
from app.ui.qt.theme.tokens import SPACING
from app.ui.qt.widgets.section_card import SectionCard


class RulesPage(QWidget):
    CONTEXT = "rules"

    def __init__(
        self,
        bridge: QtServiceBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._baseline = ProductConfigurationSnapshot.loading()
        self._loading = False
        self._dirty = False
        self._saving = False
        self._validation_valid = False
        self._edit_generation = 0
        self._service_snapshot = bridge.snapshot
        self.setObjectName("RulesPage")
        self.setProperty("pageSurface", True)
        self.setAccessibleName("Rules page")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("RulesScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        root.addWidget(self.scroll_area)

        self.content = QWidget()
        self.content.setObjectName("RulesContent")
        self.content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.scroll_area.setWidget(self.content)
        self.page_layout = QVBoxLayout(self.content)
        self.page_layout.setContentsMargins(
            SPACING.xl,
            SPACING.xl,
            SPACING.xl,
            SPACING.xl,
        )
        self.page_layout.setSpacing(SPACING.lg)

        eyebrow = QLabel("FILEPILOT / AUTOMATION")
        eyebrow.setProperty("role", "eyebrow")
        title = QLabel("Rules")
        title.setProperty("role", "pageTitle")
        description = QLabel(
            "Map file extensions to destination categories. Changes are checked "
            "against the complete configuration before FilePilot saves them."
        )
        description.setProperty("role", "secondary")
        description.setWordWrap(True)
        self.page_layout.addWidget(eyebrow)
        self.page_layout.addWidget(title)
        self.page_layout.addWidget(description)

        editor_card = SectionCard(elevated=True)
        header = QVBoxLayout()
        header.setSpacing(SPACING.sm)
        heading = QLabel("Classification map")
        heading.setProperty("role", "sectionTitle")
        header.addWidget(heading)
        editor_actions = QHBoxLayout()
        editor_actions.addStretch(1)
        self.add_button = QPushButton("Add category")
        self.add_button.setAccessibleName("Add a classification category")
        self.add_button.clicked.connect(self._add_rule)
        editor_actions.addWidget(self.add_button)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.setAccessibleName("Remove selected classification category")
        self.remove_button.clicked.connect(self._remove_rule)
        editor_actions.addWidget(self.remove_button)
        header.addLayout(editor_actions)
        editor_card.content_layout.addLayout(header)

        hint = QLabel(
            "Enter extensions separated by commas, for example: .pdf, PDF, *.docx. "
            "FilePilot normalizes them to lowercase dot-prefixed values. Unmatched "
            "files always use the Others fallback."
        )
        hint.setProperty("role", "secondary")
        hint.setWordWrap(True)
        editor_card.content_layout.addWidget(hint)

        self.rule_tree = QTreeWidget()
        self.rule_tree.setProperty("activityTable", True)
        self.rule_tree.setAccessibleName("Classification rules editor")
        self.rule_tree.setColumnCount(2)
        self.rule_tree.setHeaderLabels(("Category", "Extensions"))
        self.rule_tree.header().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.rule_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.rule_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.rule_tree.setMinimumHeight(150)
        self.rule_tree.setMaximumHeight(170)
        self.rule_tree.itemChanged.connect(self._editor_changed)
        self.rule_tree.currentItemChanged.connect(self._load_selected_rule)
        editor_card.content_layout.addWidget(self.rule_tree)

        detail_heading = QLabel("Selected category")
        detail_heading.setProperty("role", "sectionTitle")
        editor_card.content_layout.addWidget(detail_heading)
        self.category_input = QLineEdit()
        self.category_input.setAccessibleName("Selected category name")
        self.category_input.setPlaceholderText("Category name")
        self.category_input.textChanged.connect(self._category_changed)
        editor_card.content_layout.addWidget(self.category_input)

        self.extension_list = QListWidget()
        self.extension_list.setAccessibleName("Extensions in selected category")
        self.extension_list.setMinimumHeight(70)
        self.extension_list.setMaximumHeight(80)
        editor_card.content_layout.addWidget(self.extension_list)
        extension_actions = QHBoxLayout()
        self.extension_input = QLineEdit()
        self.extension_input.setAccessibleName("Extension to add")
        self.extension_input.setPlaceholderText(".pdf, PDF, or *.pdf")
        self.extension_input.returnPressed.connect(self._add_extension)
        extension_actions.addWidget(self.extension_input, 1)
        self.add_extension_button = QPushButton("Add extension")
        self.add_extension_button.clicked.connect(self._add_extension)
        extension_actions.addWidget(self.add_extension_button)
        self.remove_extension_button = QPushButton("Remove extension")
        self.remove_extension_button.clicked.connect(self._remove_extension)
        extension_actions.addWidget(self.remove_extension_button)
        editor_card.content_layout.addLayout(extension_actions)
        self.page_layout.addWidget(editor_card)

        preview_card = SectionCard()
        preview_heading = QLabel("Try an unsaved rule")
        preview_heading.setProperty("role", "sectionTitle")
        preview_copy = QLabel(
            "Check the extension-rule outcome for a filename. No file is moved and "
            "the current configuration is not changed."
        )
        preview_copy.setProperty("role", "secondary")
        preview_copy.setWordWrap(True)
        preview_row = QHBoxLayout()
        self.preview_input = QLineEdit()
        self.preview_input.setPlaceholderText("quarterly-report.PDF")
        self.preview_input.setAccessibleName("Filename for unsaved rule preview")
        self.preview_input.returnPressed.connect(self._request_preview)
        preview_row.addWidget(self.preview_input, 1)
        self.preview_button = QPushButton("Check outcome")
        self.preview_button.clicked.connect(self._request_preview)
        preview_row.addWidget(self.preview_button)
        self.preview_result = QLabel("Enter a filename to preview its category.")
        self.preview_result.setProperty("role", "secondary")
        self.preview_result.setWordWrap(True)
        preview_card.content_layout.addWidget(preview_heading)
        preview_card.content_layout.addWidget(preview_copy)
        preview_card.content_layout.addLayout(preview_row)
        preview_card.content_layout.addWidget(self.preview_result)
        self.page_layout.addWidget(preview_card)

        self.feedback_label = QLabel("Loading authoritative configuration...")
        self.feedback_label.setProperty("badgeTone", "info")
        self.feedback_label.setWordWrap(True)
        self.feedback_label.setAccessibleName("Rules validation status")
        self.page_layout.addWidget(self.feedback_label)

        actions = QHBoxLayout()
        self.unsaved_label = QLabel("All changes saved")
        self.unsaved_label.setProperty("badgeTone", "success")
        self.unsaved_label.setAccessibleName("Rules unsaved changes status")
        actions.addWidget(self.unsaved_label)
        self.revision_label = QLabel("Revision unavailable")
        self.revision_label.setProperty("role", "secondary")
        actions.addWidget(self.revision_label)
        actions.addStretch(1)
        self.reload_button = QPushButton("Revert changes")
        self.reload_button.setAccessibleName("Revert unsaved rule changes")
        self.reload_button.clicked.connect(self._reload)
        actions.addWidget(self.reload_button)
        self.save_button = QPushButton("Save rules")
        self.save_button.setProperty("variant", "primary")
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)
        self.page_layout.addLayout(actions)
        self.page_layout.addStretch(1)

        self._validation_timer = QTimer(self)
        self._validation_timer.setSingleShot(True)
        self._validation_timer.setInterval(180)
        self._validation_timer.timeout.connect(self._request_validation)

        self._connect_bridge()
        self.render_service_state(self._service_snapshot)
        self.render_configuration(
            getattr(bridge, "configuration_snapshot", self._baseline)
        )

    def _connect_bridge(self) -> None:
        self.bridge.state_changed.connect(self.render_service_state)
        for signal_name, handler in (
            ("configuration_snapshot_changed", self.render_configuration),
            ("configuration_validation_changed", self._validation_finished),
            ("candidate_classification_changed", self._preview_finished),
            ("configuration_save_started", self._save_started),
            ("configuration_save_completed", self._save_finished),
            ("configuration_request_failed", self._request_failed),
        ):
            signal = getattr(self.bridge, signal_name, None)
            if signal is not None:
                signal.connect(handler)

    def render_configuration(self, snapshot: ProductConfigurationSnapshot) -> None:
        if snapshot.state is not ConfigurationDataState.AVAILABLE:
            if self._baseline.state is not ConfigurationDataState.AVAILABLE:
                self._set_editor_enabled(False)
                self._set_feedback(
                    snapshot.error or "Loading authoritative configuration...",
                    "info"
                    if snapshot.state is ConfigurationDataState.LOADING
                    else "error",
                )
            return
        if self._dirty:
            if self._baseline.revision != snapshot.revision:
                self._set_feedback(
                    "Configuration changed elsewhere. Reload before saving to review "
                    "the latest folders and rules.",
                    "warning",
                )
            return
        self._load_snapshot(snapshot)

    def render_service_state(self, snapshot: ServiceSnapshot) -> None:
        self._service_snapshot = snapshot
        self._update_actions()
        if self._dirty and not self._lifecycle_allows_save():
            self._set_feedback(
                "Stop monitoring before saving configuration changes.",
                "warning",
            )

    def _load_snapshot(self, snapshot: ProductConfigurationSnapshot) -> None:
        self._baseline = snapshot
        self._edit_generation += 1
        self._loading = True
        self.rule_tree.clear()
        for rule in snapshot.rules:
            item = QTreeWidgetItem((rule.category, ", ".join(rule.extensions)))
            self.rule_tree.addTopLevelItem(item)
        if self.rule_tree.topLevelItemCount():
            self.rule_tree.setCurrentItem(self.rule_tree.topLevelItem(0))
        self._loading = False
        self._dirty = False
        self._set_unsaved(False)
        self._validation_valid = not snapshot.issues
        revision = snapshot.revision or "unavailable"
        self.revision_label.setText(f"Revision {revision[:10]}")
        self._set_editor_enabled(True)
        if snapshot.issues:
            self._set_feedback(
                " ".join(issue.message for issue in snapshot.issues),
                "error",
            )
        else:
            self._set_feedback("Rules are loaded and valid.", "success")
        self._update_actions()

    def _add_rule(self) -> None:
        item = QTreeWidgetItem(("new_category", ".ext"))
        self.rule_tree.addTopLevelItem(item)
        self.rule_tree.setCurrentItem(item)
        self._editor_changed()
        self.category_input.setFocus()
        self.category_input.selectAll()

    def _remove_rule(self) -> None:
        item = self.rule_tree.currentItem()
        if item is None:
            self._set_feedback("Select a category to remove.", "warning")
            return
        extensions = self._extensions_from_item(item)
        if extensions:
            answer = QMessageBox.question(
                self,
                "Delete category?",
                (
                    f"Delete '{item.text(0)}' and its {len(extensions)} "
                    "extension rule(s)?"
                ),
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer is not QMessageBox.StandardButton.Yes:
                return
        self.rule_tree.takeTopLevelItem(self.rule_tree.indexOfTopLevelItem(item))
        self._editor_changed()

    def _load_selected_rule(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None = None,
    ) -> None:
        self._loading = True
        self.category_input.setText(current.text(0) if current is not None else "")
        self.extension_list.clear()
        if current is not None:
            self.extension_list.addItems(self._extensions_from_item(current))
        self._loading = False
        self._update_actions()

    def _category_changed(self, category: str) -> None:
        if self._loading:
            return
        item = self.rule_tree.currentItem()
        if item is None:
            return
        item.setText(0, category)

    def _add_extension(self) -> None:
        item = self.rule_tree.currentItem()
        extension = self.extension_input.text().strip()
        if item is None or not extension:
            self._set_feedback("Select a category and enter an extension.", "warning")
            return
        extensions = list(self._extensions_from_item(item))
        extensions.append(extension)
        item.setText(1, ", ".join(extensions))
        self.extension_input.clear()
        self._load_selected_rule(item)

    def _remove_extension(self) -> None:
        item = self.rule_tree.currentItem()
        row = self.extension_list.currentRow()
        if item is None or row < 0:
            self._set_feedback("Select an extension to remove.", "warning")
            return
        extensions = list(self._extensions_from_item(item))
        del extensions[row]
        item.setText(1, ", ".join(extensions))
        self._load_selected_rule(item)

    @staticmethod
    def _extensions_from_item(item: QTreeWidgetItem) -> tuple[str, ...]:
        return tuple(
            token.strip()
            for token in item.text(1).replace(";", ",").split(",")
            if token.strip()
        )

    def _editor_changed(self, *_args) -> None:
        if self._loading:
            return
        self._dirty = True
        self._validation_valid = False
        self._edit_generation += 1
        self._set_feedback("Checking the complete configuration...", "info")
        self._set_unsaved(True)
        self._update_actions()
        self._validation_timer.start()

    def _rules(self) -> tuple[ProductRule, ...]:
        rules = []
        for index in range(self.rule_tree.topLevelItemCount()):
            item = self.rule_tree.topLevelItem(index)
            extensions = self._extensions_from_item(item)
            rules.append(ProductRule(item.text(0).strip(), extensions))
        return tuple(rules)

    def _candidate(self) -> ProductConfigurationCandidate | None:
        if self._baseline.state is not ConfigurationDataState.AVAILABLE:
            return None
        folders = self._baseline.folders
        if folders is None:
            return None
        return ProductConfigurationCandidate(
            watch_folders=folders.watch_folders,
            organized_folder=folders.organized_folder,
            archive_by_date=folders.archive_by_date,
            rules=self._rules(),
        )

    def _request_validation(self) -> None:
        candidate = self._candidate()
        request = getattr(self.bridge, "request_configuration_validation", None)
        if candidate is not None and request is not None:
            request(self._validation_context(), candidate)

    def _validation_finished(self, context: str, result: object) -> None:
        if context != self._validation_context():
            return
        self._validation_valid = bool(getattr(result, "valid", False))
        issues = getattr(result, "issues", ())
        self._loading = True
        try:
            for index in range(self.rule_tree.topLevelItemCount()):
                item = self.rule_tree.topLevelItem(index)
                item.setToolTip(0, "")
                item.setToolTip(1, "")
            for issue in issues:
                fields = (issue.field, *issue.related_fields)
                for field in fields:
                    parts = field.split(".")
                    if (
                        len(parts) < 2
                        or parts[0] != "rules"
                        or not parts[1].isdigit()
                    ):
                        continue
                    index = int(parts[1])
                    if index < self.rule_tree.topLevelItemCount():
                        item = self.rule_tree.topLevelItem(index)
                        column = 0 if "category" in parts else 1
                        item.setToolTip(column, issue.message)
        finally:
            self._loading = False
        if issues:
            self._set_feedback(" ".join(issue.message for issue in issues), "error")
        else:
            self._set_feedback("Ready to save these rule changes.", "success")
        self._update_actions()

    def _request_preview(self) -> None:
        filename = self.preview_input.text().strip()
        request = getattr(self.bridge, "request_candidate_classification", None)
        if not filename:
            self.preview_result.setText("Enter a filename to check.")
            return
        if request is None:
            return
        self.preview_result.setText("Checking the unsaved rule set...")
        request(self.CONTEXT, self._rules(), filename)

    def _preview_finished(self, context: str, result: object) -> None:
        if context != self.CONTEXT:
            return
        if not getattr(result, "valid", False):
            self.preview_result.setText(result.message)
            return
        qualifier = "fallback" if result.fallback else "matched rule"
        extension = result.normalized_extension or "no extension"
        self.preview_result.setText(
            f"{result.filename} -> {result.category} ({qualifier}; {extension})"
        )

    def _save(self) -> None:
        candidate = self._candidate()
        request = getattr(self.bridge, "request_configuration_save", None)
        if (
            candidate is None
            or request is None
            or not self._dirty
            or not self._validation_valid
            or self._saving
            or not self._lifecycle_allows_save()
        ):
            return
        request(self.CONTEXT, candidate, self._baseline.revision)

    def _save_started(self, context: str) -> None:
        if context != self.CONTEXT:
            return
        self._saving = True
        self._set_feedback("Saving and rebuilding FilePilot safely...", "info")
        self._update_actions()

    def _save_finished(self, context: str, result: object) -> None:
        if context != self.CONTEXT:
            return
        self._saving = False
        if result.status is ConfigurationSaveStatus.SAVED and result.snapshot:
            self._load_snapshot(result.snapshot)
            self._set_feedback(result.message, "success")
        else:
            tone = "warning" if result.status is ConfigurationSaveStatus.STALE else "error"
            self._set_feedback(result.message, tone)
        self._update_actions()

    def _request_failed(self, name: str, context: str, message: str) -> None:
        if context and context.split(":", 1)[0] != self.CONTEXT:
            return
        if name == "configuration_save":
            self._saving = False
        elif name == "configuration_validation":
            self._validation_valid = False
        self._set_feedback(message, "error")
        self._update_actions()

    def _reload(self) -> None:
        self._validation_timer.stop()
        self._dirty = False
        self._validation_valid = False
        self._set_unsaved(False)
        self._edit_generation += 1
        self._update_actions()
        request = getattr(self.bridge, "request_configuration_refresh", None)
        if request is not None:
            self._set_feedback("Reverting to authoritative configuration...", "info")
            request()

    def _lifecycle_allows_save(self) -> bool:
        return (
            self._service_snapshot.startup_status is StartupStatus.READY
            and self._service_snapshot.monitor_state is MonitorState.STOPPED
        )

    def _validation_context(self) -> str:
        return f"{self.CONTEXT}:{self._edit_generation}"

    def _update_actions(self) -> None:
        available = self._baseline.state is ConfigurationDataState.AVAILABLE
        self.save_button.setEnabled(
            available
            and self._dirty
            and self._validation_valid
            and not self._saving
            and self._lifecycle_allows_save()
        )
        self.reload_button.setEnabled(not self._saving)
        self.add_button.setEnabled(available and not self._saving)
        self.remove_button.setEnabled(available and not self._saving)
        self.preview_button.setEnabled(available and not self._saving)
        selected = self.rule_tree.currentItem() is not None
        self.category_input.setEnabled(available and selected and not self._saving)
        self.extension_list.setEnabled(available and selected and not self._saving)
        self.extension_input.setEnabled(available and selected and not self._saving)
        self.add_extension_button.setEnabled(available and selected and not self._saving)
        self.remove_extension_button.setEnabled(
            available and selected and not self._saving
        )

    def _set_editor_enabled(self, enabled: bool) -> None:
        self.rule_tree.setEnabled(enabled)
        self.preview_input.setEnabled(enabled)
        self._update_actions()

    def _set_unsaved(self, dirty: bool) -> None:
        self.unsaved_label.setText("Unsaved changes" if dirty else "All changes saved")
        self.unsaved_label.setProperty("badgeTone", "warning" if dirty else "success")
        self.unsaved_label.style().unpolish(self.unsaved_label)
        self.unsaved_label.style().polish(self.unsaved_label)

    def _set_feedback(self, message: str, tone: str) -> None:
        self.feedback_label.setText(message)
        self.feedback_label.setProperty("badgeTone", tone)
        self.feedback_label.style().unpolish(self.feedback_label)
        self.feedback_label.style().polish(self.feedback_label)

    def resizeEvent(self, event) -> None:
        margin = SPACING.md if event.size().width() < 760 else SPACING.xl
        self.page_layout.setContentsMargins(margin, margin, margin, margin)
        super().resizeEvent(event)
