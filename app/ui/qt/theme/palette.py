from PySide6.QtGui import QColor, QPalette

from app.ui.qt.theme.tokens import COLORS


def build_palette() -> QPalette:
    """Provide dark fallback brushes for widgets not painted by QSS."""
    palette = QPalette()
    brushes = {
        QPalette.ColorRole.Window: COLORS.background,
        QPalette.ColorRole.WindowText: COLORS.text_primary,
        QPalette.ColorRole.Base: COLORS.background,
        QPalette.ColorRole.AlternateBase: COLORS.surface,
        QPalette.ColorRole.ToolTipBase: COLORS.surface_elevated,
        QPalette.ColorRole.ToolTipText: COLORS.text_primary,
        QPalette.ColorRole.Text: COLORS.text_primary,
        QPalette.ColorRole.Button: COLORS.surface_elevated,
        QPalette.ColorRole.ButtonText: COLORS.text_primary,
        QPalette.ColorRole.BrightText: COLORS.error,
        QPalette.ColorRole.Highlight: COLORS.primary,
        QPalette.ColorRole.HighlightedText: COLORS.text_primary,
        QPalette.ColorRole.PlaceholderText: COLORS.text_muted,
    }
    for role, color in brushes.items():
        palette.setColor(role, QColor(color))
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Text,
        QColor(COLORS.text_muted),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor(COLORS.text_muted),
    )
    return palette
