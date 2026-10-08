from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.application_service import MonitorState, StartupStatus
from app.product_configuration import ConfigurationDataState, ConfigurationSaveStatus
from app.product_identity import PRODUCT_IDENTITY
from app.product_settings import ProductSettingsCandidate, ProductSettingsSnapshot
from app.ui.qt.service_bridge import QtServiceBridge, ServiceSnapshot
from app.ui.qt.theme.tokens import SPACING
from app.ui.qt.widgets.section_card import SectionCard
from app.ui.qt.widgets.editor_footer import install_editor_footer


class SecondsControl(QDoubleSpinBox):
    def textFromValue(self, value: float) -> str:
        text = super().textFromValue(value)
        decimal = self.locale().decimalPoint()
        return text.rstrip("0").rstrip(decimal) if decimal in text else text


class SettingsPage(QWidget):
    CONTEXT = "settings"

    def __init__(
        self,
        bridge: QtServiceBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._baseline = ProductSettingsSnapshot.loading()
        self._service_snapshot = bridge.snapshot
        self._loading = False
        self._dirty = False
        self._saving = False
        self._validation_valid = False
        self._edit_generation = 0
        self._edited_fields: set[str] = set()
        self.setObjectName("SettingsPage")
        self.setProperty("pageSurface", True)
        self.setAccessibleName("Settings page")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("SettingsScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        root.addWidget(self.scroll_area)

        self.content = QWidget()
        self.content.setObjectName("SettingsContent")
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

        eyebrow = QLabel("FILEPILOT / PREFERENCES")
        eyebrow.setProperty("role", "eyebrow")
        title = QLabel("Settings")
        title.setProperty("role", "pageTitle")
        description = QLabel(
            "Make FilePilot work your way. Choose processing preferences and your AI provider."
        )
        description.setProperty("role", "secondary")
        description.setWordWrap(True)
        self.page_layout.addWidget(eyebrow)
        self.page_layout.addWidget(title)
        self.page_layout.addWidget(description)

        self.settings_tabs = QTabWidget()
        self.settings_tabs.setAccessibleName("Settings sections")
        self.page_layout.addWidget(self.settings_tabs)
        tab_layouts = []
        for caption in ("General", "AI classification", "About && privacy"):
            panel = QWidget()
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(0, SPACING.md, 0, 0)
            panel_layout.setSpacing(SPACING.md)
            self.settings_tabs.addTab(panel, caption)
            tab_layouts.append(panel_layout)

        general_card = SectionCard(elevated=True)
        general_card.setProperty("cardAccent", "purple")
        general_heading = QLabel("General processing")
        general_heading.setProperty("role", "sectionTitle")
        general_copy = QLabel(
            "Choose how long to wait for new files and repeated events. Stop monitoring before saving."
        )
        general_copy.setProperty("role", "secondary")
        general_copy.setWordWrap(True)
        general_card.content_layout.addWidget(general_heading)
        general_card.content_layout.addWidget(general_copy)
        general_form = QFormLayout()
        general_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.processing_wait = self._seconds_control(
            "Wait before processing a newly detected file"
        )
        self.processing_wait.setAccessibleName("Processing wait seconds")
        general_form.addRow("New-file wait", self.processing_wait)
        self.duplicate_window = self._seconds_control(
            "Suppress duplicate filesystem events within this interval"
        )
        self.duplicate_window.setAccessibleName("Duplicate event window seconds")
        general_form.addRow("Duplicate event window", self.duplicate_window)
        general_card.content_layout.addLayout(general_form)
        tab_layouts[0].addWidget(general_card)

        ai_card = SectionCard()
        ai_heading = QLabel("AI classification")
        ai_heading.setProperty("role", "sectionTitle")
        ai_copy = QLabel(
            "Use AI to classify files automatically. Local answers in Ask your files are configured separately."
        )
        ai_copy.setProperty("role", "secondary")
        ai_copy.setWordWrap(True)
        ai_card.content_layout.addWidget(ai_heading)
        ai_card.content_layout.addWidget(ai_copy)
        self.ai_enabled = QCheckBox("Enable automatic AI classification")
        self.ai_enabled.setAccessibleName("Enable automatic AI classification")
        ai_card.content_layout.addWidget(self.ai_enabled)
        ai_form = QFormLayout()
        ai_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.ai_provider = QComboBox()
        self.ai_provider.setAccessibleName("AI provider")
        self.ai_provider.addItem("Local Ollama", "ollama")
        self.ai_provider.addItem("Anthropic Claude (cloud)", "claude")
        ai_form.addRow("Provider", self.ai_provider)
        self.ollama_model = QLineEdit()
        self.ollama_model.setAccessibleName("Ollama model name")
        self.ollama_model.setPlaceholderText("Example: gemma4:e4b-it-qat")
        self.ollama_model.setMaxLength(200)
        ai_form.addRow("Ollama model", self.ollama_model)
        self.credential_status = QLabel("Credential status unavailable")
        self.credential_status.setProperty("role", "caption")
        self.credential_status.setWordWrap(True)
        ai_form.addRow("Claude credential", self.credential_status)
        ai_card.content_layout.addLayout(ai_form)
        self.ai_status = QLabel("AI status unavailable")
        self.ai_status.setProperty("badgeTone", "neutral")
        self.ai_status.setWordWrap(True)
        self.ai_status.setAccessibleName("AI configuration status")
        ai_card.content_layout.addWidget(self.ai_status)
        tab_layouts[1].addWidget(ai_card)

        startup_card = SectionCard()
        startup_heading = QLabel("Startup")
        startup_heading.setProperty("role", "sectionTitle")
        self.startup_summary = QLabel("Loading startup behavior...")
        self.startup_summary.setProperty("role", "secondary")
        self.startup_summary.setWordWrap(True)
        startup_card.content_layout.addWidget(startup_heading)
        startup_card.content_layout.addWidget(self.startup_summary)
        tab_layouts[0].addWidget(startup_card)

        privacy_card = SectionCard()
        privacy_heading = QLabel("Privacy & data")
        privacy_heading.setProperty("role", "sectionTitle")
        self.privacy_summary = QLabel("Loading local data behavior...")
        self.privacy_summary.setProperty("role", "secondary")
        self.privacy_summary.setWordWrap(True)
        self.cloud_summary = QLabel("")
        self.cloud_summary.setProperty("role", "secondary")
        self.cloud_summary.setWordWrap(True)
        privacy_card.content_layout.addWidget(privacy_heading)
        privacy_card.content_layout.addWidget(self.privacy_summary)
        privacy_card.content_layout.addWidget(self.cloud_summary)
        tab_layouts[2].addWidget(privacy_card)

        about_card = SectionCard()
        about_heading = QLabel("About")
        about_heading.setProperty("role", "sectionTitle")
        identity = getattr(bridge, "product_identity", PRODUCT_IDENTITY)
        self.product_name_label = QLabel(identity.product_name)
        self.product_name_label.setProperty("role", "headline")
        self.version_label = QLabel(f"Version {identity.display_version}")
        self.version_label.setObjectName("ProductVersionLabel")
        self.version_label.setProperty("role", "body")
        self.build_label = QLabel(identity.build_description)
        self.build_label.setProperty("role", "secondary")
        self.build_label.setWordWrap(True)
        about_card.content_layout.addWidget(about_heading)
        about_card.content_layout.addWidget(self.product_name_label)
        about_card.content_layout.addWidget(self.version_label)
        about_card.content_layout.addWidget(self.build_label)
        tab_layouts[2].addWidget(about_card)

        self.feedback_label = QLabel("Loading authoritative Settings...")
        self.feedback_label.setProperty("badgeTone", "info")
        self.feedback_label.setWordWrap(True)
        self.feedback_label.setAccessibleName("Settings validation status")

        actions = QHBoxLayout()
        self.unsaved_label = QLabel("All changes saved")
        self.unsaved_label.setProperty("badgeTone", "success")
        self.unsaved_label.setAccessibleName("Settings unsaved changes status")
        actions.addWidget(self.unsaved_label)
        self.revision_label = QLabel("Revision unavailable")
        self.revision_label.setProperty("role", "secondary")
        actions.addWidget(self.revision_label)
        actions.addStretch(1)
        self.revert_button = QPushButton("Revert changes")
        self.revert_button.setAccessibleName("Revert unsaved Settings changes")
        self.revert_button.clicked.connect(self._reload)
        actions.addWidget(self.revert_button)
        self.save_button = QPushButton("Save settings")
        self.save_button.setProperty("variant", "primary")
        self.save_button.setAccessibleName("Save Settings")
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)
        self.footer = install_editor_footer(root, self.feedback_label, actions)
        self.revision_label.hide()
        self.revision_label.setToolTip("Configuration revision for diagnostics")
        for tab_layout in tab_layouts:
            tab_layout.addStretch(1)
        self.page_layout.addStretch(1)

        self._validation_timer = QTimer(self)
        self._validation_timer.setSingleShot(True)
        self._validation_timer.setInterval(180)
        self._validation_timer.timeout.connect(self._request_validation)

        self.processing_wait.valueChanged.connect(
            lambda: self._field_changed("processing_wait")
        )
        self.duplicate_window.valueChanged.connect(
            lambda: self._field_changed("duplicate_window")
        )
        self.ai_enabled.stateChanged.connect(self._editor_changed)
        self.ai_provider.currentIndexChanged.connect(self._provider_changed)
        self.ollama_model.textChanged.connect(self._editor_changed)
        QWidget.setTabOrder(self.processing_wait, self.duplicate_window)
        QWidget.setTabOrder(self.duplicate_window, self.ai_enabled)
        QWidget.setTabOrder(self.ai_enabled, self.ai_provider)
        QWidget.setTabOrder(self.ai_provider, self.ollama_model)
        QWidget.setTabOrder(self.ollama_model, self.revert_button)
        QWidget.setTabOrder(self.revert_button, self.save_button)

        self._connect_bridge()
        self.render_service_state(self._service_snapshot)
        self.render_settings(getattr(bridge, "settings_snapshot", self._baseline))

    @staticmethod
    def _seconds_control(tooltip: str) -> QDoubleSpinBox:
        control = SecondsControl()
        control.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.PlusMinus)
        control.setRange(0, 3600)
        control.setDecimals(6)
        control.setSingleStep(0.5)
        control.setSuffix(" seconds")
        control.setToolTip(tooltip)
        return control

    def _connect_bridge(self) -> None:
        self.bridge.state_changed.connect(self.render_service_state)
        for signal_name, handler in (
            ("settings_snapshot_changed", self.render_settings),
            ("settings_validation_changed", self._validation_finished),
            ("settings_save_started", self._save_started),
            ("settings_save_completed", self._save_finished),
            ("settings_request_failed", self._request_failed),
        ):
            signal = getattr(self.bridge, signal_name, None)
            if signal is not None:
                signal.connect(handler)

    def render_settings(self, snapshot: ProductSettingsSnapshot) -> None:
        if snapshot.state is not ConfigurationDataState.AVAILABLE:
            if self._dirty:
                self._set_feedback(
                    "Authoritative Settings could not be refreshed; unsaved edits were "
                    "preserved. Revert again after configuration access is restored.",
                    "warning",
                )
            else:
                self._validation_valid = False
                self._set_editor_enabled(False)
                self._set_feedback(
                    snapshot.error or "Loading authoritative Settings...",
                    "info" if snapshot.state is ConfigurationDataState.LOADING else "error",
                )
            return
        if self._dirty:
            if self._baseline.revision != snapshot.revision:
                self._set_feedback(
                    "Configuration changed elsewhere. Revert before saving to review "
                    "the latest Rules, Folders, and Settings.",
                    "warning",
                )
            return
        self._load_snapshot(snapshot)

    def render_service_state(self, snapshot: ServiceSnapshot) -> None:
        self._service_snapshot = snapshot
        self._update_actions()
        self._render_valid_dirty_feedback()

    def _load_snapshot(self, snapshot: ProductSettingsSnapshot) -> None:
        if snapshot.general is None or snapshot.ai is None:
            return
        self._baseline = snapshot
        self._edit_generation += 1
        self._loading = True
        self.processing_wait.setValue(snapshot.general.processing_wait_seconds)
        self.duplicate_window.setValue(
            snapshot.general.duplicate_event_window_seconds
        )
        self.ai_enabled.setChecked(snapshot.ai.automatic_classification)
        provider_index = self.ai_provider.findData(snapshot.ai.provider)
        self.ai_provider.setCurrentIndex(max(0, provider_index))
        self.ollama_model.setText(snapshot.ai.ollama_model)
        self.ollama_model.setEnabled(snapshot.ai.provider == "ollama")
        self.credential_status.setText(
            "Configured; value is never shown here."
            if snapshot.ai.credential_configured
            else "Not configured. Credential editing is unavailable on this page."
        )
        self.ai_status.setText(snapshot.ai.status_text)
        self.ai_status.setProperty(
            "badgeTone",
            "success" if snapshot.ai.status.value.endswith("configured") else "neutral",
        )
        self.ai_status.style().unpolish(self.ai_status)
        self.ai_status.style().polish(self.ai_status)
        if snapshot.startup is not None:
            self.startup_summary.setText(
                "On a normal launch, start monitoring from Overview when you are ready."
            )
            self.startup_summary.setToolTip(snapshot.startup.summary)
        if snapshot.privacy is not None:
            self.privacy_summary.setText(snapshot.privacy.summary)
            self.cloud_summary.setText(snapshot.privacy.cloud_summary)
        self._loading = False
        self._dirty = False
        self._edited_fields.clear()
        self._validation_valid = not snapshot.issues
        self._set_unsaved(False)
        revision = snapshot.revision or "unavailable"
        self.revision_label.setText(f"Revision {revision[:10]}")
        self._set_editor_enabled(True)
        if snapshot.issues:
            self._set_feedback(" ".join(issue.message for issue in snapshot.issues), "error")
        else:
            self._set_feedback("Settings are loaded and valid.", "success")
        self._update_actions()

    def _provider_changed(self, *_args) -> None:
        self.ollama_model.setEnabled(self.ai_provider.currentData() == "ollama")
        self._editor_changed()

    def _field_changed(self, field: str) -> None:
        if not self._loading:
            self._edited_fields.add(field)
        self._editor_changed()

    def _editor_changed(self, *_args) -> None:
        if self._loading:
            return
        self._dirty = True
        self._validation_valid = False
        self._edit_generation += 1
        self._set_unsaved(True)
        self._set_feedback("Checking Settings against the complete configuration...", "info")
        self._update_actions()
        self._validation_timer.start()

    def _candidate(self) -> ProductSettingsCandidate | None:
        if self._baseline.state is not ConfigurationDataState.AVAILABLE:
            return None
        general = self._baseline.general
        if general is None:
            return None
        return ProductSettingsCandidate(
            (
                self.processing_wait.value()
                if "processing_wait" in self._edited_fields
                else general.processing_wait_seconds
            ),
            (
                self.duplicate_window.value()
                if "duplicate_window" in self._edited_fields
                else general.duplicate_event_window_seconds
            ),
            self.ai_enabled.isChecked(),
            str(self.ai_provider.currentData()),
            self.ollama_model.text(),
        )

    def _request_validation(self) -> None:
        candidate = self._candidate()
        request = getattr(self.bridge, "request_settings_validation", None)
        if candidate is not None and request is not None:
            request(self._validation_context(), candidate)

    def _validation_finished(self, context: str, result: object) -> None:
        if context != self._validation_context():
            return
        issues = getattr(result, "issues", ())
        self._validation_valid = bool(getattr(result, "valid", False)) and not issues
        if not self._validation_valid:
            message = (
                " ".join(issue.message for issue in issues)
                if issues
                else "Fix Settings validation issues before saving."
            )
            self._set_feedback(message, "error")
        else:
            self._render_valid_dirty_feedback()
        self._update_actions()

    def _render_valid_dirty_feedback(self) -> None:
        if not self._dirty or not self._validation_valid:
            return
        if self._lifecycle_allows_save():
            self._set_feedback("Ready to save these Settings changes.", "success")
        else:
            self._set_feedback(
                "Stop monitoring to save these Settings changes.",
                "warning",
            )

    def _save(self) -> None:
        candidate = self._candidate()
        request = getattr(self.bridge, "request_settings_save", None)
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
        self._set_editor_enabled(False)
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
            self._set_editor_enabled(True)
        self._update_actions()

    def _request_failed(self, name: str, context: str, message: str) -> None:
        if context and context.split(":", 1)[0] != self.CONTEXT:
            return
        if name == "settings_save":
            self._saving = False
            self._set_editor_enabled(True)
        elif name == "settings_validation":
            self._validation_valid = False
        self._set_feedback(message, "error")
        self._update_actions()

    def _reload(self) -> None:
        self._validation_timer.stop()
        self._dirty = False
        self._edited_fields.clear()
        self._validation_valid = False
        self._set_unsaved(False)
        self._edit_generation += 1
        self._update_actions()
        request = getattr(self.bridge, "request_settings_refresh", None)
        if request is not None:
            self._set_feedback("Reverting to authoritative Settings...", "info")
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
        self.revert_button.setEnabled(not self._saving)

    def _set_editor_enabled(self, enabled: bool) -> None:
        self.processing_wait.setEnabled(enabled)
        self.duplicate_window.setEnabled(enabled)
        self.ai_enabled.setEnabled(enabled)
        self.ai_provider.setEnabled(enabled)
        self.ollama_model.setEnabled(
            enabled and self.ai_provider.currentData() == "ollama"
        )
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
