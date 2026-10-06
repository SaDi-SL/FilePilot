from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from app.ui.qt.theme.tokens import SPACING
from app.ui.qt.widgets.section_card import SectionCard


class MetricCard(SectionCard):
    def __init__(self, title: str, value: str = "Not available", detail: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(f"{title} status summary")
        self.setMinimumHeight(104)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.content_layout.setSpacing(SPACING.xs)

        title_row = QHBoxLayout()
        title_row.setSpacing(SPACING.sm)
        self.status_indicator = QFrame()
        self.status_indicator.setProperty("statusTone", "neutral")
        self.status_indicator.setFixedSize(7, 7)
        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "caption")
        title_row.addWidget(self.status_indicator, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(self.title_label)
        title_row.addStretch(1)
        self.value_label = QLabel(value)
        self.value_label.setProperty("role", "metric")
        self.value_label.setAccessibleName(f"{title} status: {value}")
        self.detail_label = QLabel(detail)
        self.detail_label.setProperty("role", "caption")
        self.detail_label.setWordWrap(True)
        self.detail_label.setAccessibleName(f"{title} status detail")
        self.content_layout.addLayout(title_row)
        self.content_layout.addWidget(self.value_label)
        self.content_layout.addWidget(self.detail_label)

    def set_value(self, value: str, detail: str = "", tone: str = "neutral") -> None:
        self.value_label.setText(value)
        self.value_label.setAccessibleName(f"{self.title_label.text()} status: {value}")
        self.detail_label.setText(detail)
        self.status_indicator.setProperty("statusTone", tone)
        style = self.status_indicator.style()
        style.unpolish(self.status_indicator)
        style.polish(self.status_indicator)
