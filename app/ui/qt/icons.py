from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from app.ui.qt.theme.tokens import COLORS


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
    return navigation_icon("brand")


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
