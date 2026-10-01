"""'Which program starts <game>?' — pick the executable of a Rift game when FramePort isn't sure (or to change it)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import flet as ft

from ... import pipeline
from ...core import library
from .. import components as C
from .. import theme as T

if TYPE_CHECKING:
    from ..app import FramePortApp


def _size(n: int) -> str:
    return f"{n / 2**20:.0f} MB" if n < 2**30 else f"{n / 2**30:.1f} GB"


def show_exe_dialog(app: "FramePortApp", package: str, remaining: int = 0,
                    on_done: Callable[[], None] | None = None) -> None:
    g = library.game(package)
    extra = (g.get("analysis") or {}).get("extra") or {}
    cands = extra.get("exe_candidates") or [{"path": g.get("exe"), "score": 0, "reasons": [], "size": 0}]
    current = g.get("exe") or cands[0]["path"]
    group = ft.RadioGroup(value=current, content=ft.Column(spacing=T.S2))
    for i, c in enumerate(cands):
        tags = [C.pill(r, T.OK if r in ("Unreal game build", "Unity game (next to its data)", "name matches the game",
                                        "named in the Oculus manifest") else
                       T.WARN if r in ("Steam build", "Unreal launcher (starts the real game build)") else T.TEXT_2)
                for r in c.get("reasons", [])]
        if i == 0:
            tags.insert(0, C.pill("Best guess", T.ACCENT, ft.Icons.AUTO_AWESOME_ROUNDED))
        folder, _, name = c["path"].rpartition("/")
        group.content.controls.append(ft.Container(ft.Row([
            ft.Radio(value=c["path"], active_color=T.ACCENT),
            ft.Column([
                ft.Row([C.body(name, T.TEXT, weight=ft.FontWeight.W_600), C.meta(_size(c.get("size") or 0))],
                       spacing=T.S2),
                C.meta(folder or "(top folder)", selectable=True),
                ft.Row(tags, spacing=T.px(6), wrap=True) if tags else ft.Container(),
            ], spacing=T.px(4), expand=True),
        ], vertical_alignment=ft.CrossAxisAlignment.START), padding=ft.Padding(T.S2, T.S2, T.S3, T.S2),
            border_radius=T.RADIUS_SM, bgcolor=T.SURFACE if c["path"] != current else T.ACCENT_SOFT,
            on_click=lambda e, p=c["path"]: (setattr(group, "value", p), group.update())))

    def use(e):
        choice = group.value
        app.page.pop_dialog()

        def work():
            try:
                pipeline.set_exe(package, choice)
                app.toast(f"{g.get('title')} starts with {choice.rsplit('/', 1)[-1]}")
            except Exception as exc:  # noqa: BLE001
                app.toast(f"Couldn't use {choice}: {exc}", error=True)
            app.refresh_view()
            if on_done:
                on_done()
        app.run_bg(work)

    def later(e):
        app.page.pop_dialog()
        if on_done:
            on_done()

    title = f"Which program starts {g.get('title')}?"
    lead = ("FramePort found more than one program that could start this game. Pick the one you'd double-click to "
            "play it. Oculus builds usually work better with Revive than Steam builds.")
    app.page.show_dialog(ft.AlertDialog(
        title=ft.Row([ft.Text(title, weight=ft.FontWeight.W_600, expand=True)]
                     + ([C.meta(f"{remaining} more after this")] if remaining else [])),
        content=ft.Container(ft.Column([C.body(lead), group], spacing=T.S4, scroll=ft.ScrollMode.AUTO, tight=True),
                             width=T.px(620), height=min(120 + 96 * len(cands), 520)),
        bgcolor=T.SURFACE_2, shape=ft.RoundedRectangleBorder(radius=T.RADIUS),
        actions=[C.ghost("Decide later", on_click=later), C.primary("Use this program", on_click=use)]))
