from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.application_service import MonitorState, StartupStatus
from app.ui.qt.service_bridge import ServiceSnapshot
from app.ui.qt.theme.tokens import SPACING
from app.ui.qt.widgets.section_card import SectionCard


def _refresh_style(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class StatePanel(SectionCard):
    start_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Monitoring control")
        self.content_layout.setSpacing(SPACING.sm)

        heading_row = QHBoxLayout()
        heading_row.setSpacing(SPACING.md)
        heading = QLabel("Automatic organization")
        heading.setProperty("role", "sectionTitle")
        heading_row.addWidget(heading)
        heading_row.addStretch(1)

        self.status_indicator = QFrame()
        self.status_indicator.setProperty("statusTone", "info")
        self.status_indicator.setFixedSize(8, 8)
        heading_row.addWidget(
            self.status_indicator,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        self.status_badge = QLabel("Initializing")
        self.status_badge.setProperty("badgeTone", "info")
        self.status_badge.setAccessibleName("Monitoring state: Initializing")
        heading_row.addWidget(self.status_badge)

        self.title_label = QLabel("Preparing FilePilot")
        self.title_label.setProperty("role", "sectionTitle")
        self.message_label = QLabel(
            "FilePilot is checking configuration and recovery state."
        )
        self.message_label.setProperty("role", "secondary")
        self.message_label.setWordWrap(True)
        self.message_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        self.details_frame = QFrame()
        self.details_frame.setProperty("card", True)
        details_layout = QVBoxLayout(self.details_frame)
        details_layout.setContentsMargins(
            SPACING.lg,
            SPACING.md,
            SPACING.lg,
            SPACING.md,
        )
        details_layout.setSpacing(SPACING.sm)
        self.details_title = QLabel("Action required")
        self.details_title.setProperty("role", "body")
        self.details_label = QLabel()
        self.details_label.setProperty("role", "secondary")
        self.details_label.setWordWrap(True)
        self.details_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        details_layout.addWidget(self.details_title)
        details_layout.addWidget(self.details_label)
        self.details_frame.hide()

        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING.md)
        self.start_button = QPushButton("Start organizing")
        self.start_button.setProperty("variant", "primary")
        self.start_button.setAccessibleName("Start FilePilot monitoring")
        self.start_button.setMinimumWidth(132)
        self.start_button.clicked.connect(self.start_requested)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setAccessibleName("Stop FilePilot monitoring")
        self.stop_button.setMinimumWidth(132)
        self.stop_button.clicked.connect(self.stop_requested)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        button_row.addStretch(1)

        QWidget.setTabOrder(self.start_button, self.stop_button)

        self.content_layout.addLayout(heading_row)
        self.content_layout.addWidget(self.title_label)
        self.content_layout.addWidget(self.message_label)
        self.content_layout.addWidget(self.details_frame)
        self.content_layout.addLayout(button_row)

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(False)

    def render(self, snapshot: ServiceSnapshot) -> None:
        startup = snapshot.startup_status
        state = snapshot.monitor_state
        self.details_frame.hide()

        if startup is None:
            self._set_content(
                "Initializing",
                "info",
                "Preparing FilePilot",
                "FilePilot is checking configuration and recovery state.",
                False,
                False,
            )
            return

        if startup is StartupStatus.SETUP_REQUIRED:
            self._set_content(
                "Setup required",
                "warning",
                "Initial configuration is needed",
                "Open Folders, choose the locations FilePilot should watch and organize, "
                "then save the validated configuration. Monitoring stays stopped.",
                False,
                False,
            )
            return

        if startup is StartupStatus.BLOCKED or state is MonitorState.BLOCKED:
            self._set_content(
                "Action required",
                "error",
                "Monitoring is safely blocked",
                snapshot.error
                or "Recovery evidence requires review before monitoring can start.",
                False,
                False,
            )
            details = []
            if snapshot.blocking_operation_ids:
                details.append(
                    "Operations: " + ", ".join(snapshot.blocking_operation_ids)
                )
            if snapshot.blocking_reasons:
                details.append("Reasons: " + "; ".join(snapshot.blocking_reasons))
            self.details_label.setText(
                "\n".join(details)
                or "Recovery review is required. No automatic resolution was attempted."
            )
            self.details_frame.show()
            return

        if startup is StartupStatus.ERROR:
            self._set_content(
                "Startup error",
                "error",
                "FilePilot could not initialize",
                snapshot.error or "An unexpected startup error occurred.",
                False,
                False,
            )
            return

        content = {
            MonitorState.STOPPED: (
                "Stopped",
                "neutral",
                "Monitoring is paused",
                "Start monitoring when you are ready for FilePilot to process new files.",
                True,
                False,
            ),
            MonitorState.STARTING: (
                "Starting",
                "info",
                "Starting monitoring",
                "FilePilot is starting every configured watch folder.",
                False,
                False,
            ),
            MonitorState.RUNNING: (
                "Running",
                "success",
                "Monitoring is active",
                "FilePilot is watching all configured active folders.",
                False,
                True,
            ),
            MonitorState.STOPPING: (
                "Stopping",
                "warning",
                "Stopping safely",
                "FilePilot is draining active work before monitoring stops.",
                False,
                False,
            ),
            MonitorState.ERROR: (
                "Error",
                "error",
                "Monitoring needs attention",
                snapshot.error or "Monitoring did not complete the requested action.",
                False,
                True,
            ),
        }
        self._set_content(*content.get(state, content[MonitorState.ERROR]))
        if state is MonitorState.ERROR and snapshot.failed_folders:
            self.details_title.setText("Folders needing attention")
            self.details_label.setText("\n".join(snapshot.failed_folders))
            self.details_frame.show()

    def _set_content(
        self,
        badge: str,
        tone: str,
        title: str,
        message: str,
        start_enabled: bool,
        stop_enabled: bool,
    ) -> None:
        self.status_badge.setText(badge)
        self.status_badge.setProperty("badgeTone", tone)
        self.status_badge.setAccessibleName(f"Monitoring state: {badge}")
        self.status_indicator.setProperty("statusTone", tone)
        self.title_label.setText(title)
        self.message_label.setText(message)
        self.start_button.setEnabled(start_enabled)
        self.stop_button.setEnabled(stop_enabled)
        _refresh_style(self.status_badge)
        _refresh_style(self.status_indicator)
