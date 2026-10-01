from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from app.branding import APP_NAME, APP_VERSION
from app.ui.qt.icons import navigation_icon
from app.ui.qt.navigation import NAVIGATION_ITEMS, NavigationSidebar
from app.ui.qt.pages.activity import ActivityPage
from app.ui.qt.pages.overview import OverviewPage
from app.ui.qt.pages.placeholder import PlaceholderPage
from app.ui.qt.service_bridge import QtServiceBridge


PLACEHOLDER_COPY = {
    "activity": "Review authoritative operation history, outcomes, and recovery cases.",
    "rules": "Define how FilePilot classifies and organizes incoming files.",
    "folders": "Manage the folders FilePilot watches for new files.",
    "integrations": "Connect FilePilot with supported services and extensions.",
    "settings": "Configure application behavior, appearance, and automation.",
}


class MainWindow(QMainWindow):
    COMPACT_THRESHOLD = 1080

    def __init__(
        self,
        bridge: QtServiceBridge,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._allow_close = False
        self._shutdown_requested = False
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setAccessibleName("FilePilot main window")
        self.resize(1180, 680)
        self.setMinimumSize(760, 520)

        root = QWidget()
        root.setObjectName("AppRoot")
        root.setAutoFillBackground(True)
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.navigation = NavigationSidebar()
        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("PageStack")
        self.page_stack.setAccessibleName("FilePilot workspace")
        layout.addWidget(self.navigation)
        layout.addWidget(self.page_stack, 1)

        self._page_indexes: dict[str, int] = {}
        overview = OverviewPage(self.bridge)
        self._page_indexes["overview"] = self.page_stack.addWidget(overview)
        self.overview_page = overview

        activity = ActivityPage(self.bridge)
        self._page_indexes["activity"] = self.page_stack.addWidget(activity)
        self.activity_page = activity

        for item in NAVIGATION_ITEMS[2:]:
            page = PlaceholderPage(
                item.label,
                PLACEHOLDER_COPY[item.key],
                navigation_icon(item.icon),
            )
            self._page_indexes[item.key] = self.page_stack.addWidget(page)

        self.navigation.page_selected.connect(self.show_page)
        self.overview_page.view_all_requested.connect(
            lambda: self.show_page("activity")
        )
        self.bridge.closed.connect(self._finish_close)
        self.bridge.command_failed.connect(self._handle_command_failure)
        self.show_page("overview")

    def center_on_primary_screen(self) -> None:
        screen = self.screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.resize(
            min(self.width(), max(self.minimumWidth(), int(available.width() * 0.94))),
            min(self.height(), max(self.minimumHeight(), int(available.height() * 0.90))),
        )
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def show_page(self, key: str) -> None:
        index = self._page_indexes.get(key)
        if index is None:
            return
        self.page_stack.setCurrentIndex(index)
        self.navigation.select(key)
        if key == "activity":
            self.bridge.request_product_refresh(100)

    def resizeEvent(self, event) -> None:
        self.navigation.set_compact(event.size().width() < self.COMPACT_THRESHOLD)
        super().resizeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            event.accept()
            return
        event.ignore()
        if not self._shutdown_requested:
            self._shutdown_requested = True
            self.setEnabled(False)
            self.bridge.shutdown()

    def _finish_close(self) -> None:
        self._allow_close = True
        self.close()

    def _handle_command_failure(self, command: str, _message: str) -> None:
        if command == "shutdown":
            self._shutdown_requested = False
            self.setEnabled(True)
