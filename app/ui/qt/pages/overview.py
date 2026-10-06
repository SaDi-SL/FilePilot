from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

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
        subtitle = QLabel("Monitor organization, safety, and recent file activity from one place.")
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

        self.control_row = QWidget()
        self.control_layout = QHBoxLayout(self.control_row)
        self.control_layout.setContentsMargins(0, 0, 0, 0)
        self.control_layout.setSpacing(SPACING.md)
        self.state_panel = StatePanel()
        self.state_panel.start_requested.connect(self.bridge.request_start)
        self.state_panel.stop_requested.connect(self.bridge.request_stop)
        self.control_layout.addWidget(self.state_panel, 3)

        self.system_status = SectionCard()
        self.system_status.setAccessibleName("System status summary")
        self.system_status.content_layout.setSpacing(SPACING.sm)
        heading = QLabel("Safety status")
        heading.setProperty("role", "sectionTitle")
        caption = QLabel("Live facts from FilePilot services")
        caption.setProperty("role", "caption")
        self.system_status.content_layout.addWidget(heading)
        self.system_status.content_layout.addWidget(caption)
        divider = QFrame()
        divider.setProperty("divider", True)
        self.system_status.content_layout.addWidget(divider)
        self.monitoring_status_value = self._status_row("Monitoring", "Initializing")
        self.startup_status_value = self._status_row("Startup", "Checking")
        self.recovery_status_value = self._status_row("Recovery", "Not checked")
        self.system_status.content_layout.addStretch(1)
        self.control_layout.addWidget(self.system_status, 2)
        self.page_layout.addWidget(self.control_row)

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
        self.render_state(self.bridge.snapshot)
        self.render_product_snapshot(self.bridge.product_snapshot)
        recovery_snapshot = getattr(self.bridge, "recovery_snapshot", None)
        if recovery_snapshot is not None:
            self.render_recovery_snapshot(recovery_snapshot)

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
        startup_text = snapshot.startup_status.value.replace("_", " ").title() if snapshot.startup_status is not None else "Checking"
        if snapshot.startup_status is StartupStatus.BLOCKED:
            recovery_text = "Action required"
        elif snapshot.startup_status is StartupStatus.READY:
            recovery_text = "Ready"
        else:
            recovery_text = "Not checked"

        if snapshot.startup_status is StartupStatus.BLOCKED:
            header_text, tone = "Action required", "error"
        elif snapshot.startup_status is StartupStatus.SETUP_REQUIRED:
            header_text, tone = "Setup required", "warning"
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

        self._set_status_value(self.monitoring_status_value, "Monitoring", monitor_text)
        self._set_status_value(self.startup_status_value, "Startup state", startup_text)
        self._set_status_value(self.recovery_status_value, "Recovery", recovery_text)

    def render_product_snapshot(self, snapshot: ProductSnapshot) -> None:
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

    @staticmethod
    def _set_status_value(label: QLabel, name: str, value: str) -> None:
        label.setText(value)
        label.setAccessibleName(f"{name}: {value}")

    def resizeEvent(self, event) -> None:
        width = event.size().width()
        columns = 1 if width < 560 else 2 if width < 980 else 4
        self._arrange_metrics(columns)
        self.control_layout.setDirection(QHBoxLayout.Direction.TopToBottom if width < 860 else QHBoxLayout.Direction.LeftToRight)
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
