from app.ui.qt.theme.tokens import COLORS, RADII


def build_stylesheet() -> str:
    """Build the complete Qt stylesheet from centralized design tokens."""
    c = COLORS
    r = RADII
    return f"""
        QMainWindow,
        QWidget#AppRoot,
        QStackedWidget#PageStack,
        QWidget#OverviewPage,
        QScrollArea#OverviewScrollArea,
        QWidget#OverviewViewport,
        QWidget#OverviewContent,
        QWidget[pageSurface="true"] {{
            background-color: {c.background};
        }}
        QWidget {{
            color: {c.text_primary};
            font-size: 14px;
        }}
        QFrame#Sidebar {{
            background-color: {c.sidebar};
            border-right: 1px solid {c.border};
        }}
        QLabel[brandMark="true"] {{
            background-color: {c.surface_elevated};
            border: 1px solid {c.border_strong};
            border-radius: {r.card}px;
        }}
        QLabel[role="brand"] {{
            color: {c.text_primary};
            font-size: 18px;
            font-weight: 700;
        }}
        QLabel[role="pageTitle"] {{
            color: {c.text_primary};
            font-size: 26px;
            font-weight: 700;
        }}
        QLabel[role="headline"] {{
            color: {c.text_primary};
            font-size: 18px;
            font-weight: 650;
        }}
        QLabel[role="sectionTitle"] {{
            color: {c.text_primary};
            font-size: 16px;
            font-weight: 650;
        }}
        QLabel[role="body"] {{
            color: {c.text_primary};
            font-size: 14px;
        }}
        QLabel[role="secondary"] {{
            color: {c.text_secondary};
            font-size: 14px;
        }}
        QLabel[role="caption"] {{
            color: {c.text_muted};
            font-size: 12px;
        }}
        QLabel[role="metric"] {{
            color: {c.text_primary};
            font-size: 20px;
            font-weight: 700;
        }}
        QLabel[role="eyebrow"] {{
            color: {c.info};
            font-size: 12px;
            font-weight: 650;
        }}
        QFrame[card="true"] {{
            background-color: {c.surface};
            border: 1px solid {c.border};
            border-radius: {r.card}px;
        }}
        QFrame[elevated="true"] {{
            background-color: {c.surface_elevated};
            border: 1px solid {c.border};
            border-radius: {r.panel}px;
        }}
        QFrame[divider="true"] {{
            background-color: {c.border};
            min-height: 1px;
            max-height: 1px;
        }}
        QPushButton {{
            min-height: 38px;
            padding: 0 16px;
            border-radius: {r.control}px;
            border: 1px solid {c.border_strong};
            background-color: {c.surface_elevated};
            color: {c.text_primary};
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: {c.surface_hover};
            border-color: {c.border_strong};
        }}
        QPushButton:focus {{
            border: 2px solid {c.focus};
        }}
        QPushButton:disabled {{
            color: {c.text_muted};
            background-color: {c.surface};
            border-color: {c.border};
        }}
        QPushButton[variant="primary"] {{
            color: #FFFFFF;
            background-color: {c.primary};
            border-color: {c.primary};
        }}
        QPushButton[variant="primary"]:hover {{
            background-color: {c.primary_hover};
            border-color: {c.primary_hover};
        }}
        QPushButton[variant="primary"]:pressed {{
            background-color: {c.primary_pressed};
            border-color: {c.primary_pressed};
        }}
        QPushButton[variant="primary"]:disabled {{
            color: {c.text_muted};
            background-color: {c.surface};
            border-color: {c.border};
        }}
        QPushButton[variant="destructive"] {{
            color: {c.error};
            background-color: {c.surface};
            border-color: {c.error};
        }}
        QPushButton[navItem="true"] {{
            min-height: 40px;
            padding: 0 12px;
            text-align: left;
            border: 1px solid transparent;
            background-color: transparent;
            color: {c.text_secondary};
            font-weight: 600;
        }}
        QPushButton[navItem="true"]:hover {{
            background-color: {c.surface_hover};
            color: {c.text_primary};
        }}
        QPushButton[navItem="true"]:checked {{
            background-color: {c.surface_elevated};
            color: {c.primary_hover};
            border: 1px solid {c.border};
            border-left: 3px solid {c.primary};
        }}
        QPushButton[navItem="true"][compactNav="true"] {{
            padding: 0;
            text-align: center;
        }}
        QPushButton[navItem="true"]:focus {{
            border: 2px solid {c.focus};
        }}
        QFrame[statusTone="neutral"] {{
            background-color: {c.text_muted};
            border-radius: 5px;
        }}
        QFrame[statusTone="success"] {{
            background-color: {c.success};
            border-radius: 5px;
        }}
        QFrame[statusTone="warning"] {{
            background-color: {c.warning};
            border-radius: 5px;
        }}
        QFrame[statusTone="error"] {{
            background-color: {c.error};
            border-radius: 5px;
        }}
        QFrame[statusTone="info"] {{
            background-color: {c.info};
            border-radius: 5px;
        }}
        QLabel[badgeTone="neutral"] {{
            color: {c.text_secondary};
            background-color: {c.surface_elevated};
            border: 1px solid {c.border};
            border-radius: 6px;
            padding: 4px 8px;
            font-size: 12px;
            font-weight: 650;
        }}
        QLabel[badgeTone="success"] {{
            color: {c.success};
            background-color: {c.surface_elevated};
            border: 1px solid {c.success};
            border-radius: 6px;
            padding: 4px 8px;
            font-size: 12px;
            font-weight: 650;
        }}
        QLabel[badgeTone="warning"] {{
            color: {c.warning};
            background-color: {c.surface_elevated};
            border: 1px solid {c.warning};
            border-radius: 6px;
            padding: 4px 8px;
            font-size: 12px;
            font-weight: 650;
        }}
        QLabel[badgeTone="error"] {{
            color: {c.error};
            background-color: {c.surface_elevated};
            border: 1px solid {c.error};
            border-radius: 6px;
            padding: 4px 8px;
            font-size: 12px;
            font-weight: 650;
        }}
        QLabel[badgeTone="info"] {{
            color: {c.info};
            background-color: {c.surface_elevated};
            border: 1px solid {c.info};
            border-radius: 6px;
            padding: 4px 8px;
            font-size: 12px;
            font-weight: 650;
        }}
        QScrollArea {{
            border: none;
        }}
        QScrollArea#OverviewScrollArea > QWidget > QWidget {{
            background-color: {c.background};
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {c.border_strong};
            border-radius: 5px;
            min-height: 28px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QToolTip {{
            color: {c.text_primary};
            background-color: {c.surface_elevated};
            border: 1px solid {c.border_strong};
            padding: 5px;
        }}
    """
