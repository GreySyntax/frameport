"""Game page: a hero with the one-click action, where the game is installed, what FramePort will do, and the full
patch list tucked under "Advanced"."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

import flet as ft

from ... import pipeline
from ...artwork import thumbs
from ...core import library
from ...patches import base
from ...recommend import catalog, engine
from .. import components as C
from .. import theme as T
from ..help import HELP

if TYPE_CHECKING:
    from ..app import FramePortApp

CATEGORY_TITLES = {"frame": "Steam Frame fixes", "overport": "overport patches", "adapter": "Adapter settings",
                   "device": "Frame-side files & environment", "pcvr": "PC VR (Revive / Proton)"}


def _ago(t: float | None) -> str:
    if not t:
        return ""
    d = time.time() - t
    return "just now" if d < 90 else f"{int(d / 60)} min ago" if d < 3600 else \
        f"{int(d / 3600)} h ago" if d < 86400 else time.strftime("%b %d", time.localtime(t))


class GameView:
    def __init__(self, app: "FramePortApp", package: str, advanced: bool = False, show_all: bool = False):
        self.app, self.package, self.advanced, self.show_all = app, package, advanced, show_all
        from .library import twins

        self.g = library.game(package)
        self.games = library.games()
        self.twins = twins(self.games)
        self.rift = bool(self.g) and self.g.get("kind") == "rift"

    # ---------------------------------------------------------------- hero
    def hero(self) -> ft.Control:
        app, g, pkg = self.app, self.g, self.package
        from .library import display_title

        a = g["analysis"]
        art = thumbs.url(pkg, ("hero", "landscape", "portrait", "square"), 1280)
        platform = "Oculus Rift · PC VR" if self.rift else "Meta Quest"
        facts = " · ".join(x for x in (platform, a.get("engine"), a.get("xr")) if x and x != "?")
        recipe = g.get("recipe") or {}
        chips = [C.status_chip(recipe.get("status", "unknown"))]
        st = C.install_state(g, app.frame_info)
        if st in ("installed", "outdated"):
            chips.append(C.install_badge(st))
        if self.rift and pkg in app.pc_installs():
            chips.append(C.install_badge("on_pc"))
        actions = self.actions()
        return ft.Container(
            ft.Stack([
                C.art_fill(art, radius=T.px(16), hero=True, left=0, right=0, top=0, bottom=0,
                           placeholder_icon=ft.Icons.COMPUTER_ROUNDED if self.rift else ft.Icons.VIEW_IN_AR_ROUNDED),
                ft.Container(left=0, right=0, top=0, bottom=0, border_radius=T.px(16), gradient=ft.LinearGradient(
                    begin=ft.Alignment.CENTER_LEFT, end=ft.Alignment.CENTER_RIGHT,
                    colors=[T.soft(T.BG, 0.97), T.soft(T.BG, 0.80), T.soft(T.BG, 0.25)], stops=[0.0, 0.45, 1.0])),
                ft.Container(ft.Row([
                    ft.Column([
                        C.ghost("Library", ft.Icons.ARROW_BACK_ROUNDED, lambda e: app.go("library")),
                        ft.Container(expand=True),
                        C.meta(facts.upper(), T.TEXT_2, weight=ft.FontWeight.W_600),
                        ft.Text(display_title(g, self.twins), size=T.px(34), weight=ft.FontWeight.W_800, color=T.TEXT,
                                max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Row(chips, spacing=T.S2, wrap=True),
                        ft.Container(height=T.S2),
                        actions,
                    ], spacing=T.S2, expand=True),
                ]), padding=ft.Padding(T.S4, T.S3, T.S5, T.S5), left=0, right=0, top=0, bottom=0),
            ]),
            height=T.px(330), border_radius=T.px(16), border=ft.Border.all(1, T.BORDER))

    def actions(self) -> ft.Control:
        app, g, pkg = self.app, self.g, self.package
        job = app.jobs.busy_with(pkg)
        if job:
            pct = (f" {job.fraction:.0%}" if job.fraction is not None else "") + \
                (f" · {job.speed.split(' · ')[0]}" if job.speed else "")
            return ft.Row([
                ft.FilledButton(content=ft.Row([ft.ProgressRing(width=T.px(16), height=T.px(16), stroke_width=T.px(2), color=T.ON_ACCENT),
                                                ft.Text(f"{job.stage or 'Queued'}{pct}", color=T.ON_ACCENT,
                                                        weight=ft.FontWeight.W_600)], spacing=T.px(10), tight=True),
                                on_click=lambda e: app.show_activity(True),
                                style=ft.ButtonStyle(bgcolor=T.ACCENT, shape=ft.RoundedRectangleBorder(radius=T.px(8)),
                                                     padding=ft.Padding(T.px(22), T.px(18), T.px(22), T.px(18)))),
                C.ghost("Cancel", ft.Icons.CLOSE_ROUNDED, lambda e: app.jobs.cancel(job)),
            ], spacing=T.S2)
        buttons: list[ft.Control] = []
        for i, (label, icon, handler, disabled, tip) in enumerate(app.play_options(g) + app.install_options(g)):
            buttons.append(C.primary(label, icon, handler, disabled, tip, big=True) if i == 0 else
                           C.secondary(label, icon, handler, disabled, tip))
        if C.is_media_player(g) and g.get("kind") != "rift" and app.frame_state == "connected" and \
                C.install_state(g, app.frame_info) in ("installed", "outdated"):
            buttons.append(C.secondary("Add videos", ft.Icons.VIDEO_LIBRARY_OUTLINED,
                                       lambda e: app.send_files_dialog(pkg), False,
                                       "Send videos from this PC; they also appear in the player's own folder"))
        more = ft.PopupMenuButton(icon=ft.Icons.MORE_HORIZ_ROUNDED, icon_color=T.TEXT_2, bgcolor=T.SURFACE_2,
                                  tooltip="More actions", items=C.menu_items(app.game_actions(pkg, quick=False)))
        return ft.Row(buttons + [more], spacing=T.S2, wrap=True)

    # ---------------------------------------------------------------- sections
    def where(self) -> ft.Control:
        app, g, pkg = self.app, self.g, self.package
        cards = []
        st = C.install_state(g, app.frame_info)
        last = g.get("last_test") or {}
        diff = C.settings_diff(g, app.frame_info)
        if diff:
            def names(ids):
                return ", ".join(base.get(i).title if i in base.REGISTRY else i for i in ids)
            changed = "; ".join(filter(None, [f"on: {names(diff[0])}" if diff[0] else "",
                                              f"off: {names(diff[1])}" if diff[1] else ""]))
        frame_line = {"installed": "Installed",
                      "outdated": (f"Installed · patch settings changed since ({changed}) — update to apply" if diff
                                   else "Installed · a newer build is ready"),
                      "missing": "Not installed", None: "Frame not connected"}[st]
        frame_ok = st in ("installed", "outdated")
        # Oculus/LibOVR Rift games need Revive, which can't run on the Frame — be honest about it
        patches = (g.get("recipe") or {}).get("patches", {})
        rift_oculus = self.rift and "pcvr.revive" in patches
        rift_repack = self.rift and "pcvr.repack_launcher" in patches
        rift_platform = self.rift and (g["analysis"].get("extra") or {}).get("platform_sdk")
        frame_color = (T.TEXT_3 if (rift_oculus or rift_platform) and not frame_ok else
                       T.OK if st == "installed" else T.WARN if st == "outdated" else T.TEXT_3)
        frame_sub = ("Oculus game — needs Revive, which doesn't run on the Frame. Play it on this PC (SteamVR)."
                     if rift_oculus else
                     "Needs the Oculus Platform (Meta Horizon app) for its license check, which the Frame doesn't "
                     "have — it crashes at startup there. Play it on this PC." if rift_platform else
                     "Uses the repack's bundled Revive — experimental on the Frame" if rift_repack and not last else
                     f"Last launch test: {last.get('verdict')} · furthest: {last.get('milestone') or '—'}" if last
                     else "Runs directly — no Revive needed" if self.rift else "")
        cards.append(self.target_card(
            ft.Icons.VIEW_IN_AR_ROUNDED, "Steam Frame", frame_line, frame_color, frame_sub,
            [C.icon_btn(ft.Icons.SCIENCE_OUTLINED, C.tip("Launch test on the Frame. " + HELP["launch_test"]),
                        lambda e: app.test_game(pkg, "frame"), not frame_ok),
             C.icon_btn(ft.Icons.DELETE_OUTLINE_ROUNDED, C.tip("Uninstall from the Frame. " + HELP["uninstall"]),
                        lambda e: app.uninstall(pkg, "frame"), not frame_ok)] if frame_ok else []))
        if self.rift:
            dep = app.pc_installs().get(pkg)
            cards.append(self.target_card(
                ft.Icons.COMPUTER_ROUNDED, "This PC (Steam + Revive)",
                ("In your Steam library · launch settings changed — update it" if C.pc_outdated(g, dep) else
                 "In your Steam library") if dep else "Not installed",
                (T.WARN if C.pc_outdated(g, dep) else T.PC) if dep else T.TEXT_3,
                (f"Revive {dep.get('revive_version') or ''} · {dep.get('backend') or 'openxr'} backend"
                 if dep.get("revive_win") else "The repack's own Revive · runs the game directly"
                 if dep.get("launch") == "repack" else "Runs the game directly") if dep else "",
                [C.icon_btn(ft.Icons.SCIENCE_OUTLINED, "Launch test on this PC",
                            lambda e: app.test_game(pkg, "pc")),
                 C.icon_btn(ft.Icons.DELETE_OUTLINE_ROUNDED, "Remove from this PC's Steam library",
                            lambda e: app.uninstall(pkg, "pc"))] if dep else []))
        return C.section("Where it's installed", ft.Row(cards, spacing=T.S3), help="where")

    def target_card(self, icon, name, line, color, sub, buttons) -> ft.Control:
        return C.card(ft.Row([
            ft.Container(ft.Icon(icon, color=color, size=T.px(22)), width=T.px(44), height=T.px(44), border_radius=T.px(10),
                         bgcolor=T.soft(color, 0.14), alignment=ft.Alignment.CENTER),
            ft.Column([C.body(name, T.TEXT, weight=ft.FontWeight.W_600), C.body(line, color, size=T.T_META)]
                      + ([C.meta(sub)] if sub else []), spacing=T.px(2), expand=True),
            *buttons,
        ], spacing=T.S3), expand=True)

    def notes(self) -> list[ft.Control]:
        g, pkg = self.g, self.package
        recipe = library.recipe_from_dict(g["recipe"])
        entry = catalog.lookup(pkg)
        out = []
        extra = g["analysis"].get("extra", {})
        if self.rift and g.get("exe_confirmed") is False:
            out.append(C.callout(ft.Row([
                C.body(f"FramePort picked {g.get('exe', '').rsplit('/', 1)[-1]} to start this game, but there are "
                       "other candidates. Check it before installing.", T.TEXT, expand=True),
                C.secondary("Check", ft.Icons.TERMINAL_ROUNDED, lambda e: self.app.choose_exe(pkg))]), "warn",
                ft.Icons.HELP_OUTLINE_ROUNDED))
        if self.rift and extra.get("platform_sdk"):
            out.append(C.callout("Uses the Oculus Platform SDK: it checks your Oculus license. Normally that needs the "
                                 "Oculus app on this PC with a license you own, so it may quit right after starting "
                                 "on the headset. You can still try it.", "warn"))
        if recipe.status == "unsupported":
            out.append(C.callout(recipe.notes or "This game can't run on the Steam Frame.", "error"))
        elif recipe.notes and not (self.rift and extra.get("platform_sdk")):
            out.append(C.callout(recipe.notes, "info"))
        if entry and entry.pcvr_alternative:
            out.append(C.callout(f"PC VR alternative: {entry.pcvr_alternative}", "pc"))
        from .library import counterparts

        links = []
        for r in counterparts(g, self.games):
            other_rift = r.get("kind") == "rift"
            links.append(C.ghost(f"Also in your library: {'Rift' if other_rift else 'Quest'} version",
                                 ft.Icons.COMPUTER_ROUNDED if other_rift else ft.Icons.VIEW_IN_AR_ROUNDED,
                                 lambda e, p=r["package"]: self.app.open_game(p), color=T.ACCENT))
        if links:
            out.append(ft.Row(links, spacing=T.S2, wrap=True))
        warns = engine.warnings(recipe)
        if warns:
            out.append(C.callout("\n".join(warns), "warn"))
        return out

    def about(self) -> ft.Control | None:
        from ...artwork import details as det

        d = self.g.get("details") or {}
        shots = det.screenshot_files(self.package)
        if not d.get("description") and not shots and not d.get("genres"):
            if "details" in self.g:
                return None
            return C.section("About this game", C.card(ft.Row([
                ft.ProgressRing(width=T.px(16), height=T.px(16), stroke_width=T.px(2), color=T.ACCENT),
                C.meta("Looking up the store description and screenshots…")], spacing=T.S2)))
        parts: list[ft.Control] = []
        if shots:
            strip = ft.Row(spacing=T.S3, scroll=ft.ScrollMode.AUTO)
            for i, shot in enumerate(shots):
                url = thumbs.asset_url(thumbs.thumb(shot, 480, shot.stem))
                strip.controls.append(ft.Container(
                    C.art_fill(url, radius=T.RADIUS_SM, width=T.px(256), height=T.px(144)), border_radius=T.RADIUS_SM,
                    on_click=lambda e, i=i: self.lightbox(shots, i), ink=True, tooltip="View screenshot"))
            parts.append(strip)
        facts = [(k, d.get(k)) for k in ("developer", "publisher", "release_date") if d.get(k)]
        if facts:
            parts.append(ft.Row([ft.Column([C.meta({"developer": "Developer", "publisher": "Publisher",
                                                     "release_date": "Released"}[k]), C.body(v, T.TEXT)], spacing=T.px(2))
                                 for k, v in facts], spacing=T.S6, wrap=True))
        if d.get("genres"):
            parts.append(ft.Row([C.pill(g, T.TEXT_2) for g in d["genres"][:8]], spacing=T.px(6), wrap=True))
        text = d.get("description") or d.get("short") or ""
        if text:
            long = len(text) > 480
            body = C.body(text if not long else text[:480].rsplit(" ", 1)[0] + "…", T.TEXT, selectable=True)
            parts.append(body)
            if long:
                def more(e):
                    body.value = text if body.value.endswith("…") else text[:480].rsplit(" ", 1)[0] + "…"
                    e.control.text = "Show less" if not body.value.endswith("…") else "Show more"
                    body.update()
                    e.control.update()
                parts.append(ft.TextButton("Show more", on_click=more, style=ft.ButtonStyle(color=T.ACCENT)))
        links = det.store_links(d)
        if links:
            parts.append(ft.Row([ft.TextButton(label, icon=ft.Icons.OPEN_IN_NEW_ROUNDED, url=url,
                                               style=ft.ButtonStyle(color=T.ACCENT)) for label, url in links],
                                spacing=T.S2, wrap=True))
        src = ", ".join({"oculusdb": "Meta store (OculusDB)", "steam": "Steam store"}.get(s, s)
                        for s in d.get("sources") or [])
        return C.section("About this game", C.card(ft.Column(parts, spacing=T.S4)),
                         subtitle=f"From the {src}" if src else None)

    def lightbox(self, shots: list, index: int) -> None:
        page = self.app.page
        state = {"i": index}
        img = ft.Image(src=thumbs.asset_url(shots[index]), fit=ft.BoxFit.CONTAIN, width=T.px(1100), height=T.px(620),
                       border_radius=T.RADIUS_SM)
        counter = C.meta(f"{index + 1} / {len(shots)}")

        def show(delta):
            state["i"] = (state["i"] + delta) % len(shots)
            img.src = thumbs.asset_url(shots[state["i"]])
            counter.value = f"{state['i'] + 1} / {len(shots)}"
            img.update()
            counter.update()
        page.show_dialog(ft.AlertDialog(
            content=ft.Container(ft.Column([img, ft.Row([
                C.icon_btn(ft.Icons.CHEVRON_LEFT_ROUNDED, "Previous", lambda e: show(-1)), counter,
                C.icon_btn(ft.Icons.CHEVRON_RIGHT_ROUNDED, "Next", lambda e: show(1)),
                ft.Container(expand=True), C.ghost("Close", on_click=lambda e: page.pop_dialog())])],
                spacing=T.S2, tight=True), width=T.px(1100)),
            bgcolor=T.BG, shape=ft.RoundedRectangleBorder(radius=T.RADIUS), content_padding=T.S3))

    def tags(self) -> ft.Control:
        from .library import all_tags, auto_tags, normalize_tag, user_tags

        pkg = self.package
        row = ft.Row(spacing=T.S2, run_spacing=T.S2, wrap=True)

        def save(tags):
            library.upsert_game(pkg, tags=tags)
            self.g = library.game(pkg)
            fill()
            row.update()

        def remove(tag):
            save([t for t in user_tags(self.g) if t != tag])

        def add(e):
            tag = normalize_tag(e.control.value)
            e.control.value = ""
            if tag and tag.lower() not in {t.lower() for t in user_tags(self.g)}:
                save(user_tags(self.g) + [tag])
            else:
                e.control.update()

        field = ft.TextField(hint_text="Add a tag", dense=True, width=T.px(150), text_size=T.T_META,
                             border_radius=T.px(20), bgcolor=T.SURFACE_3, border_color=ft.Colors.TRANSPARENT,
                             focused_border_color=T.ACCENT, content_padding=ft.Padding(T.px(12), T.px(6), T.px(12), T.px(6)), on_submit=add)

        def fill():
            mine = user_tags(self.g)
            chips = [ft.Container(ft.Row([C.body(t, T.TEXT, size=T.T_META),
                                          ft.Icon(ft.Icons.CLOSE_ROUNDED, size=T.px(13), color=T.TEXT_2)], spacing=T.px(4), tight=True),
                                  bgcolor=T.ACCENT_SOFT, border_radius=T.px(20), padding=ft.Padding(T.px(10), T.px(5), T.px(8), T.px(5)),
                                  on_click=lambda e, t=t: remove(t), tooltip="Remove tag")
                     for t in mine]
            chips += [ft.Container(C.meta(t), border=ft.Border.all(1, T.BORDER), border_radius=T.px(20),
                                   padding=ft.Padding(T.px(10), T.px(5), T.px(10), T.px(5)),
                                  tooltip="Added automatically (engine, VR API or store genre)")
                      for t in auto_tags(self.g) if t.lower() not in {m.lower() for m in mine}]
            used = {x for g in self.games for x in user_tags(g)}
            suggestions = [t for t in all_tags(self.games) if t in used and t not in mine][:6]
            chips.append(field)
            chips += [ft.Container(C.meta("+ " + t, T.ACCENT), padding=ft.Padding(T.px(6), T.px(5), T.px(6), T.px(5)),
                                   on_click=lambda e, t=t: save(user_tags(self.g) + [t]), tooltip="Add this tag")
                      for t in suggestions]
            row.controls = chips
        fill()
        return C.section("Tags", row, subtitle="Use tags to group and filter your library", help="tags")

    def recipe_summary(self) -> ft.Control:
        recipe = library.recipe_from_dict(self.g["recipe"])
        entry = catalog.lookup(self.package)
        on = [base.get(pid) for pid in recipe.patches if pid in base.REGISTRY or _known(pid)]
        visible = [p for p in on if not p.default_on or p.category in ("pcvr",)]
        if entry and recipe.source.startswith("catalog"):
            lead = f"Known-good recipe for this game, tested {entry.verified.get('date', '')}".strip(", ")
            icon, color = ft.Icons.VERIFIED_ROUNDED, T.OK
        elif recipe.source == "user":
            lead, icon, color = "Your custom recipe", ft.Icons.TUNE_ROUNDED, T.ACCENT
        else:
            lead, icon, color = "Suggested by FramePort from the game's engine and APIs", ft.Icons.AUTO_AWESOME_ROUNDED, \
                T.ACCENT
        chips = [ft.Container(C.body(p.title, T.TEXT, size=T.T_META),
                              tooltip=C.tip(recipe.reasons.get(p.id) or p.description),
                              bgcolor=T.SURFACE_3, border_radius=T.px(6), padding=ft.Padding(T.px(10), T.px(5), T.px(10), T.px(5)))
                 for p in visible]
        base_count = len(on) - len(visible)
        if base_count > 0:
            chips.append(C.with_help(C.meta(f"+ {base_count} standard fixes"), "standard_fixes"))
        as_is = recipe.as_is
        if as_is and self.rift:
            # a pre-patched Rift copy still needs a VR runtime on the Frame: Revive is added at launch, not to its files
            lead, icon, color = ("Your copy is used as it is (only the Frame's copy gets launch fixes)" +
                                 (" · Revive provides the Oculus runtime" if "pcvr.revive" in recipe.patches else "")), \
                ft.Icons.INVENTORY_2_ROUNDED, T.PC
        elif as_is:
            lead, icon, color = "Installs the game exactly as it is: no patches (your copy is already patched)", \
                ft.Icons.INVENTORY_2_ROUNDED, T.PC
            chips = []

        def toggle_as_is(e):
            r = library.recipe_from_dict(library.game(self.package)["recipe"])
            r.as_is = bool(e.control.value)  # Revive stays as it is: it isn't a change to the game's files
            r.source = "user"
            pipeline.set_recipe(self.package, r)
            self.app.open_game(self.package, advanced=self.advanced)
        switch = C.with_help(ft.Switch(value=as_is, active_color=T.PC, on_change=toggle_as_is,
                                       label=("Already patched: don't change the game's files" if self.rift else
                                              "Already patched: install as is (skip patching)")), "as_is")
        return C.section(
            "What FramePort will do",
            C.card(ft.Column([
                ft.Row([ft.Icon(icon, color=color, size=T.px(18)), C.body(lead, T.TEXT, weight=ft.FontWeight.W_500,
                                                                     expand=True)], spacing=T.S2),
                ft.Row(chips, spacing=T.S2, run_spacing=T.S2, wrap=True) if chips else
                (ft.Container() if as_is else C.meta("Nothing to patch: it runs as is.")),
                ft.Divider(),
                switch,
            ], spacing=T.S3)),
            action=C.ghost("Hide patches" if self.advanced else "Customize", ft.Icons.TUNE_ROUNDED,
                           lambda e: self.app.open_game(self.package, advanced=not self.advanced),
                           tooltip=None if self.advanced else "See every patch and turn them on or off"),
            help="recipe")

    def advanced_panel(self) -> ft.Control:
        app, g, package = self.app, self.g, self.package
        recipe = library.recipe_from_dict(g["recipe"])
        analysis = library.analysis_from_dict(g["analysis"])
        shown, hidden = engine.visible_patches(analysis, recipe)
        listed = {p.id for p in (shown + hidden if self.show_all else shown)}
        state = {"recipe": recipe}
        warn = C.body("", T.WARN)

        def save(r):
            r.source = "user"
            pipeline.set_recipe(package, r)
            warn.value = "\n".join(engine.warnings(r))
            warn.update()

        def toggle(pid):
            def handler(e):
                r = state["recipe"]
                if e.control.value:
                    patch = base.get(pid)
                    r.patches[pid] = {"value": patch.params[0].default} if patch.category == "adapter" else \
                        {q.key: (r.patches.get(pid) or {}).get(q.key, q.default) for q in patch.params}
                    r.reasons[pid] = r.reasons.get(pid) or "Enabled by you."
                else:
                    r.patches.pop(pid, None)
                save(r)
            return handler

        def set_value(pid, kind):
            def handler(e):
                try:
                    v = float(e.control.value) if kind == "float" else int(float(e.control.value))
                except ValueError:
                    return
                state["recipe"].patches[pid] = {"value": v}
                save(state["recipe"])
            return handler

        def set_param(pid, key):
            def handler(e):
                r = state["recipe"]
                r.patches.setdefault(pid, {})[key] = e.control.value or ""
                r.reasons[pid] = r.reasons.get(pid) or "Set by you."
                save(r)
                app.open_game(package, advanced=True, show_all=self.show_all)  # the switch shows it's on now
            return handler

        sections = []
        for cat in ("pcvr", "frame", "overport", "adapter", "device"):
            rows = []
            for p in [p for p in base.all_patches() if p.category == cat and p.id in listed]:
                on = p.id in recipe.patches
                reason = recipe.reasons.get(p.id, "")
                sub = [C.meta(p.description, T.TEXT_2)]
                if reason:
                    sub.insert(0, C.meta(reason, T.ACCENT))
                extra = None
                if cat == "adapter":
                    val = recipe.params(p.id).get("value", p.params[0].default)
                    extra = ft.TextField(value=str(val), width=T.px(90), dense=True, text_size=T.T_BODY,
                                         border_color=T.BORDER, on_blur=set_value(p.id, p.params[0].kind))
                elif p.params:
                    q = p.params[0]
                    multi = q.kind == "text"
                    extra = ft.TextField(value=str(recipe.params(p.id).get(q.key, q.default) or ""), hint_text=q.help,
                                         width=T.px(260), dense=True, multiline=multi, min_lines=1,
                                         max_lines=4 if multi else 1, text_size=T.T_BODY, border_color=T.BORDER,
                                         on_blur=set_param(p.id, q.key))
                rows.append(ft.Container(ft.Row([
                    ft.Column([ft.Row([C.body(p.title, T.TEXT, weight=ft.FontWeight.W_500)]
                                      + ([C.pill("experimental", T.WARN, tooltip=C.tip(HELP["experimental"]))]
                                         if p.experimental else [])
                                      + [C.meta(p.id)], spacing=T.S2, wrap=True), *sub], spacing=T.px(3), expand=True),
                    *([extra] if extra else []),
                    ft.Switch(value=on, on_change=toggle(p.id), active_color=T.ACCENT),
                ], spacing=T.S3), padding=ft.Padding(T.S4, T.px(10), T.S4, T.px(10)), border=ft.Border(
                    top=ft.BorderSide(1, T.BORDER))))
            if not rows:
                continue
            count = sum(1 for p in base.all_patches() if p.category == cat and p.id in recipe.patches)
            sections.append(C.card(ft.Column([
                ft.Container(ft.Row([C.body(CATEGORY_TITLES[cat], T.TEXT, weight=ft.FontWeight.W_600),
                                     C.help_icon(f"cat_{cat}"), C.meta(f"{count} on")], spacing=T.S2), padding=ft.Padding(T.S4, T.S3, T.S4, T.S3)),
                *rows], spacing=0), padding=0))

        def set_alt(e):
            state["recipe"].use_alt = e.control.value
            save(state["recipe"])
        top = []
        if recipe.alt_patches:
            top.append(C.with_help(ft.Switch(label="Install the alternate build (" + ", ".join(recipe.alt_patches)
                                             + ")", value=recipe.use_alt, on_change=set_alt, active_color=T.ACCENT),
                                   "alt_build"))
        if hidden:
            top.append(C.with_help(ft.Switch(
                label=f"Show all patches ({len(hidden)} don't apply to this game)", value=self.show_all,
                active_color=T.ACCENT, on_change=lambda e: app.open_game(package, advanced=True,
                                                                         show_all=e.control.value)), "show_all"))
        warn.value = "\n".join(engine.warnings(recipe))
        return C.section("Patches", *top, warn, *sections,
                         subtitle="Changes apply to the next install. Hover a chip above for why it was suggested.")

    def details(self) -> ft.Control:
        g, a = self.g, self.g["analysis"]
        rows = [C.kv("Package", g["package"])]
        if self.rift:
            rows += [C.kv("Folder", g.get("game_dir") or ""), C.kv("Executable", g.get("exe") or ""),
                     C.kv("Type", f"{a['abis'][0]} · {a['graphics']}"),
                     C.kv("Size", f"{(a.get('extra', {}).get('data_bytes') or 0) / 2**30:.1f} GiB")]
        else:
            rows += [C.kv("Version", a.get("version") or ""), C.kv("ABIs", ", ".join(a.get("abis") or []), "abis"),
                     C.kv("Graphics", a.get("graphics") or "", "graphics"), C.kv("APK", g.get("apk") or ""),
                     C.kv("Data", f"{(g.get('data_bytes') or 0) / 2**30:.1f} GiB"
                          + (f" · {g.get('data_dir')}" if g.get("data_dir") else ""))]
        b = g.get("build") or {}
        if b.get("apk"):
            rows.append(C.kv("Last build", b["apk"]))
        rows.append(C.kv("Recipe", (g.get("recipe") or {}).get("source", ""), "recipe_source"))
        return ft.ExpansionTile(title=C.body("Details", T.TEXT, weight=ft.FontWeight.W_600), controls=[
            ft.Container(ft.Column(rows, spacing=T.S2), padding=ft.Padding(T.S4, 0, T.S4, T.S4))],
            bgcolor=T.SURFACE, collapsed_bgcolor=T.SURFACE)

    def build(self) -> ft.Control:
        if not self.g:
            return C.empty_state(ft.Icons.SEARCH_OFF_ROUNDED, "Game not found", "It was removed from the library.",
                                 C.primary("Back to library", on_click=lambda e: self.app.go("library")))
        about = self.about()
        body = [self.hero(), *self.notes(), self.where(), *([about] if about else []), self.tags(),
                self.recipe_summary()]
        if self.advanced:
            body.append(self.advanced_panel())
        body.append(self.details())
        return ft.Column([ft.Container(ft.Column(body, spacing=T.S5), padding=ft.Padding(0, 0, T.S3, T.S6))],
                         scroll=ft.ScrollMode.AUTO, expand=True)


def _known(pid: str) -> bool:
    try:
        base.get(pid)
        return True
    except KeyError:
        return False
