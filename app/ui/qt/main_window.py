from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from app.product_identity import PRODUCT_IDENTITY
from app.ui.qt.icons import navigation_icon
from app.ui.qt.navigation import NAVIGATION_ITEMS, NavigationSidebar
from app.ui.qt.pages.activity import ActivityPage
from app.ui.qt.pages.folders import FoldersPage
from app.ui.qt.pages.my_files import MyFilesPage
from app.ui.qt.pages.overview import OverviewPage
from app.ui.qt.pages.placeholder import PlaceholderPage
from app.ui.qt.pages.recovery import RecoveryPage
from app.ui.qt.pages.rules import RulesPage
from app.ui.qt.pages.settings import SettingsPage
from app.ui.qt.service_bridge import QtServiceBridge
from app.ui.qt.setup_dialog import SetupDialog
from app.application_service import StartupStatus
from app.product_configuration import ConfigurationDataState


PLACEHOLDER_COPY = {
    "activity": "Review authoritative operation history, outcomes, and recovery cases.",
    "recovery": "Review interrupted operations using verified recovery evidence.",
    "rules": "Define how FilePilot classifies and organizes incoming files.",
    "folders": "Manage the folders FilePilot watches for new files.",
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
        self._setup_dialog = None
        self._setup_auto_opened = False
        self.setWindowTitle(
            f"{PRODUCT_IDENTITY.product_name} {PRODUCT_IDENTITY.display_version}"
        )
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

        my_files = MyFilesPage(self.bridge)
        self._page_indexes["my_files"] = self.page_stack.addWidget(my_files)
        self.my_files_page = my_files

        activity = ActivityPage(self.bridge)
        self._page_indexes["activity"] = self.page_stack.addWidget(activity)
        self.activity_page = activity

        rules = RulesPage(self.bridge)
        self._page_indexes["rules"] = self.page_stack.addWidget(rules)
        self.rules_page = rules

        folders = FoldersPage(self.bridge)
        self._page_indexes["folders"] = self.page_stack.addWidget(folders)
        self.folders_page = folders

        settings = SettingsPage(self.bridge)
        self._page_indexes["settings"] = self.page_stack.addWidget(settings)
        self.settings_page = settings

        if hasattr(self.bridge, "recovery_snapshot_changed"):
            recovery = RecoveryPage(self.bridge)
            self._page_indexes["recovery"] = self.page_stack.addWidget(recovery)
            self.recovery_page = recovery

        for item in NAVIGATION_ITEMS:
            if item.key in self._page_indexes:
                continue
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
        self.overview_page.recovery_requested.connect(
            lambda: self.show_page("recovery")
        )
        self.overview_page.setup_requested.connect(self.open_setup)
        self.bridge.state_changed.connect(self._offer_setup)
        configuration_signal = getattr(self.bridge, "configuration_snapshot_changed", None)
        if configuration_signal is not None:
            configuration_signal.connect(self._offer_setup)
        self.bridge.closed.connect(self._finish_close)
        self.bridge.command_failed.connect(self._handle_command_failure)
        self.show_page("overview")

    def open_setup(self):
        if self._shutdown_requested or self._allow_close:
            return
        if self._setup_dialog is not None:
            self._setup_dialog.raise_()
            self._setup_dialog.activateWindow()
            return
        if not hasattr(self.bridge, "configuration_snapshot_changed"):
            return
        self._setup_dialog = SetupDialog(self.bridge, self)
        self._setup_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._setup_dialog.finished.connect(self._setup_closed)
        self._setup_dialog.index_requested.connect(self._open_index)
        self._setup_dialog.open()

    def _setup_closed(self, _result):
        self._setup_dialog = None

    def _open_index(self):
        self.show_page("my_files")
        self.my_files_page.workspace_tabs.setCurrentIndex(1)

    def _offer_setup(self, *_args):
        config = getattr(self.bridge, "configuration_snapshot", None)
        if (not self._setup_auto_opened and not self._shutdown_requested
                and self.bridge.snapshot.startup_status is StartupStatus.SETUP_REQUIRED
                and config is not None and config.state is ConfigurationDataState.AVAILABLE):
            self._setup_auto_opened = True
            QTimer.singleShot(0, self.open_setup)

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
        elif key == "recovery":
            request = getattr(self.bridge, "request_recovery_refresh", None)
            if request is not None:
                request(100)
        elif key in {"rules", "folders"}:
            request = getattr(self.bridge, "request_configuration_refresh", None)
            if request is not None:
                request()
        elif key == "settings":
            request = getattr(self.bridge, "request_settings_refresh", None)
            if request is not None:
                request()

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
