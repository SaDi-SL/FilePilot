from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from app.ui.qt.theme.tokens import SPACING


def install_editor_footer(root, feedback, actions):
    """Keep editor validation and actions outside the scrolling page content."""
    footer = QWidget()
    footer.setObjectName("EditorFooter")
    footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    layout = QVBoxLayout(footer)
    layout.setContentsMargins(SPACING.lg, SPACING.sm, SPACING.lg, SPACING.md)
    layout.setSpacing(SPACING.sm)
    feedback.setProperty("footerMessage", True)
    layout.addWidget(feedback)
    layout.addLayout(actions)
    root.addWidget(footer)
    return footer
