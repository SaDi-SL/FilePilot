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
        QWidget#ActivityPage,
        QWidget#RecoveryPage,
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
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {c.primary_hover}, stop:1 {c.primary_deep});
            border: 1px solid {c.primary_hover};
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
        QFrame[primaryPanel="true"] {{
            background-color: {c.surface_elevated};
            border: 1px solid {c.border_strong};
            border-left: 3px solid {c.primary};
            border-radius: {r.panel}px;
        }}
        QFrame[cardAccent="purple"] {{
            border-top: 2px solid {c.primary};
        }}
        QFrame[cardAccent="warm"] {{
            border-top: 2px solid {c.accent_warm};
        }}
        QFrame[cardAccent="cool"] {{
            border-top: 2px solid {c.accent_cool};
        }}
        QLabel[metricAccent="purple"] {{ color: {c.primary_hover}; }}
        QLabel[metricAccent="warm"] {{ color: {c.accent_warm}; }}
        QLabel[metricAccent="cool"] {{ color: {c.accent_cool}; }}
        QPlainTextEdit, QListWidget, QTreeWidget {{
            background-color: {c.background};
            border: 1px solid {c.border};
            border-radius: {r.control}px;
            padding: 6px;
            selection-background-color: #493564;
            selection-color: {c.text_primary};
        }}
        QListWidget::item {{ padding: 6px; border-radius: 5px; }}
        QListWidget::item:selected {{ background-color: #493564; }}
        QListWidget::item:hover {{ background-color: {c.surface_hover}; }}
        QScrollArea > QWidget > QWidget {{ background-color: {c.background}; }}
        QFrame[divider="true"] {{
            background-color: {c.border};
            min-height: 1px;
            max-height: 1px;
        }}
        QFrame[dropZone="true"] {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2B223A, stop:1 {c.background});
            border: 1px dashed {c.border_strong};
            border-radius: {r.panel}px;
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
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {c.primary}, stop:1 {c.primary_deep});
            border-color: {c.primary_hover};
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
        QPushButton[variant="quiet"] {{
            min-height: 30px;
            padding: 0 10px;
            color: {c.primary_hover};
            background-color: transparent;
            border-color: transparent;
        }}
        QPushButton[variant="quiet"]:hover {{
            background-color: {c.surface_hover};
            border-color: {c.border};
        }}
        QPushButton[navItem="true"] {{
            min-height: 38px;
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
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3B2A58, stop:1 {c.surface_elevated});
            color: #E8DBFF;
            border: 1px solid #574274;
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
        QTabWidget::pane {{ border: none; background: transparent; }}
        QTabBar::tab {{
            color: {c.text_secondary};
            background: {c.surface};
            border: 1px solid {c.border};
            border-radius: {r.control}px;
            padding: 10px 18px;
            margin-right: 8px;
        }}
        QTabBar::tab:selected {{
            color: #F5EDFF;
            background: #3B2A58;
            border-color: {c.primary};
        }}
        QTabBar::tab:hover {{ border-color: {c.primary_hover}; }}
        QScrollArea#OverviewScrollArea > QWidget > QWidget {{
            background-color: {c.background};
        }}
        QTreeWidget[activityTable="true"] {{
            background-color: {c.surface};
            alternate-background-color: {c.surface};
            border: 1px solid {c.border};
            border-radius: {r.card}px;
            outline: none;
            selection-background-color: {c.surface_hover};
            selection-color: {c.text_primary};
        }}
        QTreeWidget[activityTable="true"][compactActivity="true"] {{
            background-color: transparent;
            border: none;
            border-radius: 0;
        }}
        QTreeWidget[activityTable="true"]::item {{
            min-height: 34px;
            padding: 2px 6px;
            border-bottom: 1px solid {c.border};
        }}
        QTreeWidget[activityTable="true"]::item:hover {{
            background-color: {c.surface_hover};
        }}
        QTreeWidget[activityTable="true"]::item:selected {{
            background-color: {c.surface_hover};
            color: {c.text_primary};
        }}
        QHeaderView::section {{
            color: {c.text_secondary};
            background-color: {c.surface_elevated};
            border: none;
            border-bottom: 1px solid {c.border_strong};
            padding: 8px 6px;
            font-size: 12px;
            font-weight: 650;
        }}
        QComboBox, QDoubleSpinBox {{
            min-height: 36px;
            padding: 0 30px 0 10px;
            color: {c.text_primary};
            background-color: {c.surface_elevated};
            border: 1px solid {c.border_strong};
            border-radius: {r.control}px;
        }}
        QComboBox:hover, QComboBox:focus,
        QDoubleSpinBox:hover, QDoubleSpinBox:focus {{
            border-color: {c.focus};
        }}
        QComboBox QAbstractItemView {{
            color: {c.text_primary};
            background-color: {c.surface_elevated};
            border: 1px solid {c.border_strong};
            selection-background-color: {c.surface_hover};
        }}
        QDialog {{
            background-color: {c.background};
        }}
        QLineEdit {{
            min-height: 36px;
            padding: 0 10px;
            color: {c.text_primary};
            background-color: {c.surface_elevated};
            border: 1px solid {c.border_strong};
            border-radius: {r.control}px;
            selection-background-color: {c.primary};
        }}
        QLineEdit:hover, QLineEdit:focus {{
            border-color: {c.focus};
        }}
        QCheckBox {{
            spacing: 8px;
            color: {c.text_primary};
        }}
        QCheckBox:focus {{
            color: {c.primary_hover};
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
