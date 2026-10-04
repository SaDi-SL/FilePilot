from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from app.application_service import FilePilotService
from app.product_identity import PRODUCT_IDENTITY
from app.ui.qt.main_window import MainWindow
from app.ui.qt.service_bridge import QtServiceBridge
from app.ui.qt.theme import build_palette, build_stylesheet
from app.ui.qt.theme.tokens import FONT_FAMILY, FONT_POINT_SIZE


def create_application(
    argv: list[str] | None = None,
) -> QApplication:
    application = QApplication.instance()
    if application is None:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        application = QApplication(argv or sys.argv)
    application.setApplicationName(PRODUCT_IDENTITY.product_name)
    application.setApplicationVersion(PRODUCT_IDENTITY.version)
    application.setOrganizationName(PRODUCT_IDENTITY.product_name)
    application.setStyle("Fusion")
    application.setFont(QFont(FONT_FAMILY, FONT_POINT_SIZE))
    application.setPalette(build_palette())
    application.setStyleSheet(build_stylesheet())

    icon_path = Path(__file__).resolve().parents[3] / "icon.ico"
    if icon_path.is_file():
        application.setWindowIcon(QIcon(str(icon_path)))
    return application


def run_qt(service: FilePilotService | None = None) -> int:
    """Launch the independent Qt development UI."""
    application = create_application()
    bridge = QtServiceBridge(service=service)
    window = MainWindow(bridge)
    window.center_on_primary_screen()
    window.show()
    bridge.bootstrap()

    try:
        return application.exec()
    finally:
        bridge.wait_for_shutdown()
