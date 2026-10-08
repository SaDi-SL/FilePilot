from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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
    ProductWatchFolder,
    StartupStatus,
)
from app.ui.qt.service_bridge import QtServiceBridge, ServiceSnapshot
from app.ui.qt.theme.tokens import SPACING
from app.ui.qt.widgets.section_card import SectionCard
from app.ui.qt.widgets.editor_footer import install_editor_footer


class FoldersPage(QWidget):
    CONTEXT = "folders"

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
        self.setObjectName("FoldersPage")
        self.setProperty("pageSurface", True)
        self.setAccessibleName("Folders page")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("FoldersScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        root.addWidget(self.scroll_area)

        self.content = QWidget()
        self.content.setObjectName("FoldersContent")
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

        eyebrow = QLabel("FILEPILOT / ROUTING")
        eyebrow.setProperty("role", "eyebrow")
        title = QLabel("Folders")
        title.setProperty("role", "pageTitle")
        description = QLabel(
            "Choose where new files arrive and where FilePilot organizes them."
        )
        description.setProperty("role", "secondary")
        description.setWordWrap(True)
        self.page_layout.addWidget(eyebrow)
        self.page_layout.addWidget(title)
        self.page_layout.addWidget(description)

        watch_card = SectionCard(elevated=True)
        watch_card.setProperty("cardAccent", "warm")
        watch_header = QHBoxLayout()
        watch_header.setSpacing(SPACING.sm)
        watch_heading = QLabel("Watch folders")
        watch_heading.setProperty("role", "sectionTitle")
        watch_header.addWidget(watch_heading)
        watch_actions = QHBoxLayout()
        watch_actions.addStretch(1)
        self.add_button = QPushButton("Add folder")
        self.add_button.setProperty("variant", "primary")
        self.add_button.setAccessibleName("Add a watch folder")
        self.add_button.clicked.connect(self._browse_watch_folder)
        watch_actions.addWidget(self.add_button)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.setAccessibleName("Remove selected watch folder")
        self.remove_button.clicked.connect(self._remove_watch_folder)
        watch_actions.addWidget(self.remove_button)
        watch_header.addLayout(watch_actions)
        watch_card.content_layout.addLayout(watch_header)

        watch_hint = QLabel(
            "Checked folders are monitored. Double-click a path to edit it."
        )
        watch_hint.setProperty("role", "secondary")
        watch_hint.setWordWrap(True)
        watch_card.content_layout.addWidget(watch_hint)

        self.watch_tree = QTreeWidget()
        self.watch_tree.setProperty("activityTable", True)
        self.watch_tree.setAccessibleName("Watch folders editor")
        self.watch_tree.setColumnCount(2)
        self.watch_tree.setHeaderLabels(("State", "Folder path"))
        self.watch_tree.header().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self.watch_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.watch_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.watch_tree.setRootIsDecorated(False)
        self.watch_tree.setUniformRowHeights(True)
        self.watch_tree.setMinimumHeight(150)
        self.watch_tree.setMaximumHeight(230)
        self.watch_tree.itemChanged.connect(self._watch_item_changed)
        watch_card.content_layout.addWidget(self.watch_tree)
        self.page_layout.addWidget(watch_card)

        destination_card = SectionCard()
        destination_heading = QLabel("Organized destination")
        destination_heading.setProperty("role", "sectionTitle")
        destination_copy = QLabel(
            "Organized files go into category folders here. Choose a location outside your watch folders."
        )
        destination_copy.setProperty("role", "secondary")
        destination_copy.setWordWrap(True)
        destination_row = QHBoxLayout()
        self.destination_input = QLineEdit()
        self.destination_input.setAccessibleName("Organized base folder")
        self.destination_input.setPlaceholderText("Choose an existing destination")
        self.destination_input.textChanged.connect(self._editor_changed)
        destination_row.addWidget(self.destination_input, 1)
        self.destination_button = QPushButton("Browse")
        self.destination_button.setAccessibleName("Browse for organized base folder")
        self.destination_button.clicked.connect(self._browse_destination)
        destination_row.addWidget(self.destination_button)
        self.archive_checkbox = QCheckBox("Group files by month (for example, 2026-10)")
        self.archive_checkbox.setAccessibleName("Archive organized files by month")
        self.archive_checkbox.stateChanged.connect(self._editor_changed)
        destination_card.content_layout.addWidget(destination_heading)
        destination_card.content_layout.addWidget(destination_copy)
        destination_card.content_layout.addLayout(destination_row)
        self.destination_status = QLabel("Status unavailable")
        self.destination_status.setProperty("role", "secondary")
        self.destination_status.setAccessibleName("Organized folder status")
        destination_card.content_layout.addWidget(self.destination_status)
        destination_card.content_layout.addWidget(self.archive_checkbox)
        self.page_layout.addWidget(destination_card)

        self.feedback_label = QLabel("Loading authoritative configuration...")
        self.feedback_label.setProperty("badgeTone", "info")
        self.feedback_label.setWordWrap(True)
        self.feedback_label.setAccessibleName("Folders validation status")

        actions = QHBoxLayout()
        self.unsaved_label = QLabel("All changes saved")
        self.unsaved_label.setProperty("badgeTone", "success")
        self.unsaved_label.setAccessibleName("Folders unsaved changes status")
        actions.addWidget(self.unsaved_label)
        self.revision_label = QLabel("Revision unavailable")
        self.revision_label.setProperty("role", "secondary")
        actions.addWidget(self.revision_label)
        actions.addStretch(1)
        self.reload_button = QPushButton("Revert changes")
        self.reload_button.setAccessibleName("Revert unsaved folder changes")
        self.reload_button.clicked.connect(self._reload)
        actions.addWidget(self.reload_button)
        self.save_button = QPushButton("Save folders")
        self.save_button.setProperty("variant", "primary")
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)
        self.footer = install_editor_footer(root, self.feedback_label, actions)
        self.revision_label.hide()
        self.revision_label.setToolTip("Configuration revision for diagnostics")
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
        self.watch_tree.clear()
        folders = snapshot.folders
        if folders is None:
            self._set_editor_enabled(False)
            self._set_feedback("Folder configuration is unavailable.", "error")
            return
        for folder in folders.watch_folders:
            availability = "Ready" if folder.exists else "Missing"
            item = QTreeWidgetItem(
                (
                    f"{'Active' if folder.active else 'Inactive'} | {availability}",
                    str(folder.path),
                )
            )
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsEditable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(
                0,
                Qt.CheckState.Checked if folder.active else Qt.CheckState.Unchecked,
            )
            item.setData(0, Qt.ItemDataRole.UserRole, folder.exists)
            item.setData(1, Qt.ItemDataRole.UserRole, folder.label)
            self.watch_tree.addTopLevelItem(item)
        self.destination_input.setText(str(folders.organized_folder))
        self.destination_status.setText(
            "Existing folder" if folders.organized_exists else "Folder is missing"
        )
        self.archive_checkbox.setChecked(folders.archive_by_date)
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
            self._set_feedback("Folder routes are loaded and valid.", "success")
        self._update_actions()

    def _watch_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._loading:
            return
        if column == 0:
            self._loading = True
            exists = item.data(0, Qt.ItemDataRole.UserRole)
            availability = "Ready" if exists else "Missing"
            item.setText(
                0,
                f"{'Active' if item.checkState(0) == Qt.CheckState.Checked else 'Inactive'}"
                f" | {availability}",
            )
            self._loading = False
        elif column == 1:
            self._loading = True
            item.setData(0, Qt.ItemDataRole.UserRole, None)
            state = "Active" if item.checkState(0) == Qt.CheckState.Checked else "Inactive"
            item.setText(0, f"{state} | Check")
            self._loading = False
        self._editor_changed()

    def _browse_watch_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose watch folder")
        if not selected:
            return
        item = QTreeWidgetItem(("Active | Ready", selected))
        item.setFlags(
            item.flags()
            | Qt.ItemFlag.ItemIsEditable
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        item.setCheckState(0, Qt.CheckState.Checked)
        item.setData(0, Qt.ItemDataRole.UserRole, True)
        item.setData(1, Qt.ItemDataRole.UserRole, Path(selected).name)
        self.watch_tree.addTopLevelItem(item)
        self.watch_tree.setCurrentItem(item)
        self._editor_changed()

    def _remove_watch_folder(self) -> None:
        item = self.watch_tree.currentItem()
        if item is None:
            self._set_feedback("Select a watch folder to remove.", "warning")
            return
        self.watch_tree.takeTopLevelItem(self.watch_tree.indexOfTopLevelItem(item))
        self._editor_changed()

    def _browse_destination(self) -> None:
        start = self.destination_input.text().strip()
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose organized destination",
            start,
        )
        if selected:
            self.destination_input.setText(selected)

    def _editor_changed(self, *_args) -> None:
        if self._loading:
            return
        self._dirty = True
        self._validation_valid = False
        self._edit_generation += 1
        self._set_feedback("Checking the complete configuration...", "info")
        self.destination_status.setText("Pending complete route validation")
        self._set_unsaved(True)
        self._update_actions()
        self._validation_timer.start()

    def _candidate_folders(
        self,
    ) -> tuple[tuple[ProductWatchFolder, ...], Path, bool]:
        watch_folders = []
        for index in range(self.watch_tree.topLevelItemCount()):
            item = self.watch_tree.topLevelItem(index)
            path_text = item.text(1).strip()
            watch_folders.append(
                ProductWatchFolder(
                    path_text,
                    str(item.data(1, Qt.ItemDataRole.UserRole) or ""),
                    item.checkState(0) == Qt.CheckState.Checked,
                )
            )
        destination = self.destination_input.text().strip()
        return (
            tuple(watch_folders),
            destination,
            self.archive_checkbox.isChecked(),
        )

    def _candidate(self) -> ProductConfigurationCandidate | None:
        if self._baseline.state is not ConfigurationDataState.AVAILABLE:
            return None
        watch_folders, organized_folder, archive_by_date = self._candidate_folders()
        return ProductConfigurationCandidate(
            watch_folders=watch_folders,
            organized_folder=organized_folder,
            archive_by_date=archive_by_date,
            rules=self._baseline.rules,
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
        if issues:
            folder_messages = [
                issue.message
                for issue in issues
                if issue.field.startswith("folders.")
            ]
            self.destination_status.setText(
                folder_messages[0] if folder_messages else "Route validation failed"
            )
            self._set_feedback(" ".join(issue.message for issue in issues), "error")
        else:
            self.destination_status.setText("Existing folder; route is safe")
            self._set_feedback("Ready to save these folder changes.", "success")
        self._update_actions()

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
            self._service_snapshot.startup_status
            in {StartupStatus.READY, StartupStatus.SETUP_REQUIRED}
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
        self.destination_button.setEnabled(available and not self._saving)

    def _set_editor_enabled(self, enabled: bool) -> None:
        self.watch_tree.setEnabled(enabled)
        self.destination_input.setEnabled(enabled)
        self.destination_button.setEnabled(enabled)
        self.archive_checkbox.setEnabled(enabled)
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
