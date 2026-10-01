from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.ui.qt.theme.tokens import SPACING
from app.ui.qt.widgets.section_card import SectionCard


class PlaceholderPage(QWidget):
    def __init__(
        self,
        title: str,
        description: str,
        icon: QIcon,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PlaceholderPage")
        self.setProperty("pageSurface", True)
        self.setAutoFillBackground(True)
        self.setAccessibleName(f"{title} page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACING.xxl,
            SPACING.xxl,
            SPACING.xxl,
            SPACING.xxl,
        )
        layout.setSpacing(SPACING.xl)

        eyebrow = QLabel("FILEPILOT")
        eyebrow.setProperty("role", "eyebrow")
        heading = QLabel(title)
        heading.setProperty("role", "pageTitle")
        subtitle = QLabel(description)
        subtitle.setProperty("role", "secondary")
        subtitle.setWordWrap(True)
        layout.addWidget(eyebrow)
        layout.addWidget(heading)
        layout.addWidget(subtitle)

        card = SectionCard(elevated=True)
        empty_row = QHBoxLayout()
        empty_row.setSpacing(SPACING.xl)
        icon_label = QLabel()
        icon_label.setPixmap(icon.pixmap(40, 40))
        icon_label.setAccessibleName(f"{title} workspace icon")
        empty_copy = QVBoxLayout()
        empty_copy.setSpacing(SPACING.sm)
        state_label = QLabel("Planned workspace")
        state_label.setProperty("badgeTone", "info")
        state_label.setMaximumWidth(state_label.sizeHint().width() + SPACING.lg)
        title_label = QLabel(f"{title} is coming to the Qt experience")
        title_label.setProperty("role", "sectionTitle")
        body = QLabel(
            "This area is intentionally reserved for a later migration patch. "
            "The legacy FilePilot interface remains available for this workflow."
        )
        body.setProperty("role", "secondary")
        body.setWordWrap(True)
        empty_copy.addWidget(state_label)
        empty_copy.addWidget(title_label)
        empty_copy.addWidget(body)
        empty_row.addWidget(icon_label, 0)
        empty_row.addLayout(empty_copy, 1)
        card.content_layout.addLayout(empty_row)
        layout.addWidget(card)
        layout.addStretch(1)
