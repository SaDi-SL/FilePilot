from dataclasses import dataclass


@dataclass(frozen=True)
class ColorTokens:
    background: str = "#17161B"
    sidebar: str = "#111014"
    surface: str = "#222126"
    surface_elevated: str = "#2A2830"
    surface_hover: str = "#343039"
    border: str = "#38343F"
    border_strong: str = "#514A5D"
    primary: str = "#8854EB"
    primary_hover: str = "#A477FF"
    primary_pressed: str = "#7040CD"
    text_primary: str = "#F6F3FB"
    text_secondary: str = "#C0B9CC"
    text_muted: str = "#A099AD"
    success: str = "#42C58A"
    warning: str = "#E3AA52"
    error: str = "#E26974"
    info: str = "#B494F6"
    accent_warm: str = "#FFA752"
    accent_cool: str = "#65CFC8"
    primary_deep: str = "#6035BB"
    focus: str = "#C7A8FF"


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
    control: int = 8
    card: int = 12
    panel: int = 14


COLORS = ColorTokens()
SPACING = SpacingTokens()
RADII = RadiusTokens()

FONT_FAMILY = "Segoe UI"
FONT_POINT_SIZE = 10
