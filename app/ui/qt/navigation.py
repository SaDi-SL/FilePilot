from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

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
    NavigationItem("my_files", "My Files", "folders"),
    NavigationItem("activity", "Activity", "activity"),
    NavigationItem("recovery", "Recovery", "recovery"),
    NavigationItem("rules", "Rules", "rules"),
    NavigationItem("folders", "Folders", "folders"),
    NavigationItem("settings", "Settings", "settings"),
)


class NavigationSidebar(QFrame):
    page_selected = Signal(str)
    EXPANDED_WIDTH = 216
    COMPACT_WIDTH = 68

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAccessibleName("Primary navigation")
        self._compact = False
        self._buttons: dict[str, QPushButton] = {}
        self._section_labels: list[QLabel] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING.md, SPACING.lg, SPACING.md, SPACING.md)
        layout.setSpacing(SPACING.xs)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(SPACING.md)
        self.brand_mark = QLabel()
        self.brand_mark.setProperty("brandMark", True)
        self.brand_mark.setFixedSize(36, 36)
        self.brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_mark.setPixmap(brand_icon().pixmap(36, 36))
        self.brand_mark.setAccessibleName("FilePilot application mark")
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(0)
        self.brand_name = QLabel(PRODUCT_IDENTITY.product_name)
        self.brand_name.setProperty("role", "brand")
        self.brand_tagline = QLabel("Files, under control")
        self.brand_tagline.setProperty("role", "caption")
        brand_copy.addWidget(self.brand_name)
        brand_copy.addWidget(self.brand_tagline)
        brand_row.addWidget(self.brand_mark)
        brand_row.addLayout(brand_copy, 1)
        layout.addLayout(brand_row)
        layout.addSpacing(SPACING.lg)

        group = QButtonGroup(self)
        group.setExclusive(True)
        sections = (
            ("WORKSPACE", NAVIGATION_ITEMS[:4]),
            ("AUTOMATION", NAVIGATION_ITEMS[4:6]),
            ("SYSTEM", NAVIGATION_ITEMS[6:]),
        )
        previous_button = None
        for section_index, (section_name, items) in enumerate(sections):
            if section_index:
                layout.addSpacing(SPACING.md)
            label = QLabel(section_name)
            label.setProperty("role", "caption")
            self._section_labels.append(label)
            layout.addWidget(label)
            layout.addSpacing(SPACING.xs)
            for item in items:
                button = QPushButton(item.label)
                button.setProperty("navItem", True)
                button.setCheckable(True)
                button.setIcon(navigation_icon(item.icon))
                button.setIconSize(QSize(18, 18))
                button.setToolTip(item.label)
                button.setAccessibleName(f"Open {item.label}")
                button.clicked.connect(lambda checked=False, key=item.key: self.page_selected.emit(key))
                group.addButton(button)
                layout.addWidget(button)
                self._buttons[item.key] = button
                if previous_button is not None:
                    QWidget.setTabOrder(previous_button, button)
                previous_button = button

        # Kept for compatibility with the original Qt shell tests.
        self.section_label = self._section_labels[0]
        layout.addStretch(1)
        self.version_label = QLabel(PRODUCT_IDENTITY.display_version)
        self.version_label.setProperty("role", "caption")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        self.setFixedWidth(self.COMPACT_WIDTH if compact else self.EXPANDED_WIDTH)
        self.brand_name.setVisible(not compact)
        self.brand_tagline.setVisible(not compact)
        for label in self._section_labels:
            label.setVisible(not compact)
        self.version_label.setVisible(not compact)
        for item in NAVIGATION_ITEMS:
            button = self._buttons[item.key]
            button.setProperty("compactNav", compact)
            button.setText("" if compact else item.label)
            button.setToolTip(item.label if compact else "")
            style = button.style()
            style.unpolish(button)
            style.polish(button)
