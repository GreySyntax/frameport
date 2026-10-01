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

# spacing / shape / type (at 100 %; set_scale() multiplies them)
_BASE = {"S1": 4, "S2": 8, "S3": 12, "S4": 16, "S5": 24, "S6": 32, "RADIUS": 12, "RADIUS_SM": 8,
         "T_TITLE": 28, "T_H2": 16, "T_BODY": 13, "T_META": 12, "T_SMALL": 11}
S1, S2, S3, S4, S5, S6 = 4, 8, 12, 16, 24, 32
RADIUS = 12
RADIUS_SM = 8
T_TITLE, T_H2, T_BODY, T_META, T_SMALL = 28, 16, 13, 12, 11
SCALE = 1.0


def set_scale(factor: float) -> None:
    """UI scale (1.0 = 100 %): every size token and every px() value. Call before the views are built."""
    global SCALE
    SCALE = max(0.75, min(3.0, float(factor or 1.0)))
    for name, value in _BASE.items():
        globals()[name] = round(value * SCALE)


SCALE_CHOICES = (1.0, 1.25, 1.5, 1.75, 2.0)


def detect_scale() -> float:
    """The display scaling the GUI should use by itself. Native Windows/macOS: 1.0 (Flutter applies the OS scaling).
    Under WSL the Linux window gets none of Windows' scaling, so follow Windows' setting halfway (AppliedDPI 144 =
    150 % -> 125 %); plain Linux: GDK_SCALE if set."""
    import os

    from ..core import winhost

    if winhost.is_wsl():
        try:
            dpi = int(winhost.reg_query(r"HKCU\Control Panel\Desktop\WindowMetrics", "AppliedDPI") or 0, 0)
        except (ValueError, TypeError, OSError):
            dpi = 0
        # half of Windows' extra scaling (150 % -> 125 %): the full factor made the app too large
        return 1.0 + (dpi / 96 - 1.0) / 2 if dpi > 96 else 1.0
    if not winhost.is_windows() and os.environ.get("GDK_SCALE", "").isdigit():
        return 1.0  # GTK already scales the window by GDK_SCALE
    return 1.0


def scale_from_setting(value) -> float:
    """Library setting ui.scale: "auto" (or missing) → detect_scale(), else a factor like 1.5."""
    if value in (None, "", "auto"):
        return detect_scale()
    try:
        return float(value)
    except (TypeError, ValueError):
        return detect_scale()


def px(value: float) -> float:
    """A size in logical pixels at 100 %, scaled to the current UI scale."""
    return round(value * SCALE) if isinstance(value, int) else value * SCALE


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
    # Material's default type scale (buttons = label_large, inputs/menus = body_large …) at the UI scale, for every
    # control that doesn't set its own size
    sizes = {"display_large": 57, "display_medium": 45, "display_small": 36, "headline_large": 32,
             "headline_medium": 28, "headline_small": 24, "title_large": 22, "title_medium": 16, "title_small": 14,
             "body_large": 16, "body_medium": 14, "body_small": 12, "label_large": 14, "label_medium": 12,
             "label_small": 11}
    text_theme = ft.TextTheme(**{k: ft.TextStyle(size=px(v)) for k, v in sizes.items()})
    theme = ft.Theme(color_scheme=scheme, visual_density=ft.VisualDensity.COMFORTABLE, use_material3=True,
                     text_theme=text_theme,
                     divider_theme=ft.DividerTheme(color=BORDER, thickness=1, space=1),
                     tooltip_theme=ft.TooltipTheme(
                         decoration=ft.BoxDecoration(bgcolor=SURFACE_3, border_radius=6,
                                                     border=ft.Border.all(1, BORDER)),
                         text_style=ft.TextStyle(color=TEXT, size=px(12)), wait_duration=400),
                     expansion_tile_theme=ft.ExpansionTileTheme(
                         shape=ft.RoundedRectangleBorder(radius=RADIUS), collapsed_shape=ft.RoundedRectangleBorder(
                             radius=RADIUS), icon_color=TEXT_2, collapsed_icon_color=TEXT_3, text_color=TEXT,
                         collapsed_text_color=TEXT),
                     scaffold_bgcolor=BG, card_bgcolor=SURFACE, canvas_color=SURFACE_2)
    page.theme = page.dark_theme = theme
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = BG
