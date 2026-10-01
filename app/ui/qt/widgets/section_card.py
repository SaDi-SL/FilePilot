from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from app.ui.qt.theme.tokens import SPACING


class SectionCard(QFrame):
    def __init__(self, parent: QWidget | None = None, *, elevated: bool = False):
        super().__init__(parent)
        self.setProperty("card", not elevated)
        self.setProperty("elevated", elevated)
        self.content_layout = QVBoxLayout(self)
        self.content_layout.setContentsMargins(
            SPACING.lg,
            SPACING.lg,
            SPACING.lg,
            SPACING.lg,
        )
        self.content_layout.setSpacing(SPACING.md)
