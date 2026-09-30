"""Design tokens for the FramePort GUI (dark only): one accent, a neutral surface ramp, semantic state colours, an
8-pt spacing scale and a small type scale. Every view takes its colours and sizes from here."""
from __future__ import annotations

import flet as ft

# surfaces (darkest → lightest)
BG = "#0E0F13"          # window background
SIDEBAR = "#121319"
SURFACE = "#171920"     # cards
SURFACE_2 = "#1E2029"   # raised / hover
SURFACE_3 = "#262935"   # inputs, chips
BORDER = "#2C2F3B"
BORDER_STRONG = "#3A3E4D"

# text
TEXT = "#ECEDF3"
TEXT_2 = "#A9ADBD"      # secondary
TEXT_3 = "#737889"      # meta / disabled

# accent + states
ACCENT = "#8B7CFF"
ACCENT_SOFT = "#2A2550"
ON_ACCENT = "#0E0F13"
OK = "#4ADE80"
WARN = "#FBBF24"
ERROR = "#F87171"
INFO = "#60A5FA"
PC = "#38BDF8"          # PC VR / "on this PC"

# spacing / shape / type
S1, S2, S3, S4, S5, S6 = 4, 8, 12, 16, 24, 32
RADIUS = 12
RADIUS_SM = 8
T_TITLE, T_H2, T_BODY, T_META, T_SMALL = 28, 16, 13, 12, 11


def soft(color: str, opacity: float = 0.14) -> str:
    return ft.Colors.with_opacity(opacity, color)


def apply(page: ft.Page) -> None:
    scheme = ft.ColorScheme(
        primary=ACCENT, on_primary=ON_ACCENT, primary_container=ACCENT_SOFT, on_primary_container=TEXT,
        secondary=PC, on_secondary=ON_ACCENT, surface=SURFACE, on_surface=TEXT, on_surface_variant=TEXT_2,
        surface_container_lowest=BG, surface_container_low=SIDEBAR, surface_container=SURFACE,
        surface_container_high=SURFACE_2, surface_container_highest=SURFACE_3, outline=BORDER_STRONG,
        outline_variant=BORDER, error=ERROR, on_error=ON_ACCENT,
    )
    theme = ft.Theme(color_scheme=scheme, visual_density=ft.VisualDensity.COMFORTABLE, use_material3=True,
                     divider_theme=ft.DividerTheme(color=BORDER, thickness=1, space=1),
                     tooltip_theme=ft.TooltipTheme(
                         decoration=ft.BoxDecoration(bgcolor=SURFACE_3, border_radius=6,
                                                     border=ft.Border.all(1, BORDER)),
                         text_style=ft.TextStyle(color=TEXT, size=12), wait_duration=400),
                     expansion_tile_theme=ft.ExpansionTileTheme(
                         shape=ft.RoundedRectangleBorder(radius=RADIUS), collapsed_shape=ft.RoundedRectangleBorder(
                             radius=RADIUS), icon_color=TEXT_2, collapsed_icon_color=TEXT_3, text_color=TEXT,
                         collapsed_text_color=TEXT),
                     scaffold_bgcolor=BG, card_bgcolor=SURFACE, canvas_color=SURFACE_2)
    page.theme = page.dark_theme = theme
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = BG
