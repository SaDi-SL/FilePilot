from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QLinearGradient

from app.ui.qt.theme.tokens import COLORS
from app.application_paths import get_application_paths


def navigation_icon(name: str) -> QIcon:
    """Return a deterministic, DPI-ready icon drawn with Qt primitives."""
    icon = QIcon()
    states = (
        (QIcon.Mode.Normal, QIcon.State.Off, COLORS.text_secondary),
        (QIcon.Mode.Active, QIcon.State.Off, COLORS.text_primary),
        (QIcon.Mode.Normal, QIcon.State.On, COLORS.primary_hover),
        (QIcon.Mode.Selected, QIcon.State.On, COLORS.primary_hover),
        (QIcon.Mode.Disabled, QIcon.State.Off, COLORS.text_muted),
    )
    for size in (18, 20, 24, 32):
        for mode, state, color in states:
            icon.addPixmap(_draw_icon(name, size, color), mode, state)
    return icon


def brand_icon() -> QIcon:
    """Use the same multi-resolution mark as the Windows app and installer."""
    path = get_application_paths().resource("icon.ico")
    icon = QIcon(str(path)) if path.is_file() else QIcon()
    return icon if not icon.isNull() else QIcon(_draw_icon("brand", 32, "#FFFFFF"))


def folder_art(size: int = 72) -> QPixmap:
    """Paint a warm folder illustration without external image dependencies."""
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size * 2 / 80, size * 2 / 80)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#C57029"))
    painter.drawRoundedRect(QRectF(9, 20, 28, 20), 5, 5)
    painter.setBrush(QColor("#E8923C"))
    painter.drawRoundedRect(QRectF(9, 26, 62, 40), 6, 6)
    painter.setBrush(QColor("#DED7EE"))
    painter.drawRoundedRect(QRectF(16, 23, 47, 34), 3, 3)
    painter.setBrush(QColor("#F8F4FF"))
    painter.drawRoundedRect(QRectF(20, 28, 45, 30), 3, 3)
    gradient = QLinearGradient(0, 36, 0, 68)
    gradient.setColorAt(0, QColor("#FFBA70"))
    gradient.setColorAt(1, QColor("#F28B32"))
    painter.setBrush(gradient)
    painter.drawRoundedRect(QRectF(9, 35, 62, 33), 6, 6)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return pixmap


def status_icon(color: str, size: int = 12) -> QIcon:
    ratio = 2
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    inset = 3 * ratio
    diameter = (size * ratio) - (inset * 2)
    painter.drawEllipse(QRectF(inset, inset, diameter, diameter))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return QIcon(pixmap)


def _draw_icon(name: str, size: int, color: str) -> QPixmap:
    ratio = 2
    pixmap = QPixmap(size * ratio, size * ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale((size * ratio) / 24.0, (size * ratio) / 24.0)
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    _paint_symbol(painter, name)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def _paint_symbol(painter: QPainter, name: str) -> None:
    if name == "overview":
        painter.drawRoundedRect(QRectF(3, 4, 18, 14), 2, 2)
        painter.drawLine(QPointF(8, 21), QPointF(16, 21))
        painter.drawLine(QPointF(12, 18), QPointF(12, 21))
        painter.drawLine(QPointF(7, 9), QPointF(10, 12))
        painter.drawLine(QPointF(10, 12), QPointF(15.5, 7.5))
    elif name == "activity":
        painter.drawEllipse(QRectF(3.5, 3.5, 17, 17))
        painter.drawLine(QPointF(12, 7), QPointF(12, 12))
        painter.drawLine(QPointF(12, 12), QPointF(16, 14))
        painter.drawLine(QPointF(5, 4), QPointF(3, 7))
    elif name == "recovery":
        shield = QPainterPath()
        shield.moveTo(12, 3)
        shield.lineTo(19, 6)
        shield.lineTo(18, 14)
        shield.cubicTo(17, 18, 14, 20, 12, 21)
        shield.cubicTo(10, 20, 7, 18, 6, 14)
        shield.lineTo(5, 6)
        shield.closeSubpath()
        painter.drawPath(shield)
        painter.drawLine(QPointF(8.5, 12), QPointF(11, 14.5))
        painter.drawLine(QPointF(11, 14.5), QPointF(15.5, 9.5))
    elif name == "rules":
        for y, x in ((6, 8), (12, 15), (18, 10)):
            painter.drawLine(QPointF(3, y), QPointF(21, y))
            painter.drawEllipse(QRectF(x - 2, y - 2, 4, 4))
    elif name == "folders":
        path = QPainterPath()
        path.moveTo(3, 7)
        path.lineTo(9, 7)
        path.lineTo(11, 9)
        path.lineTo(21, 9)
        path.lineTo(20, 19)
        path.lineTo(4, 19)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QPointF(3, 7), QPointF(3, 18))
    elif name == "integrations":
        painter.drawLine(QPointF(7, 7), QPointF(17, 7))
        painter.drawLine(QPointF(7, 7), QPointF(12, 17))
        painter.drawLine(QPointF(17, 7), QPointF(12, 17))
        for x, y in ((7, 7), (17, 7), (12, 17)):
            painter.drawEllipse(QRectF(x - 2.5, y - 2.5, 5, 5))
    elif name == "settings":
        painter.drawEllipse(QRectF(8, 8, 8, 8))
        painter.drawEllipse(QRectF(11, 11, 2, 2))
        for index in range(8):
            angle = math.radians(index * 45)
            inner = QPointF(12 + math.cos(angle) * 6, 12 + math.sin(angle) * 6)
            outer = QPointF(12 + math.cos(angle) * 9, 12 + math.sin(angle) * 9)
            painter.drawLine(inner, outer)
    elif name == "brand":
        painter.drawRoundedRect(QRectF(4, 3, 13, 18), 2, 2)
        painter.drawLine(QPointF(8, 8), QPointF(14, 8))
        painter.drawLine(QPointF(8, 12), QPointF(14, 12))
        painter.drawLine(QPointF(8, 16), QPointF(11, 16))
        painter.drawLine(QPointF(16, 15), QPointF(21, 10))
        painter.drawLine(QPointF(21, 10), QPointF(21, 14))
        painter.drawLine(QPointF(21, 10), QPointF(17, 10))
