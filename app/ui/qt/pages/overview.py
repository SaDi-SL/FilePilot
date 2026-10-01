from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.application_service import MonitorState, StartupStatus
from app.ui.qt.service_bridge import QtServiceBridge, ServiceSnapshot
from app.ui.qt.theme.tokens import SPACING
from app.ui.qt.widgets.metric_card import MetricCard
from app.ui.qt.widgets.section_card import SectionCard
from app.ui.qt.widgets.state_panel import StatePanel


class OverviewPage(QWidget):
    def __init__(
        self,
        bridge: QtServiceBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.setObjectName("OverviewPage")
        self.setAccessibleName("Overview page")

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("OverviewScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.viewport().setObjectName("OverviewViewport")
        self.scroll_area.viewport().setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground,
            True,
        )
        root_layout.addWidget(self.scroll_area)

        self.content = QWidget()
        self.content.setObjectName("OverviewContent")
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

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(SPACING.xs)
        eyebrow = QLabel("FILEPILOT")
        eyebrow.setProperty("role", "eyebrow")
        self.page_title = QLabel("Overview")
        self.page_title.setProperty("role", "pageTitle")
        product_message = QLabel("File automation, under control")
        product_message.setProperty("role", "headline")
        description = QLabel(
            "Confirm lifecycle, monitoring, and recovery readiness before files "
            "are processed."
        )
        description.setProperty("role", "secondary")
        description.setWordWrap(True)
        description.setMaximumWidth(760)
        header_layout.addWidget(eyebrow)
        header_layout.addWidget(self.page_title)
        header_layout.addWidget(product_message)
        header_layout.addWidget(description)
        self.page_layout.addWidget(header)

        self.metrics_widget = QWidget()
        self.metrics_layout = QGridLayout(self.metrics_widget)
        self.metrics_layout.setContentsMargins(0, 0, 0, 0)
        self.metrics_layout.setHorizontalSpacing(SPACING.lg)
        self.metrics_layout.setVerticalSpacing(SPACING.lg)
        self.monitoring_card = MetricCard(
            "Monitoring",
            "Initializing",
            "Waiting for lifecycle state",
        )
        self.startup_card = MetricCard(
            "Startup",
            "Checking",
            "Configuration and recovery",
        )
        self.recovery_card = MetricCard(
            "Recovery",
            "Not checked",
            "No result is available yet",
        )
        self._metric_cards = (
            self.monitoring_card,
            self.startup_card,
            self.recovery_card,
        )
        self._arrange_metrics(3)
        self.page_layout.addWidget(self.metrics_widget)

        self.state_panel = StatePanel()
        self.state_panel.start_requested.connect(self.bridge.request_start)
        self.state_panel.stop_requested.connect(self.bridge.request_stop)
        self.page_layout.addWidget(self.state_panel)

        self.system_status = SectionCard()
        self.system_status.setAccessibleName("System status summary")
        self.system_status.content_layout.setSpacing(SPACING.sm)
        system_heading = QLabel("System Status")
        system_heading.setProperty("role", "sectionTitle")
        system_copy = QLabel(
            "Current lifecycle facts reported directly by FilePilotService."
        )
        system_copy.setProperty("role", "secondary")
        system_copy.setWordWrap(True)
        self.system_status.content_layout.addWidget(system_heading)
        self.system_status.content_layout.addWidget(system_copy)

        divider = QFrame()
        divider.setProperty("divider", True)
        self.system_status.content_layout.addWidget(divider)

        self.monitoring_status_value = self._status_row(
            "Monitoring",
            "Initializing",
        )
        self.startup_status_value = self._status_row(
            "Startup state",
            "Checking",
        )
        self.recovery_status_value = self._status_row(
            "Recovery",
            "Not checked",
        )
        self.page_layout.addWidget(self.system_status)
        self.page_layout.addStretch(1)

        self.bridge.state_changed.connect(self.render_state)
        self.render_state(self.bridge.snapshot)

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
        self.state_panel.render(snapshot)
        monitor_text = snapshot.monitor_state.value.replace("_", " ").title()
        startup_text = (
            snapshot.startup_status.value.replace("_", " ").title()
            if snapshot.startup_status is not None
            else "Checking"
        )
        if snapshot.startup_status is StartupStatus.BLOCKED:
            recovery_text = "Action required"
            recovery_detail = "Recovery review blocks monitoring"
        elif snapshot.startup_status is StartupStatus.READY:
            recovery_text = "Ready"
            recovery_detail = "No blocking recovery case"
        else:
            recovery_text = "Not checked"
            recovery_detail = "Runtime recovery has not completed"

        monitoring_tone = {
            MonitorState.STOPPED: "neutral",
            MonitorState.STARTING: "info",
            MonitorState.RUNNING: "success",
            MonitorState.STOPPING: "warning",
            MonitorState.BLOCKED: "error",
            MonitorState.ERROR: "error",
        }[snapshot.monitor_state]
        startup_tone = {
            None: "info",
            StartupStatus.READY: "success",
            StartupStatus.SETUP_REQUIRED: "warning",
            StartupStatus.BLOCKED: "error",
            StartupStatus.ERROR: "error",
        }[snapshot.startup_status]
        recovery_tone = (
            "error"
            if snapshot.startup_status is StartupStatus.BLOCKED
            else "success"
            if snapshot.startup_status is StartupStatus.READY
            else "info"
        )

        self.monitoring_card.set_value(
            monitor_text,
            self._monitoring_detail(snapshot.monitor_state),
            monitoring_tone,
        )
        self.startup_card.set_value(
            startup_text,
            self._startup_detail(snapshot),
            startup_tone,
        )
        self.recovery_card.set_value(recovery_text, recovery_detail, recovery_tone)
        self._set_status_value(self.monitoring_status_value, "Monitoring", monitor_text)
        self._set_status_value(self.startup_status_value, "Startup state", startup_text)
        self._set_status_value(self.recovery_status_value, "Recovery", recovery_text)

    @staticmethod
    def _startup_detail(snapshot: ServiceSnapshot) -> str:
        if snapshot.error:
            return snapshot.error
        return {
            None: "Configuration and recovery checks in progress",
            StartupStatus.READY: "Configuration and recovery checks passed",
            StartupStatus.SETUP_REQUIRED: "Initial configuration is required",
            StartupStatus.BLOCKED: "Recovery review prevents startup",
            StartupStatus.ERROR: "Startup did not complete",
        }[snapshot.startup_status]

    @staticmethod
    def _set_status_value(label: QLabel, name: str, value: str) -> None:
        label.setText(value)
        label.setAccessibleName(f"{name}: {value}")

    @staticmethod
    def _monitoring_detail(state: MonitorState) -> str:
        return {
            MonitorState.STOPPED: "Ready for an explicit start",
            MonitorState.STARTING: "Starting configured folders",
            MonitorState.RUNNING: "All active folders confirmed",
            MonitorState.STOPPING: "Draining active work",
            MonitorState.BLOCKED: "Start is disabled",
            MonitorState.ERROR: "Review the lifecycle message",
        }[state]

    def resizeEvent(self, event) -> None:
        columns = 1 if event.size().width() < 760 else 3
        self._arrange_metrics(columns)
        margin = SPACING.lg if event.size().width() < 760 else SPACING.xl
        self.page_layout.setContentsMargins(margin, margin, margin, margin)
        self.page_layout.setSpacing(
            SPACING.md if event.size().width() < 760 else SPACING.lg
        )
        super().resizeEvent(event)

    def _arrange_metrics(self, columns: int) -> None:
        while self.metrics_layout.count():
            item = self.metrics_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(self.metrics_widget)
        for index, card in enumerate(self._metric_cards):
            card.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Preferred,
            )
            self.metrics_layout.addWidget(card, index // columns, index % columns)
        for column in range(len(self._metric_cards)):
            self.metrics_layout.setColumnStretch(column, 0)
        for column in range(columns):
            self.metrics_layout.setColumnStretch(column, 1)
