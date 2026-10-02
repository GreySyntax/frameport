"""'Find artwork' — search the Meta/Oculus store (via OculusDB) and Steam, pick a result, or fetch automatically."""
from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from ... import pipeline
from ...artwork import sources, thumbs
from ...core import library
from .. import components as C
from .. import theme as T

if TYPE_CHECKING:
    from ..app import FramePortApp


def show_art_dialog(app: FramePortApp, package: str) -> None:
    g = library.game(package)
    term = ft.TextField(value=g.get("title") or package, dense=True, expand=True, border_radius=T.RADIUS_SM,
                        bgcolor=T.SURFACE_3, border_color=ft.Colors.TRANSPARENT, focused_border_color=T.ACCENT,
                        content_padding=ft.Padding(T.px(12), T.px(8), T.px(12), T.px(8)), text_size=T.T_BODY)
    results = ft.GridView(max_extent=T.px(190), child_aspect_ratio=0.8, spacing=T.S3, run_spacing=T.S3, height=T.px(400))
    status = C.meta("")

    def pick(choice):
        app.page.pop_dialog()

        def work():
            if not sources.apply_choice(package, choice):
                app.toast(f"No artwork could be downloaded from that {choice['source']} result; the current artwork "
                          "stays. Try another one.", error=True)
                return
            thumbs.prewarm(package)
            library.upsert_game(package, art_source=choice["source"].lower())
            app.refresh_view()
            if C.install_state(library.game(package), app.frame_info) in ("installed", "outdated"):
                app.toast(f"Artwork updated for {g.get('title')}. The Frame's Steam library still shows the old "
                          "art.", action="Update on Frame", on_action=lambda e: app.update_steam_art(package))
            else:
                app.toast(f"Artwork updated for {g.get('title')}")
        app.run_bg(work)

    def search(e=None):
        status.value = "Searching…"
        results.controls = []
        C.update(status, results)

        def work():
            found = sources.search(term.value.strip())
            results.controls = [ft.Container(ft.Column([
                ft.Container(C.art_fill(r["preview"], radius=T.px(8), height=T.px(120)), height=T.px(120)),
                C.body(r["name"] or "", T.TEXT, size=T.T_META, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                C.meta(r["source"]),
            ], spacing=T.px(4)), padding=T.S2, border_radius=T.RADIUS_SM, bgcolor=T.SURFACE, ink=True,
                on_click=lambda e, r=r: pick(r)) for r in found]
            status.value = f"{len(found)} results" if found else "Nothing found. Try a shorter or different name."
            C.update(status, results)
        app.run_bg(work)

    def auto(e):
        app.page.pop_dialog()

        def work():
            found = pipeline.fetch_art(package)
            thumbs.prewarm(package)  # regenerate the thumbnails the cards/hero use, or the view shows the old art
            app.refresh_view()
            src = found.get("source")
            if src and src != "none" and C.install_state(library.game(package), app.frame_info) in ("installed",
                                                                                                    "outdated"):
                app.toast(f"Artwork updated ({src}). The Frame's Steam library still shows the old art.",
                          action="Update on Frame", on_action=lambda e: app.update_steam_art(package))
            else:
                app.toast(f"Artwork: {src or 'none found'}")
        app.run_bg(work)

    term.on_submit = search
    app.page.show_dialog(ft.AlertDialog(
        title=ft.Text(f"Artwork for {g.get('title')}", weight=ft.FontWeight.W_600),
        content=ft.Container(ft.Column([
            ft.Row([term, C.secondary("Search", ft.Icons.SEARCH_ROUNDED, search)], spacing=T.S2),
            status, results,
        ], spacing=T.S3, tight=True), width=T.px(660)),
        bgcolor=T.SURFACE_2, shape=ft.RoundedRectangleBorder(radius=T.RADIUS),
        actions=[C.ghost("Find automatically", ft.Icons.AUTO_AWESOME_ROUNDED, auto),
                 C.ghost("Close", on_click=lambda e: app.page.pop_dialog())]))
    search()
