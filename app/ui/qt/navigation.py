from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.product_identity import PRODUCT_IDENTITY
from app.ui.qt.icons import brand_icon, navigation_icon
from app.ui.qt.theme.tokens import SPACING


@dataclass(frozen=True)
class NavigationItem:
    key: str
    label: str
    icon: str


NAVIGATION_ITEMS = (
    NavigationItem("overview", "Overview", "overview"),
    NavigationItem("activity", "Activity", "activity"),
    NavigationItem("recovery", "Recovery", "recovery"),
    NavigationItem("rules", "Rules", "rules"),
    NavigationItem("folders", "Folders", "folders"),
    NavigationItem("settings", "Settings", "settings"),
)


class NavigationSidebar(QFrame):
    page_selected = Signal(str)

    EXPANDED_WIDTH = 240
    COMPACT_WIDTH = 72

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAccessibleName("Primary navigation")
        self._compact = False
        self._buttons: dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACING.md,
            SPACING.xl,
            SPACING.md,
            SPACING.lg,
        )
        layout.setSpacing(SPACING.sm)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(SPACING.md)
        self.brand_mark = QLabel()
        self.brand_mark.setProperty("brandMark", True)
        self.brand_mark.setFixedSize(38, 38)
        self.brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_mark.setPixmap(brand_icon().pixmap(22, 22))
        self.brand_mark.setAccessibleName("FilePilot application mark")
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(1)
        self.brand_name = QLabel(PRODUCT_IDENTITY.product_name)
        self.brand_name.setProperty("role", "brand")
        self.brand_tagline = QLabel("Desktop file automation")
        self.brand_tagline.setProperty("role", "caption")
        brand_copy.addWidget(self.brand_name)
        brand_copy.addWidget(self.brand_tagline)
        brand_row.addWidget(self.brand_mark)
        brand_row.addLayout(brand_copy, 1)
        layout.addLayout(brand_row)
        layout.addSpacing(SPACING.md)

        divider = QFrame()
        divider.setProperty("divider", True)
        layout.addWidget(divider)
        layout.addSpacing(SPACING.sm)

        self.section_label = QLabel("WORKSPACE")
        self.section_label.setProperty("role", "caption")
        layout.addWidget(self.section_label)
        layout.addSpacing(SPACING.xs)

        group = QButtonGroup(self)
        group.setExclusive(True)
        for index, item in enumerate(NAVIGATION_ITEMS):
            button = QPushButton(item.label)
            button.setProperty("navItem", True)
            button.setCheckable(True)
            button.setIcon(navigation_icon(item.icon))
            button.setIconSize(QSize(18, 18))
            button.setToolTip(item.label)
            button.setAccessibleName(f"Open {item.label}")
            button.clicked.connect(
                lambda checked=False, key=item.key: self.page_selected.emit(key)
            )
            group.addButton(button)
            layout.addWidget(button)
            self._buttons[item.key] = button
            if index > 0:
                QWidget.setTabOrder(
                    self._buttons[NAVIGATION_ITEMS[index - 1].key],
                    button,
                )

        layout.addStretch(1)
        self.version_label = QLabel(
            f"{PRODUCT_IDENTITY.product_name} {PRODUCT_IDENTITY.display_version}"
        )
        self.version_label.setProperty("role", "caption")
        layout.addWidget(self.version_label)

        self._buttons["overview"].setChecked(True)
        self.setFixedWidth(self.EXPANDED_WIDTH)

    def select(self, key: str) -> None:
        button = self._buttons.get(key)
        if button is not None:
            button.setChecked(True)

    def button(self, key: str) -> QPushButton:
        return self._buttons[key]

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self.setFixedWidth(
            self.COMPACT_WIDTH if compact else self.EXPANDED_WIDTH
        )
        self.brand_name.setVisible(not compact)
        self.brand_tagline.setVisible(not compact)
        self.section_label.setVisible(not compact)
        self.version_label.setVisible(not compact)
        for item in NAVIGATION_ITEMS:
            button = self._buttons[item.key]
            button.setProperty("compactNav", compact)
            button.setText("" if compact else item.label)
            button.setToolTip(item.label if compact else "")
            style = button.style()
            style.unpolish(button)
            style.polish(button)
