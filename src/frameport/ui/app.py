"""FramePort desktop GUI (Flet / Flutter). Morphe-style flow:

    Library (scan/add games) → Game (suggested patches, confirm) → Job (patch → validate → install → test)
    Frame (pair / connect, Lepton, installed games)   Tools (portable toolchain)

The UI only calls `frameport.pipeline` and friends; all work runs in background threads and reports through
core.events.Reporter.
"""
from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path

import flet as ft

from .. import pipeline
from ..artwork import fetch as artwork
from ..core import library
from ..core.events import Event, Reporter
from ..patches import base
from ..recommend import catalog, engine

STATUS_STYLE = {
    "works": ("Works", ft.Colors.GREEN_400),
    "issues": ("Works with issues", ft.Colors.AMBER_400),
    "unsupported": ("Unsupported", ft.Colors.RED_400),
    "unknown": ("Untested", ft.Colors.BLUE_GREY_300),
}
CATEGORY_TITLES = {"frame": "Steam Frame fixes", "overport": "overport patches", "adapter": "Adapter settings",
                   "device": "Frame-side files & environment"}


def status_chip(status: str) -> ft.Container:
    label, color = STATUS_STYLE.get(status, STATUS_STYLE["unknown"])
    return ft.Container(ft.Text(label, size=11, color=ft.Colors.BLACK, weight=ft.FontWeight.W_600), bgcolor=color,
                        padding=ft.Padding(8, 2, 8, 2), border_radius=10)


def pill(text: str) -> ft.Container:
    return ft.Container(ft.Text(text, size=11), border=ft.Border.all(1, ft.Colors.OUTLINE), padding=ft.Padding(6, 1, 6, 1),
                        border_radius=8)


class FramePortApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.target = None  # FrameLeptonTarget when connected
        self.frame_info: dict | None = None
        self.pairing = None
        self.job_running = False
        page.title = "FramePort"
        page.theme_mode = ft.ThemeMode.DARK
        page.theme = ft.Theme(color_scheme_seed=ft.Colors.DEEP_PURPLE)
        page.dark_theme = ft.Theme(color_scheme_seed=ft.Colors.DEEP_PURPLE)
        page.padding = 0
        page.window.min_width, page.window.min_height = 1000, 680
        self.body = ft.Container(expand=True, padding=20)
        self.rail = ft.NavigationRail(
            selected_index=0, label_type=ft.NavigationRailLabelType.ALL, min_width=90, group_alignment=-0.95,
            leading=ft.Container(ft.Text("FramePort", weight=ft.FontWeight.BOLD, size=16), padding=ft.Padding(0, 16, 0, 16)),
            destinations=[
                ft.NavigationRailDestination(icon=ft.Icons.VIDEOGAME_ASSET_OUTLINED, selected_icon=ft.Icons.VIDEOGAME_ASSET, label="Library"),
                ft.NavigationRailDestination(icon=ft.Icons.VIEW_IN_AR_OUTLINED, selected_icon=ft.Icons.VIEW_IN_AR, label="Frame"),
                ft.NavigationRailDestination(icon=ft.Icons.BUILD_OUTLINED, selected_icon=ft.Icons.BUILD, label="Tools"),
            ],
            on_change=lambda e: self.navigate(e.control.selected_index),
        )
        self.frame_badge = ft.Text("Frame: not connected", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        page.add(ft.Row([self.rail, ft.VerticalDivider(width=1),
                         ft.Column([ft.Container(self.frame_badge, padding=ft.Padding(20, 10, 20, 0)), self.body],
                                   expand=True, spacing=0)], expand=True))
        self.navigate(0)
        threading.Thread(target=self._startup, daemon=True).start()

    # ------------------------------------------------------------------ helpers
    def toast(self, message: str, error: bool = False):
        self.page.show_dialog(ft.SnackBar(ft.Text(message), bgcolor=ft.Colors.RED_700 if error else None))

    def run_bg(self, fn, *args):
        def wrapper():
            try:
                fn(*args)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                self.toast(f"{type(exc).__name__}: {exc}", error=True)
        self.page.run_thread(wrapper)

    def navigate(self, index: int, **kw):
        self.rail.selected_index = index if index < 3 else 0  # game/job screens belong to the Library
        views = [self.library_view, self.frame_view, self.tools_view]
        self.body.content = views[index](**kw) if index < 3 else kw["view"]
        self.page.update()

    def _startup(self):
        from ..frame.connection import saved_targets
        from ..tools import toolchain

        if not all(s.installed for s in toolchain.status()):
            self.toast("First run: open Tools and install the toolchain (Java, overport, apksigner).")
        saved = saved_targets()
        if saved:
            self.connect(saved[0], quiet=True)
            return
        # nothing remembered yet: look for a Frame and connect if our key (or the user's SSH key) is accepted
        from ..frame.connection import parse_target
        from ..frame.discovery import browse

        for f in browse(4, scan=False):
            self.connect(parse_target(f"{f.user}@{f.host}"), quiet=True)
            break

    # ------------------------------------------------------------------ Library
    def library_view(self):
        grid = ft.GridView(expand=True, max_extent=210, child_aspect_ratio=0.56, spacing=14, run_spacing=14)
        games = library.games()
        installed = {g["package"] for g in (self.frame_info or {}).get("installed", [])}
        for g in games:
            grid.controls.append(self.game_card(g, g["package"] in installed))
        empty = ft.Column([ft.Icon(ft.Icons.FOLDER_OPEN, size=48), ft.Text("No games yet. Scan a folder with Quest "
                           "game dumps (APK + OBB), or add a single APK.")], horizontal_alignment=ft.CrossAxisAlignment.CENTER)
        return ft.Column([
            ft.Row([ft.Text("Library", size=26, weight=ft.FontWeight.BOLD), ft.Container(expand=True),
                    ft.Button("Scan folder", icon=ft.Icons.FOLDER_OPEN, on_click=self.pick_folder),
                    ft.OutlinedButton("Add APK", icon=ft.Icons.ANDROID, on_click=self.pick_apk)]),
            ft.Text(f"{len(games)} game(s). Click a game to review its suggested patches.", color=ft.Colors.ON_SURFACE_VARIANT),
            grid if games else ft.Container(empty, alignment=ft.Alignment.CENTER, expand=True),
        ], expand=True)

    def game_card(self, g: dict, installed: bool) -> ft.Control:
        pkg = g["package"]
        art = next((p for p in artwork.files(pkg) if p.stem == "portrait"), None) or \
            next((p for p in artwork.files(pkg) if p.stem == "icon"), None)
        img = ft.Image(src=art.read_bytes(), fit=ft.BoxFit.COVER, height=210, border_radius=8) if art else \
            ft.Container(ft.Icon(ft.Icons.VIDEOGAME_ASSET, size=56), height=210, alignment=ft.Alignment.CENTER,
                         bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST, border_radius=8)
        a = g["analysis"]
        state = "On Frame" if installed else "Built" if g.get("build", {}).get("ok") else ""
        return ft.Card(ft.Container(ft.Column([
            img,
            ft.Text(g.get("title") or pkg, weight=ft.FontWeight.W_600, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
            ft.Row([status_chip(g["recipe"]["status"]), pill(a["engine"])], spacing=4, wrap=True),
            ft.Text(f"{a['xr']} · {state}" if state else a["xr"], size=11, color=ft.Colors.ON_SURFACE_VARIANT),
        ], spacing=6), padding=8, on_click=lambda e, p=pkg: self.open_game(p), ink=True))

    async def pick_folder(self, e):
        path = await ft.FilePicker().get_directory_path(dialog_title="Folder with Quest games")
        if path:
            self.run_bg(self._scan, path)

    async def pick_apk(self, e):
        files = await ft.FilePicker().pick_files(allow_multiple=True, allowed_extensions=["apk"])
        for f in files or []:
            if f.path:
                self.run_bg(self._scan, f.path)

    def _scan(self, path: str):
        self.toast(f"Scanning {path} …")
        rep = Reporter()
        added = pipeline.add_path(Path(path), rep)
        for g in added:  # artwork for the cards (dynamic: store art by package)
            try:
                artwork.fetch(g["package"], Path(g["apk"]))
            except Exception:
                pass
        self.toast(f"Added {len(added)} game(s).")
        self.navigate(0)

    # ------------------------------------------------------------------ Game (patch selection)
    def open_game(self, package: str):
        self.navigate(3, view=self.game_view(package))

    def game_view(self, package: str):
        g = library.game(package)
        a, recipe = g["analysis"], library.recipe_from_dict(g["recipe"])
        entry = catalog.lookup(package)
        state = {"recipe": recipe}
        hero = next((p for p in artwork.files(package) if p.stem in ("landscape", "hero")), None)

        def toggle(pid):
            def handler(e):
                r = state["recipe"]
                if e.control.value:
                    patch = base.get(pid)
                    r.patches[pid] = {"value": patch.params[0].default} if patch.category == "adapter" else {}
                    r.reasons[pid] = r.reasons.get(pid) or "Enabled by you."
                else:
                    r.patches.pop(pid, None)
                r.source = "user"
                pipeline.set_recipe(package, r)
                warn.value = "\n".join(engine.warnings(r))
            return handler

        def set_value(pid, kind):
            def handler(e):
                try:
                    v = float(e.control.value) if kind == "float" else int(float(e.control.value))
                except ValueError:
                    return
                state["recipe"].patches[pid] = {"value": v}
                pipeline.set_recipe(package, state["recipe"])
            return handler

        sections = []
        for cat in ("frame", "overport", "adapter", "device"):
            rows = []
            for p in [p for p in base.all_patches() if p.category == cat]:
                on = p.id in recipe.patches
                reason = recipe.reasons.get(p.id, "")
                sub = [ft.Text(p.description, size=12, color=ft.Colors.ON_SURFACE_VARIANT)]
                if reason:
                    sub.insert(0, ft.Text("Suggested: " + reason, size=12, color=ft.Colors.PRIMARY))
                trailing = ft.Switch(value=on, on_change=toggle(p.id))
                extra = None
                if cat == "adapter":
                    val = recipe.params(p.id).get("value", p.params[0].default)
                    extra = ft.TextField(value=str(val), width=90, dense=True, on_blur=set_value(p.id, p.params[0].kind))
                rows.append(ft.ListTile(
                    title=ft.Row([ft.Text(p.title, weight=ft.FontWeight.W_500)] +
                                 ([pill("experimental")] if p.experimental else []) + ([pill(p.id)]), wrap=True),
                    subtitle=ft.Column(sub, spacing=2), trailing=ft.Row([extra, trailing] if extra else [trailing], tight=True),
                ))
            count = sum(1 for p in base.all_patches() if p.category == cat and p.id in recipe.patches)
            sections.append(ft.ExpansionTile(title=ft.Text(f"{CATEGORY_TITLES[cat]}  ({count} on)"), controls=rows,
                                             expanded=cat == "frame"))

        def set_alt(e):
            state["recipe"].use_alt = e.control.value
            pipeline.set_recipe(package, state["recipe"])

        alt_row = []
        if recipe.alt_patches:
            alt_row = [ft.Switch(label="Install the alternate build (" + ", ".join(recipe.alt_patches) + ")",
                                 value=recipe.use_alt, on_change=set_alt)]
        warn = ft.Text("\n".join(engine.warnings(recipe)), color=ft.Colors.AMBER_300)
        info = [f"{a['engine']} · {a['xr']} · {a['graphics']}", f"ABIs: {', '.join(a['abis'])} · version {a['version']}",
                f"Recipe: {recipe.source}"]
        header = ft.Row([
            ft.Image(src=hero.read_bytes(), width=320, height=180, fit=ft.BoxFit.COVER, border_radius=10) if hero else ft.Container(),
            ft.Column([
                ft.Row([ft.IconButton(ft.Icons.ARROW_BACK, on_click=lambda e: self.navigate(0)),
                        ft.Text(g.get("title") or package, size=24, weight=ft.FontWeight.BOLD)]),
                ft.Row([status_chip(recipe.status), pill(package)]),
                *[ft.Text(t, size=12, color=ft.Colors.ON_SURFACE_VARIANT) for t in info],
                ft.Text(recipe.notes, size=13) if recipe.notes else ft.Container(),
                ft.Text("PC VR: " + entry.pcvr_alternative, size=12, color=ft.Colors.SECONDARY)
                if entry and entry.pcvr_alternative else ft.Container(),
            ], spacing=4, expand=True),
        ], vertical_alignment=ft.CrossAxisAlignment.START)
        connected = self.target is not None
        actions = ft.Row([
            ft.FilledButton("Patch", icon=ft.Icons.AUTO_FIX_HIGH, on_click=lambda e: self.start_job(package, install=False)),
            ft.FilledButton("Patch & install on Frame", icon=ft.Icons.SEND_TO_MOBILE, disabled=not connected,
                            on_click=lambda e: self.start_job(package, install=True)),
            ft.OutlinedButton("Test on Frame", icon=ft.Icons.PLAY_CIRCLE, disabled=not connected,
                              on_click=lambda e: self.start_job(package, build=False, install=False, test=True)),
            ft.TextButton("Reset to suggested", icon=ft.Icons.RESTART_ALT,
                          on_click=lambda e: (pipeline.reset_recipe(package), self.open_game(package))),
            ft.TextButton("Save as known-good", icon=ft.Icons.VERIFIED, on_click=lambda e: self.save_known_good(package)),
        ], wrap=True)
        return ft.Column([header, actions, warn, *alt_row, *sections], scroll=ft.ScrollMode.AUTO, expand=True, spacing=10)

    def save_known_good(self, package: str):
        g = library.game(package)
        r = library.recipe_from_dict(g["recipe"])
        from ..patches.overport import DEFAULT_OVERPORT

        e = catalog.CatalogEntry(
            package=package, title=g.get("title") or package, status="works", notes=r.notes,
            tested_version=g["analysis"]["version"], engine=g["analysis"]["engine"], xr=g["analysis"]["xr"],
            overport_extra=[p for p in r.patches if base.get(p).category == "overport" and p not in DEFAULT_OVERPORT],
            overport_remove=[p for p in DEFAULT_OVERPORT if p not in r.patches],
            alt_overport=r.alt_patches, use_alt=r.use_alt,
            frame=[p for p in r.patches if base.get(p).category == "frame" and not base.get(p).default_on],
            adapter={p.split(".", 1)[1]: v.get("value") for p, v in r.patches.items() if p.startswith("adapter.")},
            device_files=r.params("device.files").get("files", {}),
            verified={"date": time.strftime("%Y-%m-%d"), "known_good_sha256": g.get("build", {}).get("sha256")},
            source_hint=g.get("name", ""),
        )
        path = catalog.save_user_entry(e)
        self.toast(f"Saved recipe to {path}")

    # ------------------------------------------------------------------ Job
    def start_job(self, package: str, build: bool = True, install: bool = False, test: bool | None = None):
        if self.job_running:
            self.toast("A job is already running.")
            return
        test = install if test is None else test
        g = library.game(package)
        stage_text = ft.Text("Starting…", size=16, weight=ft.FontWeight.W_500)
        bar = ft.ProgressBar(value=None)
        checks = ft.Column(spacing=2)
        log = ft.ListView(expand=True, auto_scroll=True, spacing=0)
        suggestions = ft.Column()
        done_row = ft.Row(visible=False)
        view = ft.Column([
            ft.Row([ft.IconButton(ft.Icons.ARROW_BACK, on_click=lambda e: self.open_game(package)),
                    ft.Text(g.get("title") or package, size=22, weight=ft.FontWeight.BOLD)]),
            stage_text, bar,
            ft.Row([ft.Container(ft.Column([ft.Text("Checks", weight=ft.FontWeight.BOLD), checks], scroll=ft.ScrollMode.AUTO),
                                 width=420, height=380, padding=10, bgcolor=ft.Colors.SURFACE_CONTAINER, border_radius=8),
                    ft.Container(log, expand=True, height=380, padding=10, bgcolor=ft.Colors.SURFACE_CONTAINER_LOWEST,
                                 border_radius=8)]),
            suggestions, done_row,
        ], expand=True, scroll=ft.ScrollMode.AUTO)
        self.navigate(3, view=view)
        rep = Reporter()
        last = {"t": 0.0}

        def sink(ev: Event):
            if ev.kind == "stage":
                stage_text.value = ev.message
                bar.value = None
            elif ev.kind == "progress":
                bar.value = ev.fraction
                if ev.message:
                    stage_text.value = f"{rep.stage_name}: {ev.message}"
            elif ev.kind == "check":
                ok = ev.data.get("ok")
                icon, color = {True: (ft.Icons.CHECK_CIRCLE, ft.Colors.GREEN_400), False: (ft.Icons.CANCEL, ft.Colors.RED_400),
                               None: (ft.Icons.WARNING, ft.Colors.AMBER_400)}[ok]
                checks.controls.append(ft.Row([ft.Icon(icon, color=color, size=16),
                                               ft.Text(f"{ev.data.get('name')}" + (f" — {ev.message}" if ev.message else ""),
                                                       size=12, expand=True)]))
            if ev.message and ev.kind in ("log", "stage"):
                log.controls.append(ft.Text(time.strftime("%H:%M:%S ", time.localtime(ev.time)) + ev.message, size=11,
                                            font_family="monospace", selectable=True))
                if len(log.controls) > 3000:
                    del log.controls[:500]
            now = time.time()
            if now - last["t"] > 0.15 or ev.kind in ("stage", "check"):
                last["t"] = now
                self.page.update()

        rep.subscribe(sink)

        def work():
            self.job_running = True
            try:
                if build:
                    info = pipeline.build_game(package, rep)
                    if not info["ok"]:
                        rep.stage("Build finished with failed checks")
                if install:
                    pipeline.install_game(package, self.target, rep, apk_only=False)
                if test:
                    summary = pipeline.test_game(package, self.target, rep)
                    rep.stage(f"Launch test: {summary['state']} ({summary['verdict']}); furthest: {summary['milestone']}")
                    if summary["suggestions"]:
                        suggestions.controls = [
                            ft.Text("Triage suggests: " + ", ".join(summary["suggestions"]), color=ft.Colors.AMBER_300),
                            ft.FilledButton("Apply suggestions and rebuild", icon=ft.Icons.HEALING,
                                            on_click=lambda e: (pipeline.apply_suggestions(package, summary["suggestions"]),
                                                                self.start_job(package, install=install)))]
                    else:
                        suggestions.controls = [ft.Text(
                            "Startup looks healthy. Put the headset on and launch the game from your Steam library to "
                            "check the picture, controls and audio; then use 'Save as known-good'.")]
                bar.value = 1
                stage_text.value = "Done"
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                stage_text.value = f"Failed: {exc}"
                bar.value = 0
                bar.color = ft.Colors.RED_400
            finally:
                self.job_running = False
                done_row.controls = [ft.Button("Back to game", on_click=lambda e: self.open_game(package))]
                done_row.visible = True
                self.page.update()

        self.page.run_thread(work)

    # ------------------------------------------------------------------ Frame
    def connect(self, target, password=None, quiet=False):
        from ..frame.connection import save_target
        from ..targets.frame_lepton import FrameLeptonTarget

        def work():
            try:
                t = FrameLeptonTarget(target, password).connect()
                if password:
                    t.frame.install_key()
                info = t.describe()
                target.name = info.get("hostname") or target.name
                t.label = target.label
                save_target(target)
                self.target, self.frame_info = t, info
                self.frame_badge.value = f"Frame: {target.label} ({target.host}) · " + \
                    ("Lepton ready" if info.get("lepton") else "Lepton missing")
                self.frame_badge.color = ft.Colors.GREEN_300 if info.get("lepton") else ft.Colors.AMBER_300
                if not quiet:
                    self.toast(f"Connected to {target.label}")
                if self.rail.selected_index == 1:
                    self.navigate(1)
                else:
                    self.page.update()
            except Exception as exc:  # noqa: BLE001
                if not quiet:
                    self.toast(f"Could not connect: {exc}", error=True)
        self.page.run_thread(work)

    def frame_view(self):
        from ..frame.connection import parse_target, saved_targets

        addr = ft.TextField(label="Address", hint_text="steamos@frame.local or 192.168.x.x", width=320)
        pw = ft.TextField(label="Password (first time only)", password=True, can_reveal_password=True, width=220)
        found = ft.Column()

        def do_discover(e):
            found.controls = [ft.ProgressRing(width=18, height=18)]
            self.page.update()

            def work():
                from ..frame.discovery import browse

                res = browse(4)
                found.controls = [ft.ListTile(
                    leading=ft.Icon(ft.Icons.VIEW_IN_AR if f.source != "scan" else ft.Icons.COMPUTER),
                    title=ft.Text(f.name if f.source != "scan" else f"SSH host {f.host}"),
                    subtitle=ft.Text(f"{f.user}@{f.host} · via {f.via} · "
                                     + {"devkit": "SteamOS (Developer Mode)", "frameport": "FramePort ready",
                                        "saved": "remembered", "scan": "found by network scan"}.get(f.source, f.source)),
                    on_click=lambda e, f=f: self.connect(parse_target(f"{f.user}@{f.host}")))
                    for f in res] or [ft.Text("No Frames found. Turn on Developer Mode (Settings → System) and make "
                                              "sure the Frame is on the same network, or use the setup command below.")]
                self.page.update()
            self.page.run_thread(work)

        pair_box = ft.Column()

        def do_pair(e):
            from ..frame.connection import FrameTarget
            from ..frame.pairing import PairingServer

            if self.pairing:
                self.pairing.stop()

            def on_paired(info):
                self.connect(FrameTarget(info["host"], info["user"], 22, info["name"]))
            self.pairing = PairingServer(on_paired=on_paired).start()
            line = self.pairing.one_liner
            pair_box.controls = [
                ft.Text("On the Frame: Steam button → Power → Switch to Desktop, open Konsole and run:"),
                ft.Container(ft.Text(line, font_family="monospace", selectable=True), padding=10,
                             bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST, border_radius=6),
                ft.Row([ft.OutlinedButton("Copy", icon=ft.Icons.COPY, on_click=lambda e: self.copy(line)),
                        ft.Text(f"Pairing code {self.pairing.code}. Waiting for the Frame…", size=12)]),
                ft.Text("It enables SSH, trusts this app's key, announces the Frame on your network and installs "
                        "Lepton if needed. You only do this once.", size=12, color=ft.Colors.ON_SURFACE_VARIANT),
            ]
            self.page.update()

        info = self.frame_info
        status = []
        if info:
            st = [f"{info['hostname']} · {info.get('os')} {info.get('os_version')} (build {info.get('build_id')})",
                  f"Free space: {info['free_bytes'] / 2**30:.0f} GiB · Steam users: {', '.join(info.get('steam_users') or []) or 'none'}"]
            lepton_ok = bool(info.get("lepton"))
            status = [ft.Card(ft.Container(ft.Column([
                ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE if lepton_ok else ft.Icons.WARNING,
                                color=ft.Colors.GREEN_400 if lepton_ok else ft.Colors.AMBER_400),
                        ft.Text("Connected: " + self.target.label, weight=ft.FontWeight.BOLD)]),
                *[ft.Text(s, size=12) for s in st],
                ft.Text("Lepton: " + (info["lepton"] or "not installed"), size=12),
                ft.Row([ft.Button("Install Lepton", disabled=lepton_ok,
                                  on_click=lambda e: self.run_bg(lambda: self.toast(str(self.target.install_lepton())))),
                        ft.OutlinedButton("Refresh", on_click=lambda e: self.connect(self.target.target, quiet=True))]),
            ]), padding=14))]
            rows = []
            for d in info.get("installed", []):
                pkg = d["package"]
                rows.append(ft.ListTile(
                    title=ft.Text(d["title"]), subtitle=ft.Text(f"{pkg} · {d.get('apk_size', 0) / 2**20:.0f} MiB APK"),
                    trailing=ft.Row([
                        ft.IconButton(ft.Icons.PLAY_CIRCLE, tooltip="Headless launch test",
                                      on_click=lambda e, p=pkg: self.start_job(p, build=False, install=False, test=True)
                                      if library.game(p) else self.run_bg(self._quick_test, p)),
                        ft.IconButton(ft.Icons.TUNE, tooltip="Adapter settings", on_click=lambda e, p=pkg: self.settings_dialog(p)),
                        ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="Uninstall (keeps saves)",
                                      on_click=lambda e, p=pkg: self.confirm_uninstall(p)),
                    ], tight=True)))
            status.append(ft.ExpansionTile(title=ft.Text(f"Installed games ({len(rows)})"), controls=rows, expanded=True))
        saved = saved_targets()
        return ft.Column([
            ft.Text("Steam Frame", size=26, weight=ft.FontWeight.BOLD),
            *status,
            ft.Text("Find a Frame on your network", weight=ft.FontWeight.BOLD),
            ft.Row([ft.Button("Discover", icon=ft.Icons.WIFI_FIND, on_click=do_discover)]), found,
            ft.Text("First-time setup (pairing)", weight=ft.FontWeight.BOLD),
            ft.Button("Show setup command", icon=ft.Icons.QR_CODE_2, on_click=do_pair), pair_box,
            ft.Text("Connect manually", weight=ft.FontWeight.BOLD),
            ft.Row([addr, pw, ft.Button("Connect", on_click=lambda e: self.connect_manual(addr.value, pw.value))]),
            ft.Text("Remembered: " + (", ".join(f"{t.label} ({t.host})" for t in saved) or "none"), size=12),
        ], scroll=ft.ScrollMode.AUTO, expand=True, spacing=10)

    def connect_manual(self, address: str, password: str | None):
        from ..frame.connection import parse_target

        if not (address or "").strip():
            self.toast("Enter the Frame's address (e.g. steamos@frame.local or its IP), or use Discover.", error=True)
            return
        try:
            target = parse_target(address)
        except ValueError as exc:
            self.toast(str(exc), error=True)
            return
        self.connect(target, password or None)

    def copy(self, text: str):
        try:
            self.page.run_task(ft.Clipboard().set, text)
            self.toast("Copied")
        except Exception:
            self.toast("Copy failed; select the text instead", error=True)

    def _quick_test(self, package: str):
        from ..core.events import printing_reporter

        res, _ = self.target.launch_test(package, printing_reporter(False))
        self.toast(f"{package}: {res.state} ({res.verdict}); furthest: {res.milestone}")

    def settings_dialog(self, package: str):
        from ..patches.settings import SETTINGS

        fields = {key: ft.TextField(label=title, value="", hint_text=str(default), width=200, dense=True)
                  for key, kind, default, title, _ in SETTINGS}

        def save(e):
            vals = {k: f.value for k, f in fields.items() if f.value.strip()}
            self.run_bg(lambda: self.toast(f"Saved: {self.target.set_settings(package, vals)['settings']}"))
            self.page.pop_dialog()
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text(f"Adapter settings: {package}"),
            content=ft.Column([ft.Text("Only filled-in values change. Restart the game afterwards.", size=12),
                               ft.Row(list(fields.values()), wrap=True, width=640)], tight=True, scroll=ft.ScrollMode.AUTO),
            actions=[ft.TextButton("Cancel", on_click=lambda e: self.page.pop_dialog()), ft.FilledButton("Save", on_click=save)]))

    def confirm_uninstall(self, package: str):
        def go(e):
            self.page.pop_dialog()
            self.run_bg(lambda: (self.target.uninstall(package, keep_data=True), self.connect(self.target.target, quiet=True),
                                 self.toast(f"Uninstalled {package} (saves kept)")))
        self.page.show_dialog(ft.AlertDialog(
            title=ft.Text("Uninstall?"), content=ft.Text(f"Removes {package}'s game files from the Frame. Saves are kept. "
                                                          "The Steam entry disappears after the next Steam restart."),
            actions=[ft.TextButton("Cancel", on_click=lambda e: self.page.pop_dialog()),
                     ft.FilledButton("Uninstall", on_click=go)]))

    # ------------------------------------------------------------------ Tools
    def tools_view(self):
        from ..core.paths import user_data_dir
        from ..tools import toolchain

        rows = ft.Column()

        def refresh(check_latest=False):
            rows.controls = [ft.ListTile(
                leading=ft.Icon(ft.Icons.CHECK_CIRCLE if s.installed else ft.Icons.DOWNLOAD,
                                color=ft.Colors.GREEN_400 if s.installed else ft.Colors.AMBER_400),
                title=ft.Text(f"{s.name} {s.version or ''}"),
                subtitle=ft.Text((f"latest: {s.latest} · " if s.latest else "") + str(s.path or "not installed"), size=12))
                for s in toolchain.status(check_latest)]
            self.page.update()

        def install(update):
            def work():
                self.toast("Downloading tools…")
                toolchain.ensure_all(update=update)
                from ..patches import overport as op
                from ..tools import overport as ov

                op.refresh(ov.list_patches)
                refresh(True)
                self.toast("Toolchain ready.")
            self.run_bg(work)

        refresh()
        return ft.Column([
            ft.Text("Tools", size=26, weight=ft.FontWeight.BOLD),
            ft.Text("FramePort manages its own Java runtime, the overport CLI and apksigner (latest versions, verified "
                    "downloads). Nothing is installed system-wide.", color=ft.Colors.ON_SURFACE_VARIANT),
            rows,
            ft.Row([ft.FilledButton("Install missing", icon=ft.Icons.DOWNLOAD, on_click=lambda e: install(False)),
                    ft.OutlinedButton("Check for updates", icon=ft.Icons.UPDATE, on_click=lambda e: install(True))]),
            ft.Divider(),
            ft.Text(f"Data folder: {user_data_dir()}", size=12, selectable=True),
            ft.Text(f"Catalog: {len(catalog.load())} known-good recipes (bundled + remote + yours)", size=12),
        ], expand=True, scroll=ft.ScrollMode.AUTO, spacing=10)


def main(argv=None):
    ft.run(lambda page: FramePortApp(page))


if __name__ == "__main__":
    main()
