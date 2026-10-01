from dataclasses import dataclass


@dataclass(frozen=True)
class ColorTokens:
    background: str = "#0D1117"
    sidebar: str = "#101722"
    surface: str = "#151C27"
    surface_elevated: str = "#1B2431"
    surface_hover: str = "#202B3A"
    border: str = "#2A3545"
    border_strong: str = "#3A4B61"
    primary: str = "#4C8DFF"
    primary_hover: str = "#66A0FF"
    primary_pressed: str = "#3978E5"
    text_primary: str = "#F4F7FB"
    text_secondary: str = "#A8B3C2"
    text_muted: str = "#7F8A99"
    success: str = "#42C58A"
    warning: str = "#E3AA52"
    error: str = "#E26974"
    info: str = "#72A7FF"
    focus: str = "#8BB6FF"


@dataclass(frozen=True)
class SpacingTokens:
    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16
    xl: int = 24
    xxl: int = 32


@dataclass(frozen=True)
class RadiusTokens:
    control: int = 6
    card: int = 8
    panel: int = 10


COLORS = ColorTokens()
SPACING = SpacingTokens()
RADII = RadiusTokens()

FONT_FAMILY = "Segoe UI"
FONT_POINT_SIZE = 10
